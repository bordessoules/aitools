"""
Content fetching with deployment-flexible extraction.

Architecture: two separate concerns —

**HTML Sources** (escalating bot resistance):
1. Docling direct HTTP — fastest, browser User-Agent header
2. Docker Playwright headless — JS rendering, ad blocking, cookie handling
3. Docker Playwright headed (Xvfb) — higher bot resistance
4. Local Chrome via Playwright MCP — maximum bot resistance (real browser)

**Conversion** (constant pipeline):
  HTML → Docling → markdown draft → LLM tail-trim (11 tokens)

Only the HTML source changes per tier. Conversion always uses Docling
(preserves links, formatting, accents, handles images via VLM pipeline)
plus a cheap LLM tail-trim to remove nav/footer junk.

Fallback when Docling is unavailable:
  Docker Playwright + MarkItDown (no LLM needed)
"""

import asyncio
import base64
import os
import re
import tempfile
from pathlib import Path

import httpx

from . import config
from . import documents
from .llm import call_llm
from .logger import get_logger
from .utils import extract_title, safe_text

log = get_logger("fetch")

# Import MarkItDown for deployment flexibility
try:
    from .markitdown_client import convert_file as md_convert_file
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

# Feature flags (set at startup)
MARKITDOWN_WEB_AVAILABLE = None
DOCKER_PLAYWRIGHT_AVAILABLE = None
DOCLING_AVAILABLE = None
LOCAL_CHROME_AVAILABLE = None

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TAIL_TRIM_PROMPT = """You see the LAST SECTION of a markdown document extracted from a webpage.
Line numbers are shown (L280:, L281:, etc.).

The document contains an article followed by non-article content like:
- Comment sections
- Related articles lists
- Navigation links
- Footer content
- "Leave a comment" forms
- Social media links

Find the LAST LINE that is still part of the main article.
Respond with ONLY a JSON object: {"last_article_line": 280}
"""

# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


async def check_local_chrome_available() -> bool:
    """Check if local Chrome via Playwright MCP extension is available.

    This is the maximum bot-resistance tier — uses a real local Chrome browser
    controlled via the Playwright MCP extension. Falls back if not installed.
    """
    global LOCAL_CHROME_AVAILABLE
    if LOCAL_CHROME_AVAILABLE is not None:
        return LOCAL_CHROME_AVAILABLE

    try:
        project_root = Path(__file__).parent.parent
        cmd = str(project_root / "node_modules" / ".bin" / "playwright-mcp.cmd") if os.name == 'nt' else str(project_root / "node_modules" / ".bin" / "playwright-mcp")
        if not Path(cmd).exists():
            log.info("Playwright MCP not found, local Chrome disabled")
            LOCAL_CHROME_AVAILABLE = False
            return False
        log.info("Local Chrome available via Playwright MCP")
        LOCAL_CHROME_AVAILABLE = True
        return True
    except Exception as e:
        log.warning("Local Chrome check failed: %s", e)
        LOCAL_CHROME_AVAILABLE = False
        return False


async def check_markitdown_web_available() -> bool:
    """Check if MarkItDown can handle web pages."""
    global MARKITDOWN_WEB_AVAILABLE
    if MARKITDOWN_WEB_AVAILABLE is not None:
        return MARKITDOWN_WEB_AVAILABLE
    MARKITDOWN_WEB_AVAILABLE = MARKITDOWN_AVAILABLE
    return MARKITDOWN_WEB_AVAILABLE


async def check_docker_playwright_available() -> bool:
    """Check if Docker Playwright is available."""
    global DOCKER_PLAYWRIGHT_AVAILABLE
    if DOCKER_PLAYWRIGHT_AVAILABLE is not None:
        return DOCKER_PLAYWRIGHT_AVAILABLE
    try:
        from .docker_playwright import is_available
        DOCKER_PLAYWRIGHT_AVAILABLE = await is_available()
        return DOCKER_PLAYWRIGHT_AVAILABLE
    except ImportError:
        DOCKER_PLAYWRIGHT_AVAILABLE = False
        return False


