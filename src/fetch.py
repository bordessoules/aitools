"""
Content fetching with deployment-flexible extraction.

Supports 3 deployment tiers:
1. Full: Chrome + Qwen vision (best quality, local dev)
2. Docker: Docker Playwright with auth support (cloud-friendly)
3. Minimal: MarkItDown only (fastest, works anywhere)

Automatically detects available services and degrades gracefully.
"""

import asyncio
import base64
import os
import re
from pathlib import Path
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config
import documents

# Import MarkItDown for deployment flexibility
try:
    from markitdown_client import convert_file as md_convert_file
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

# Feature flags (set at startup)
VISION_AVAILABLE = None
MARKITDOWN_WEB_AVAILABLE = None
DOCKER_PLAYWRIGHT_AVAILABLE = None

# Prompt for vision-based extraction
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


async def check_vision_available() -> bool:
    """Check if vision-based extraction is available (Chrome + Qwen)."""
    global VISION_AVAILABLE
    if VISION_AVAILABLE is not None:
        return VISION_AVAILABLE

    # Check 0: Vision API URL must be configured
    if not config.VISION_API_URL:
        print("[Setup] VISION_API_URL not configured, vision disabled")
        VISION_AVAILABLE = False
        return False

    # Check 1: Playwright MCP Chrome extension
    try:
        project_root = Path(__file__).parent.parent
        cmd = str(project_root / "node_modules" / ".bin" / "playwright-mcp.cmd") if os.name == 'nt' else str(project_root / "node_modules" / ".bin" / "playwright-mcp")

        if not Path(cmd).exists():
            print("[Setup] Playwright MCP not found")
            VISION_AVAILABLE = False
            return False
    except Exception as e:
        print(f"[Setup] Playwright check failed: {e}")
        VISION_AVAILABLE = False
        return False

    # Check 2: Vision API with vision model
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.VISION_API_URL}/models")
            if resp.status_code != 200:
                print("[Setup] Vision API not available")
                VISION_AVAILABLE = False
                return False
            
            data = resp.json()
            models = data.get("data", [])
            vision_models = [m for m in models if any(v in m.get("id", "").lower() for v in ["vl", "vision", "qwen3-vl"])]
            
            if not vision_models:
                print("[Setup] No vision models found in LM Studio")
                VISION_AVAILABLE = False
                return False
            
            print(f"[Setup] Vision available: Chrome + {vision_models[0]['id']}")
            VISION_AVAILABLE = True
            return True
            
    except Exception as e:
        print(f"[Setup] Vision LLM check failed: {e}")
        VISION_AVAILABLE = False
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
        from docker_playwright import is_available
        DOCKER_PLAYWRIGHT_AVAILABLE = await is_available()
        return DOCKER_PLAYWRIGHT_AVAILABLE
    except ImportError:
        DOCKER_PLAYWRIGHT_AVAILABLE = False
        return False


