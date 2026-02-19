"""
Content fetching with deployment-flexible extraction.

Uses a strategy pattern with three extraction tiers:
1. Vision: Chrome + Vision LLM (best quality, local dev)
2. Docker Playwright: Headless Chromium in Docker (cloud-friendly)
3. MarkItDown: Pure Python HTTP fetch (fastest, works anywhere)

Automatically detects available services and degrades gracefully.
"""

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config
import documents
from utils import Result, safe_text, extract_title, build_llm_params, html_to_markdown


# =============================================================================
# EXTRACTOR PROTOCOL
# =============================================================================

@runtime_checkable
class Extractor(Protocol):
    """Protocol for web content extractors."""
    name: str

    async def is_available(self) -> bool: ...
    async def extract(self, url: str) -> Result: ...


# =============================================================================
# VISION EXTRACTOR (Chrome + Vision LLM)
# =============================================================================

WEB_VISION_PROMPT = """Extract the main article/content from this webpage screenshot as clean, structured markdown.

Instructions:
1. Focus on the MAIN CONTENT (article, documentation, post)
2. IGNORE: navigation menus, sidebars, ads, footers, cookie banners
3. Preserve structure: headings (# ## ###), lists (- *), code blocks (```)
4. Extract all meaningful text content
5. If there's a code example, preserve it exactly
6. If there's a table, format it as markdown table

Output format:
# [Title]

[Main content in markdown format...]

If the page appears to be a login page, error page, or CAPTCHA, respond with: "ERROR: [reason]"
"""