async def check_docling_available() -> bool:
    """Check if Docling is available for web extraction."""
    global DOCLING_AVAILABLE
    if DOCLING_AVAILABLE is not None:
        return DOCLING_AVAILABLE
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{docling_url}/health")
            DOCLING_AVAILABLE = resp.status_code == 200
    except Exception:
        DOCLING_AVAILABLE = False
    if DOCLING_AVAILABLE:
        log.info("Docling available at %s", docling_url)
    return DOCLING_AVAILABLE

# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------


def _return_cached(url: str, section: int | None) -> str | None:
    """Return cached document as full content, section chunk, or TOC.

    Returns None if the document is not in cache.
    """
    cached = documents.get(url)
    if cached is None:
        return None
    if section is not None:
        return safe_text(documents.format_chunk(cached, section))
    content = cached.full_text()
    estimated_tokens = len(content) // config.CHARS_PER_TOKEN
    if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
        return safe_text(content)
    return safe_text(documents.format_toc(cached))

# ---------------------------------------------------------------------------
# Extractor selection
# ---------------------------------------------------------------------------


def _get_extractors(
    method: str,
    has_docker_playwright: bool,
    has_markitdown: bool,
    has_docling: bool,
    has_local_chrome: bool,
) -> list[tuple[str, object, str | None]]:
    """Build ordered list of (name, extractor_coro, unavailable_reason) to try.

    Architecture: only the HTML source changes per tier, conversion is always
    Docling→markdown→tail-trim. When Docling is down, fall back to
    Playwright+MarkItDown (no LLM needed).

    Each entry is a tuple of:
      - name: display name for logging/metadata
      - extractor: async callable(url) -> str | None
      - unavailable_reason: error string if capability missing, None if available
    """
    all_extractors = {
        # Primary: Docling pipeline (internally escalates HTML sources)
        "docling": (
            "docling",
            _extract_with_docling,
            None if has_docling else "Docling: Not available",
        ),
        # Fallback: Playwright + MarkItDown (no LLM needed)
        "docker_playwright": (
            "docker_playwright",
            _extract_with_playwright,
            None if has_docker_playwright else "Docker Playwright: Not available",
        ),
        # Forced modes
        "local_chrome": (
            "local_chrome",
            _extract_with_local_chrome,
            None if (has_local_chrome and has_docling) else "Local Chrome or Docling: Not available",
        ),
        "markitdown": (
            "markitdown",
            _extract_with_markitdown,
            None if has_markitdown else "MarkItDown: Not available",
        ),
    }

    if method == "auto":
        # Docling pipeline (escalates HTML sources internally), then MarkItDown fallback
        return [
            all_extractors["docling"],
            all_extractors["docker_playwright"],
        ]

    if method in all_extractors:
        return [all_extractors[method]]

    # Unknown method — fall back to auto
    log.warning("Unknown extraction method '%s', using auto", method)
    return [all_extractors["docling"], all_extractors["docker_playwright"]]

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def get_webpage(url: str, section: int | None = None, force_method: str | None = None) -> str:
    """Fetch webpage with deployment-flexible extraction.

    Args:
        url: URL to fetch
        section: Optional section number for large documents
        force_method: Force specific method ("docling", "local_chrome",
                     "docker_playwright", "markitdown").
                     Overrides WEB_EXTRACTION_METHOD config.

    Auto mode (default):
    1. Docling pipeline — escalates HTML sources (direct → Docker PW → local Chrome)
       then Docling converts → LLM tail-trim. Best quality.
    2. Docker Playwright + MarkItDown — no-LLM fallback when Docling is down.
    """
    # Check cache first
    result = _return_cached(url, section)
    if result is not None:
        return result

    # Raw files: direct fetch
    if url.endswith('.md') or url.startswith('https://raw.githubusercontent.com'):
        text = await _fetch_text_direct(url)
        if text:
            documents.save(url, extract_title(text), text)
            result = _return_cached(url, section)
            if result is not None:
                return result

    # Check available extractors
    method = force_method or config.WEB_EXTRACTION_METHOD
    has_markitdown = await check_markitdown_web_available()
    has_docker_playwright = await check_docker_playwright_available()
    has_docling = await check_docling_available()
    has_local_chrome = await check_local_chrome_available()

    extractors = _get_extractors(method, has_docker_playwright, has_markitdown, has_docling, has_local_chrome)

    # Try extractors in order
    markdown = None
    extraction_method = None
    errors = []

    for name, extractor, unavailable_reason in extractors:
        if unavailable_reason:
            errors.append(unavailable_reason)
            continue
        try:
            log.info("Trying %s for: %s...", name, url[:60])
            result = await extractor(url)
            if result and len(result) > 200:
                markdown = result
                extraction_method = name
                log.info("OK: %s: %d chars", name, len(markdown))
                break
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.warning("%s failed, trying next...", name)

    if not markdown:
        error_msg = "; ".join(errors) if errors else "All methods failed"
        return safe_text(f"Error: Failed to extract content from {url}. {error_msg}")

    # Add extraction metadata and cache
    markdown = f"<!-- Extracted via: {extraction_method} -->\n\n{markdown}"
    documents.save(url, extract_title(markdown), markdown, extraction_method)
    return _return_cached(url, section) or safe_text(markdown)