async def get_webpage(url: str, section: int = None, force_method: str = None) -> str:
    """
    Fetch webpage with deployment-flexible extraction.
    
    Args:
        url: URL to fetch
        section: Optional section number for large documents
        force_method: Force specific method ("vision", "docker_playwright", "markitdown")
                     Overrides WEB_EXTRACTION_METHOD config
    
    Extraction methods (in order of preference):
    1. Vision (Chrome + Qwen) - best quality, requires Chrome extension
    2. Docker Playwright - full browser in Docker, auth support
    3. MarkItDown - fast, works everywhere, good quality
    """
    # Check cache first
    cached = documents.get(url)
    if cached is not None:
        if section is not None:
            return documents.format_chunk(cached, section)
        
        content = cached.full_text()
        estimated_tokens = len(content) // config.CHARS_PER_TOKEN
        if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
            return content
        return documents.format_toc(cached)
    
    # Raw files: direct fetch
    if url.endswith('.md') or url.startswith('https://raw.githubusercontent.com'):
        text = await _fetch_text_direct(url)
        if text:
            documents.save(url, _extract_title(text), text)
            cached = documents.get(url)
            if section is not None:
                return documents.format_chunk(cached, section)
            content = cached.full_text()
            estimated_tokens = len(content) // config.CHARS_PER_TOKEN
            if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
                return content
            return documents.format_toc(cached)
    
    # Determine which method to use
    method = force_method or config.WEB_EXTRACTION_METHOD
    has_vision = await check_vision_available()
    has_markitdown = await check_markitdown_web_available()
    has_docker_playwright = await check_docker_playwright_available()
    
    markdown = None
    extraction_method = None
    errors = []
    
    # Method selection based on preference
    if method == "vision":
        if has_vision:
            try:
                print(f"[Web] Vision extraction for: {url[:60]}...")
                result = await _extract_with_vision(url)
                if result and len(result) > 200:
                    markdown = result
                    extraction_method = 'vision'
            except Exception as e:
                errors.append(f"Vision: {e}")
        else:
            errors.append("Vision: Chrome extension not available")
    
    elif method == "docker_playwright":
        if has_docker_playwright:
            try:
                print(f"[Web] Docker Playwright for: {url[:60]}...")
                from docker_playwright import extract_webpage
                html = await extract_webpage(url, wait_for_js=config.WEB_WAIT_FOR_JS)
                if html and len(html) > 500:
                    markdown = _html_to_markdown(html, url)
                    extraction_method = 'docker_playwright'
                    print(f"[Web] OK: Docker Playwright: {len(markdown)} chars")
            except Exception as e:
                errors.append(f"Docker Playwright: {e}")
        else:
            errors.append("Docker Playwright: Not available")
    
    elif method == "markitdown":
        if has_markitdown:
            try:
                print(f"[Web] MarkItDown for: {url[:60]}...")
                result = await asyncio.to_thread(md_convert_file, url, use_vision=False)
                if result.get('success') and len(result.get('text_content', '')) > 500:
                    markdown = result['text_content']
                    extraction_method = 'markitdown'
            except Exception as e:
                errors.append(f"MarkItDown: {e}")
        else:
            errors.append("MarkItDown: Not available")
    
    else:  # auto - try all methods in order
        # Tier 1: Vision extraction (best quality - local Chrome)
        if has_vision:
            try:
                print(f"[Web] Trying vision extraction for: {url[:60]}...")
                result = await _extract_with_vision(url)
                if result and len(result) > 200:
                    markdown = result
                    extraction_method = 'vision'
                    print(f"[Web] OK: Vision: {len(markdown)} chars")
            except Exception as e:
                errors.append(f"Vision: {e}")
                print(f"[Web] FAIL: Vision failed, trying next...")
        
        # Tier 2: Docker Playwright (Docker with auth support)
        if not markdown and has_docker_playwright:
            try:
                print(f"[Web] Trying Docker Playwright for: {url[:60]}...")
                from docker_playwright import extract_webpage
                html = await extract_webpage(url, wait_for_js=config.WEB_WAIT_FOR_JS)
                if html and len(html) > 500:
                    markdown = _html_to_markdown(html, url)
                    extraction_method = 'docker_playwright'
                    print(f"[Web] OK: Docker Playwright: {len(markdown)} chars")
            except Exception as e:
                errors.append(f"Docker Playwright: {e}")
                print(f"[Web] FAIL: Docker Playwright failed, trying next...")
        
        # Tier 3: MarkItDown (final fallback - works everywhere)
        if not markdown and has_markitdown:
            try:
                print(f"[Web] Trying MarkItDown for: {url[:60]}...")
                result = await asyncio.to_thread(md_convert_file, url, use_vision=False)
                if result.get('success') and len(result.get('text_content', '')) > 500:
                    markdown = result['text_content']
                    extraction_method = 'markitdown'
                    print(f"[Web] OK: MarkItDown: {len(markdown)} chars")
            except Exception as e:
                errors.append(f"MarkItDown: {e}")
    
    if not markdown:
        error_msg = "; ".join(errors) if errors else "All methods failed"
        return _safe_text(f"Error: Failed to extract content from {url}. {error_msg}")
    
    # Add extraction metadata
    markdown = f"<!-- Extracted via: {extraction_method} -->\n\n{markdown}"
    
    # Save to cache with backend tracking
    documents.save(url, _extract_title(markdown), markdown, extraction_method)
    cached = documents.get(url)
    
    if section is not None:
        return _safe_text(documents.format_chunk(cached, section))

    content = cached.full_text()
    estimated_tokens = len(content) // config.CHARS_PER_TOKEN
    if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
        return _safe_text(content)
    return _safe_text(documents.format_toc(cached))