class VisionExtractor:
    """Extract web content via Chrome screenshot + Vision LLM."""

    name = "vision"
    _available: bool | None = None
    _last_check: float = 0
    _RECHECK_INTERVAL = 300  # Re-check every 5 minutes

    async def is_available(self) -> bool:
        now = time.time()
        if self._available is not None and (now - self._last_check) < self._RECHECK_INTERVAL:
            return self._available
        self._available = await self._check()
        self._last_check = now
        return self._available

    async def _check(self) -> bool:
        if not config.VISION_API_URL:
            return False

        # Check Playwright MCP binary exists
        try:
            cmd = self._get_playwright_cmd()
            if not Path(cmd).exists():
                print("[Setup] Playwright MCP not found")
                return False
        except Exception:
            return False

        # Check Vision API has a vision model
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{config.VISION_API_URL}/models")
                if resp.status_code != 200:
                    return False
                models = resp.json().get("data", [])
                vision_models = [
                    m for m in models
                    if any(v in m.get("id", "").lower() for v in ["vl", "vision", "qwen3-vl"])
                ]
                if not vision_models:
                    return False
                print(f"[Setup] Vision available: Chrome + {vision_models[0]['id']}")
                return True
        except Exception:
            return False

    async def extract(self, url: str) -> Result:
        try:
            markdown = await self._extract_with_vision(url)
            if markdown and len(markdown) > 200:
                return Result.ok(markdown)
            return Result.fail("Vision returned insufficient content")
        except Exception as e:
            return Result.fail(f"Vision: {e}")

    async def _extract_with_vision(self, url: str) -> str | None:
        """Extract webpage content using Chrome + Vision LLM."""
        cmd = self._get_playwright_cmd()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        args = ["--extension"]
        if config.PLAYWRIGHT_MCP_TOKEN:
            env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = config.PLAYWRIGHT_MCP_TOKEN

        server = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                print(f"[Vision] Navigating to {url[:60]}...")
                await session.call_tool("browser_navigate", {"url": url})

                wait_time = 5 if config.WEB_WAIT_FOR_JS else 1
                await asyncio.sleep(wait_time)

                if config.WEB_WAIT_FOR_JS:
                    try:
                        await session.call_tool("browser_scroll", {"direction": "down", "amount": 500})
                        await asyncio.sleep(1)
                    except Exception:
                        pass  # Scroll may fail on some pages

                print("[Vision] Taking screenshot...")
                screenshot_result = await session.call_tool("browser_take_screenshot", {})
                screenshot_b64 = self._extract_screenshot_data(screenshot_result)

                if not screenshot_b64:
                    print("[Vision] No screenshot data found")
                    return None

                messages = [
                    {"role": "system", "content": "You are a web content extraction expert."},
                    {"role": "user", "content": [
                        {"type": "text", "text": WEB_VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                    ]},
                ]
                return await _call_llm(messages, max_tokens=8000)

    @staticmethod
    def _extract_screenshot_data(screenshot_result) -> str | None:
        """Extract base64 image data from Playwright MCP screenshot result."""
        for item in screenshot_result.content:
            if hasattr(item, "data") and item.data:
                if isinstance(item.data, str):
                    return item.data
                return base64.b64encode(item.data).decode()
            if hasattr(item, "text") and item.text:
                text = item.text
                if text.startswith("data:image"):
                    return text.split(",")[1] if "," in text else text
                if text.startswith("http"):
                    resp = httpx.get(text)
                    return base64.b64encode(resp.content).decode()
        return None

    @staticmethod
    def _get_playwright_cmd() -> str:
        project_root = Path(__file__).parent.parent
        if os.name == "nt":
            return str(project_root / "node_modules" / ".bin" / "playwright-mcp.cmd")
        return str(project_root / "node_modules" / ".bin" / "playwright-mcp")


# =============================================================================
# DOCKER PLAYWRIGHT EXTRACTOR
# =============================================================================

class DockerPlaywrightExtractor:
    """Extract web content via Docker Playwright + html2text."""

    name = "docker_playwright"
    _available: bool | None = None
    _last_check: float = 0
    _RECHECK_INTERVAL = 300

    async def is_available(self) -> bool:
        now = time.time()
        if self._available is not None and (now - self._last_check) < self._RECHECK_INTERVAL:
            return self._available
        try:
            from docker_playwright import is_available
            self._available = await is_available()
        except ImportError:
            self._available = False
        self._last_check = now
        return self._available

    async def extract(self, url: str) -> Result:
        try:
            from docker_playwright import extract_webpage
            print(f"[Web] Docker Playwright for: {url[:60]}...")
            html = await extract_webpage(url, wait_for_js=config.WEB_WAIT_FOR_JS)
            if not html or len(html) < 500:
                return Result.fail("Docker Playwright returned insufficient content")
            markdown = html_to_markdown(html, url)
            print(f"[Web] OK: Docker Playwright: {len(markdown)} chars")
            return Result.ok(markdown)
        except Exception as e:
            return Result.fail(f"Docker Playwright: {e}")


# =============================================================================
# MARKITDOWN EXTRACTOR
# =============================================================================

class MarkItDownExtractor:
    """Extract web content via MarkItDown (pure Python, no browser)."""

    name = "markitdown"

    async def is_available(self) -> bool:
        try:
            from markitdown_client import MARKITDOWN_AVAILABLE
            return MARKITDOWN_AVAILABLE
        except ImportError:
            return False

    async def extract(self, url: str) -> Result:
        try:
            from markitdown_client import convert_file as md_convert_file
            print(f"[Web] MarkItDown for: {url[:60]}...")
            result = await asyncio.to_thread(md_convert_file, url, use_vision=False)
            if result.get("success") and len(result.get("text_content", "")) > 500:
                print(f"[Web] OK: MarkItDown: {len(result['text_content'])} chars")
                return Result.ok(result["text_content"])
            error = result.get("error", "Content too short or extraction failed")
            return Result.fail(f"MarkItDown: {error}")
        except Exception as e:
            return Result.fail(f"MarkItDown: {e}")


# =============================================================================
# EXTRACTOR REGISTRY
# =============================================================================

EXTRACTORS: list = [
    VisionExtractor(),
    DockerPlaywrightExtractor(),
    MarkItDownExtractor(),
]

EXTRACTOR_MAP: dict[str, object] = {e.name: e for e in EXTRACTORS}


# =============================================================================
# PUBLIC API
# =============================================================================

async def get_webpage(url: str, section: int = None, force_method: str = None) -> str:
    """Fetch webpage with deployment-flexible extraction.

    Args:
        url: URL to fetch
        section: Optional section number for large documents
        force_method: Force specific method ("vision", "docker_playwright", "markitdown")
    """
    # Check cache first
    cached = documents.get(url)
    if cached is not None:
        return _format_cached(cached, section)

    # Raw files: direct fetch (markdown, raw GitHub)
    if url.endswith(".md") or url.startswith("https://raw.githubusercontent.com"):
        text = await _fetch_text_direct(url)
        if text:
            documents.save(url, extract_title(text), text)
            cached = documents.get(url)
            return _format_cached(cached, section)

    # Determine extraction order
    method = force_method or config.WEB_EXTRACTION_METHOD
    if method != "auto" and method in EXTRACTOR_MAP:
        extractors_to_try = [EXTRACTOR_MAP[method]]
    else:
        extractors_to_try = EXTRACTORS

    # Try extractors in order
    errors = []
    for extractor in extractors_to_try:
        if not await extractor.is_available():
            errors.append(f"{extractor.name}: Not available")
            continue
        try:
            result = await extractor.extract(url)
            if result.success and len(result.content) > 200:
                markdown = f"<!-- Extracted via: {extractor.name} -->\n\n{result.content}"
                documents.save(url, extract_title(markdown), markdown, extractor.name)
                cached = documents.get(url)
                return safe_text(_format_cached(cached, section))
            if not result.success:
                errors.append(f"{extractor.name}: {result.error}")
        except Exception as e:
            errors.append(f"{extractor.name}: {e}")

    error_msg = "; ".join(errors) if errors else "All methods failed"
    return safe_text(f"Error: Failed to extract content from {url}. {error_msg}")


def _format_cached(cached, section: int = None) -> str:
    """Format cached document for return (full text, TOC, or specific section)."""
    if section is not None:
        return safe_text(documents.format_chunk(cached, section))
    content = cached.full_text()
    estimated_tokens = len(content) // config.CHARS_PER_TOKEN
    if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
        return safe_text(content)
    return safe_text(documents.format_toc(cached))


async def describe_image(url: str, prompt: str = "Describe this image in detail.") -> str:
    """Describe image using Vision AI."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            img_b64 = base64.b64encode(resp.content).decode()

        messages = [
            {"role": "system", "content": "You are a vision analysis assistant."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]},
        ]

        result = await _call_llm(messages)
        return safe_text(result or "Could not describe image.")
    except Exception as e:
        return f"Image error: {e}"


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

async def _call_llm(messages: list, max_tokens: int = None) -> str | None:
    """Call vision API with configured sampling parameters."""
    if not config.VISION_API_URL:
        return None

    params = build_llm_params(messages, max_tokens)

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_LLM) as client:
            resp = await client.post(f"{config.VISION_API_URL}/chat/completions", json=params)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"LLM error: {e}")
        return None


async def _fetch_text_direct(url: str) -> str | None:
    """Fetch raw text content directly (for .md files, raw GitHub URLs).

    For GitHub README URLs, tries common variants when the default 404s:
    README.md, README.rst, README.txt, README (both main and master branches).
    """
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text

            # GitHub README fallback: try common filenames and branches
            if resp.status_code == 404 and "raw.githubusercontent.com" in url and "README" in url:
                base = url.rsplit("/README", 1)[0]
                variants = ["README.md", "README.rst", "README.txt", "README"]
                branches = set()
                if "/main/" in url:
                    branches = {"main", "master"}
                elif "/master/" in url:
                    branches = {"master", "main"}

                for branch in branches:
                    for variant in variants:
                        alt = base.replace("/main/", f"/{branch}/").replace("/master/", f"/{branch}/")
                        alt = f"{alt}/{variant}"
                        resp = await client.get(alt)
                        if resp.status_code == 200:
                            print(f"[Fetch] Found README at: {alt}")
                            return resp.text

            resp.raise_for_status()
            return resp.text
    except Exception as e:
        print(f"Direct fetch failed: {e}")
        return None