# ---------------------------------------------------------------------------
# Docling pipeline: HTML source escalation + Docling convert + tail-trim
# ---------------------------------------------------------------------------


async def _extract_with_docling(url: str) -> str | None:
    """Extract webpage via Docling pipeline, then trim nav junk with a cheap LLM call.

    HTML source escalation (only the source changes, conversion is always Docling):
    1. Docling direct HTTP — fastest, browser User-Agent header
    2. Best available browser → Docling:
       - Local Chrome (if available) — real browser, best bot resistance
       - Docker Playwright headed/Xvfb (fallback) — good bot resistance

    Then the LLM reads only the last ~200 lines and identifies where the article ends.
    We slice at that line — no rewriting, just trimming (11 completion tokens).
    """
    # Source 1: Docling fetches the URL directly (fastest)
    md = await _docling_fetch_url(url)

    # Source 2: best available browser renders, Docling converts
    if not md:
        if await check_local_chrome_available():
            # Local Chrome is strictly better — skip Docker Playwright
            md = await _docling_convert_local_chrome(url)
        else:
            # No local Chrome — use Docker Playwright
            md = await _docling_convert_playwright(url)

    if not md:
        return None

    log.info("Docling draft: %d chars, %d lines", len(md), len(md.splitlines()))

    # Tail-trim to remove nav/footer junk
    trimmed = await _tail_trim(md)
    return trimmed


async def _docling_fetch_url(url: str) -> str | None:
    """Docling fetches and converts a URL directly (fastest path)."""
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            resp = await client.post(
                f"{docling_url}/v1/convert/source",
                json={
                    "sources": [{
                        "url": url,
                        "kind": "http",
                        "headers": {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        },
                    }],
                    "options": {
                        "to_formats": ["md"],
                        "image_export_mode": "placeholder",
                    },
                },
            )
            resp.raise_for_status()
    except Exception as e:
        log.warning("Docling direct fetch failed: %s", e)
        return None

    md = resp.json().get("document", {}).get("md_content", "")
    if md and len(md) >= 200:
        log.info("Docling direct: %d chars", len(md))
        return md

    log.warning("Docling direct returned too little content (%d chars)", len(md))
    return None


async def _docling_convert_html(html: str) -> str | None:
    """Send pre-rendered HTML to Docling for markdown conversion.

    Shared by all browser-based HTML sources (Docker Playwright, local Chrome).
    Docling converts HTML to markdown, preserving links, formatting, and images.
    """
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            resp = await client.post(
                f"{docling_url}/v1/convert/file",
                files={"files": ("page.html", html.encode("utf-8"), "text/html")},
                data={"options": '{"to_formats": ["md"], "image_export_mode": "placeholder"}'},
            )
            resp.raise_for_status()
    except Exception as e:
        log.warning("Docling HTML convert failed: %s", e)
        return None

    md = resp.json().get("document", {}).get("md_content", "")
    if md and len(md) >= 200:
        log.info("Docling HTML convert: %d chars", len(md))
        return md

    log.warning("Docling HTML convert returned too little (%d chars)", len(md))
    return None