async def _extract_with_vision(url: str) -> str | None:
    """Extract webpage content using Chrome + Qwen3-VL."""
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
                print(f"[Vision] Navigating to {url[:60]}...")
                await session.call_tool("browser_navigate", {"url": url})
                
                # Wait for page to load (configurable)
                wait_time = 5 if config.WEB_WAIT_FOR_JS else 1
                await asyncio.sleep(wait_time)
                
                # Optional: Scroll to load lazy content
                if config.WEB_WAIT_FOR_JS:
                    try:
                        await session.call_tool("browser_scroll", {"direction": "down", "amount": 500})
                        await asyncio.sleep(1)
                    except:
                        pass  # Scroll may fail on some pages
                
                # Take screenshot
                print(f"[Vision] Taking screenshot...")
                screenshot_result = await session.call_tool("browser_take_screenshot", {})
                
                # Extract image data from result
                screenshot_b64 = None
                for item in screenshot_result.content:
                    # Handle ImageContent (new format) - data is already base64 string
                    if hasattr(item, 'data') and item.data:
                        if isinstance(item.data, str):
                            screenshot_b64 = item.data
                        else:
                            screenshot_b64 = base64.b64encode(item.data).decode()
                        break
                    # Handle TextContent with data URL
                    if hasattr(item, 'text') and item.text:
                        text = item.text
                        if text.startswith('data:image'):
                            screenshot_b64 = text.split(',')[1] if ',' in text else text
                            break
                        elif text.startswith('http'):
                            async with httpx.AsyncClient() as client:
                                resp = await client.get(text)
                                screenshot_b64 = base64.b64encode(resp.content).decode()
                                break
                
                if not screenshot_b64:
                    print("[Vision] No screenshot data found")
                    return None
                
                # Send to Qwen3-VL
                messages = [
                    {"role": "system", "content": "You are a web content extraction expert."},
                    {"role": "user", "content": [
                        {"type": "text", "text": WEB_VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}
                    ]}
                ]

                # Use configured sampling parameters
                return await _call_llm(messages, max_tokens=8000)
                    
    except Exception as e:
        print(f"[Vision Extract Error] {e}")
        return None


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
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]}
        ]

        result = await _call_llm(messages)
        return _safe_text(result or "Could not describe image.")
    except Exception as e:
        return f"Image error: {e}"


def _build_llm_params(messages: list, max_tokens: int = None) -> dict:
    """Build vision API params with explicit sampling from config."""
    params = {
        "model": config.VISION_MODEL,  # Vision model (e.g., qwen3-vl-4b, gpt-4-vision)
        "messages": messages,
        "stream": False
    }

    # Add all configured sampling parameters (override wrapper defaults)
    if config.VLM_TEMPERATURE is not None:
        params["temperature"] = config.VLM_TEMPERATURE
    if config.VLM_TOP_P is not None:
        params["top_p"] = config.VLM_TOP_P
    if config.VLM_TOP_K is not None:
        params["top_k"] = config.VLM_TOP_K
    if config.VLM_PRESENCE_PENALTY is not None:
        params["presence_penalty"] = config.VLM_PRESENCE_PENALTY
    if config.VLM_REPETITION_PENALTY is not None:
        params["repetition_penalty"] = config.VLM_REPETITION_PENALTY

    # Max tokens
    if max_tokens:
        params["max_tokens"] = max_tokens
    elif config.VLM_MAX_TOKENS is not None:
        params["max_tokens"] = config.VLM_MAX_TOKENS

    return params


async def _call_llm(messages: list, max_tokens: int = None) -> str | None:
    """Call vision API with configured sampling parameters."""
    if not config.VISION_API_URL:
        print("Vision API error: VISION_API_URL not configured")
        return None

    params = _build_llm_params(messages, max_tokens)

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
    """Fetch text directly."""
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
        print(f"Direct fetch failed: {e}")
        return None


async def _fetch_html_as_text(url: str) -> str | None:
    """Fetch HTML and convert to text."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
            })
            resp.raise_for_status()
            html = resp.text
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
            html = re.sub(r'<(br|p|div|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'<[^>]+>', '', html)
            html = html.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            html = html.replace('&quot;', '"').replace('&#39;', "'")
            lines = [line.strip() for line in html.split('\n') if line.strip()]
            return '\n'.join(lines)
    except Exception as e:
        print(f"HTML fetch failed: {e}")
        return None


def _html_to_markdown(html: str, url: str) -> str:
    """Convert HTML to markdown (fallback when MarkItDown not available)."""
    import html as html_module

    # Simple HTML to text conversion
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert common tags
    text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.IGNORECASE)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.IGNORECASE)
    text = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.IGNORECASE)
    
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode HTML entities
    text = html_module.unescape(text)
    
    # Clean up
    lines = [line.strip() for line in text.split('\n')]
    lines = [line for line in lines if line]
    
    return f"Source: {url}\n\n" + '\n'.join(lines)


def _safe_text(text: str) -> str:
    """Remove problematic Unicode."""
    return text.encode('ascii', 'ignore').decode('ascii')


def _extract_title(text: str) -> str:
    """Extract title from content."""
    match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines[:3]:
        if not line.startswith('<') and len(line) < 100:
            return line
    return "Untitled"