async def _docling_convert_playwright(url: str) -> str | None:
    """Docker Playwright renders the page, then Docling converts the HTML.

    For sites that block direct HTTP but accept headless Chrome.
    """
    try:
        from .docker_playwright import extract_webpage, is_available
        if not await is_available():
            return None
    except ImportError:
        return None

    html = await extract_webpage(url, wait_for_js=config.WEB_WAIT_FOR_JS)
    if not html or len(html) < 500:
        return None

    log.info("Docker Playwright rendered %d chars, sending to Docling...", len(html))
    return await _docling_convert_html(html)


async def _tail_trim(markdown: str) -> str:
    """Trim navigation/footer junk from the end of a Docling markdown draft.

    Sends only the last ~200 lines to the LLM, which returns the line number
    where the article ends. Costs ~11 completion tokens.

    Falls back to the full markdown if LLM is unavailable.
    """
    import json

    lines = markdown.split('\n')

    # Short documents don't need trimming
    if len(lines) <= 50:
        return markdown

    # Send the tail to LLM
    tail_size = min(200, len(lines))
    tail_start = len(lines) - tail_size
    tail_text = '\n'.join(
        f"L{tail_start + i + 1}: {line}"
        for i, line in enumerate(lines[tail_start:])
    )

    if not config.VISION_API_URL:
        log.debug("Tail-trim: no VLM configured, returning full content")
        return markdown

    messages = [
        {"role": "system", "content": "You find article boundaries. Respond with JSON only."},
        {"role": "user", "content": TAIL_TRIM_PROMPT + "\n\nTAIL OF DOCUMENT:\n" + tail_text},
    ]

    try:
        result = await call_llm(messages, max_tokens=100)
        if not result:
            log.warning("Tail-trim: LLM returned nothing, keeping full content")
            return markdown

        # Parse JSON response
        json_match = re.search(r'\{[^}]+\}', result)
        if json_match:
            boundary = json.loads(json_match.group())
            end_line = boundary.get("last_article_line", len(lines))
        else:
            log.warning("Tail-trim: could not parse LLM response: %s", result[:200])
            return markdown

        # Sanity check: don't trim more than 60% of the document
        if end_line < len(lines) * 0.4:
            log.warning("Tail-trim: LLM wants to cut at L%d (too aggressive), keeping full", end_line)
            return markdown

        trimmed = '\n'.join(lines[:end_line])
        removed = len(lines) - end_line
        log.info("Tail-trim: cut at L%d, removed %d lines (%.0f%%)",
                 end_line, removed, removed / len(lines) * 100)
        return trimmed

    except Exception as e:
        log.warning("Tail-trim failed: %s, keeping full content", e)
        return markdown


# ---------------------------------------------------------------------------
# Local Chrome HTML source (via Playwright MCP extension)
# ---------------------------------------------------------------------------


async def _get_local_chrome_html(url: str) -> str | None:
    """Get page HTML from local Chrome via Playwright MCP extension.

    Uses a real local browser — maximum bot resistance. Returns raw HTML
    for Docling to convert, NOT a screenshot.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project_root = Path(__file__).parent.parent

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }

    cmd = str(project_root / "node_modules" / ".bin" / "playwright-mcp.cmd") if os.name == 'nt' else str(project_root / "node_modules" / ".bin" / "playwright-mcp")
    args = ["--extension"]

    if config.PLAYWRIGHT_MCP_TOKEN:
        env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = config.PLAYWRIGHT_MCP_TOKEN

    try:
        server = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Navigate
                log.info("Local Chrome navigating to %s...", url[:60])
                await session.call_tool("browser_navigate", {"url": url})

                # Wait for page to load
                wait_time = 5 if config.WEB_WAIT_FOR_JS else 1
                await asyncio.sleep(wait_time)

                # Scroll to trigger lazy loading
                if config.WEB_WAIT_FOR_JS:
                    try:
                        await session.call_tool("browser_scroll", {"direction": "down", "amount": 500})
                        await asyncio.sleep(1)
                    except Exception as e:
                        log.debug("Scroll failed: %s", e)

                # Get page HTML (not screenshot!)
                log.info("Local Chrome getting page content...")
                content_result = await session.call_tool("browser_snapshot", {})

                # Extract text content from MCP result
                html = None
                for item in content_result.content:
                    if hasattr(item, 'text') and item.text:
                        html = item.text
                        break

                if html and len(html) > 500:
                    log.info("Local Chrome: %d chars HTML", len(html))
                    return html

                log.warning("Local Chrome returned too little content")
                return None

    except Exception as e:
        log.error("Local Chrome error: %s", e)
        return None


async def _docling_convert_local_chrome(url: str) -> str | None:
    """Local Chrome renders the page, then Docling converts the HTML to markdown.

    For bot-protected sites that block both direct HTTP and Docker Playwright.
    """
    if not await check_local_chrome_available():
        return None

    html = await _get_local_chrome_html(url)
    if not html:
        return None

    log.info("Local Chrome rendered %d chars, sending to Docling...", len(html))
    return await _docling_convert_html(html)


async def _extract_with_local_chrome(url: str) -> str | None:
    """Standalone extractor: local Chrome → Docling → tail-trim.

    Used when force_method='local_chrome' to bypass the normal escalation.
    """
    md = await _docling_convert_local_chrome(url)
    if not md:
        return None
    log.info("Local Chrome + Docling draft: %d chars, %d lines", len(md), len(md.splitlines()))
    return await _tail_trim(md)

# ---------------------------------------------------------------------------
# Fallback: Docker Playwright + MarkItDown (when Docling is down, no LLM needed)
# ---------------------------------------------------------------------------


async def _extract_with_playwright(url: str) -> str | None:
    """Extract webpage via Docker Playwright + MarkItDown.

    This is the no-LLM fallback when Docling is unavailable.
    MarkItDown converts the rendered HTML to markdown.
    """
    from .docker_playwright import extract_webpage

    html = await extract_webpage(url, wait_for_js=config.WEB_WAIT_FOR_JS)
    if not html or len(html) < 500:
        return None

    # MarkItDown HTML conversion
    if MARKITDOWN_AVAILABLE:
        md_result = _markitdown_html(html, url)
        if md_result:
            return md_result

    return None

# ---------------------------------------------------------------------------
# MarkItDown HTML conversion (used by Playwright fallback)
# ---------------------------------------------------------------------------


def _markitdown_html(html: str, url: str) -> str | None:
    """Convert Playwright HTML to markdown using MarkItDown.

    Writes HTML to a temp file for MarkItDown to process.
    Returns None if conversion fails or output is too short.
    """
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
            f.write(html)
            tmp_path = f.name
        try:
            result = md_convert_file(tmp_path, use_vision=False)
            if result.get('success') and len(result.get('text_content', '')) > 200:
                return f"Source: {url}\n\n{result['text_content']}"
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        log.debug("MarkItDown HTML conversion failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# MarkItDown direct extraction (forced mode only)
# ---------------------------------------------------------------------------


async def _extract_with_markitdown(url: str) -> str | None:
    """Extract webpage directly with MarkItDown (no browser)."""
    result = await asyncio.to_thread(md_convert_file, url, use_vision=False)
    if result.get('success') and len(result.get('text_content', '')) > 500:
        return result['text_content']
    return None

# ---------------------------------------------------------------------------
# Image description
# ---------------------------------------------------------------------------


async def describe_image(url: str, prompt: str = "Describe this image in detail.") -> str:
    """Describe image using Vision AI."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
            })
            resp.raise_for_status()
            img_b64 = base64.b64encode(resp.content).decode()

        messages = [
            {"role": "system", "content": "You are a vision analysis assistant."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]}
        ]
        result = await call_llm(messages)
        return safe_text(result or "Could not describe image.")
    except Exception as e:
        return f"Image error: {e}"

# ---------------------------------------------------------------------------
# Direct text fetch (for .md / raw GitHub)
# ---------------------------------------------------------------------------


async def _fetch_text_direct(url: str) -> str | None:
    """Fetch raw text content directly (for markdown files, GitHub raw URLs)."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404 and 'main/README.md' in url:
                master_url = url.replace('/main/README.md', '/master/README.md')
                resp = await client.get(master_url)
                if resp.status_code == 200:
                    return resp.text
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        log.warning("Direct fetch failed: %s", e)
        return None
