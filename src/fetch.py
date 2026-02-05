"""
Content fetching with browser, vision AI, and LLM processing.

Web page extraction strategy:
1. Browser renders page (JavaScript executes)
2. Extract FULL accessibility tree text (not truncated)
3. Use markdownify/readability to convert to clean markdown
4. Chunk the same way as PDFs (heading-aware)
5. Cache and serve with TOC

This matches the PDF/doc pipeline exactly.
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


async def get_webpage(url: str, section: int = None) -> str:
    """
    Fetch webpage content with full extraction and smart size handling.
    
    Strategy:
    1. Browse with Playwright (JS renders)
    2. Extract full accessibility tree text
    3. Convert to clean markdown
    4. Cache and return content or TOC
    """
    # Check cache first
    cached = documents.get(url)
    
    if cached is not None:
        # If section specified, return that chunk
        if section is not None:
            return documents.format_chunk(cached, section)
        
        # Check size - return full or TOC
        content = cached.full_text()
        estimated_tokens = len(content) // config.CHARS_PER_TOKEN
        
        if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
            return content
        
        return documents.format_toc(cached)
    
    # Direct fetch for raw files
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
    
    # Full browser extraction for web pages
    result = await browse(url)
    
    if result.get("error"):
        # Browser failed - fallback to direct HTTP fetch
        print(f"Browser failed: {result['error']}, falling back to HTTP")
        text = await _fetch_html_as_text(url)
        if not text:
            return f"Error: Browser failed ({result['error']}) and HTTP fallback also failed."
    else:
        text = result.get("content", "")
    
    if not text:
        return "Error: No content retrieved from page"
    
    # Convert to markdown and cache
    markdown = _clean_webpage_content(text, url)
    documents.save(url, _extract_title(markdown), markdown)
    cached = documents.get(url)
    
    if section is not None:
        return documents.format_chunk(cached, section)
    
    content = cached.full_text()
    estimated_tokens = len(content) // config.CHARS_PER_TOKEN
    if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
        return content
    return documents.format_toc(cached)


async def browse(url: str) -> dict:
    """
    Browse URL and extract full content using Playwright MCP.
    
    Uses browser_snapshot which provides the accessibility tree -
    this is better than screenshots because it gives us structured text
    that can be extracted completely (not truncated).
    """
    # Get project root (parent of src/)
    project_root = Path(__file__).parent.parent
    
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    
    # Use locally installed playwright-mcp instead of npx
    # On Windows: node_modules/.bin/playwright-mcp.cmd
    # On Linux/Mac: node_modules/.bin/playwright-mcp
    if os.name == 'nt':  # Windows
        cmd = str(project_root / "node_modules" / ".bin" / "playwright-mcp.cmd")
    else:
        cmd = str(project_root / "node_modules" / ".bin" / "playwright-mcp")
    
    args = ["--extension"]
    
    if config.PLAYWRIGHT_MCP_TOKEN:
        env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = config.PLAYWRIGHT_MCP_TOKEN
    
    try:
        server = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # Navigate and wait for JS
                await session.call_tool("browser_navigate", {"url": url})
                await asyncio.sleep(2)
                
                # Get full accessibility snapshot (structured text)
                snapshot = await session.call_tool("browser_snapshot", {})
                
                # Extract ALL text from snapshot (not truncated)
                full_text = ""
                for item in snapshot.content:
                    if hasattr(item, 'text') and item.text:
                        full_text += item.text + "\n"
                
                return {"content": full_text, "url": url}
    except Exception as e:
        return {"error": str(e), "content": "", "url": url}


def _clean_webpage_content(raw_text: str, url: str) -> str:
    """
    Convert raw accessibility tree text to clean markdown.
    
    The accessibility tree contains structural information that we can
    use to reconstruct headings, lists, etc.
    """
    lines = raw_text.split('\n')
    markdown_lines = []
    prev_line = ""
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Skip common junk
        if any(junk in line.lower() for junk in [
            'accept cookies', 'cookie consent', 'privacy policy',
            'terms of service', 'sign up', 'log in', 'subscribe',
            'advertisement', 'sponsored', 'close menu'
        ]):
            continue
        
        # Heuristic: short all-caps lines might be headings
        if line.isupper() and len(line) < 100 and len(line) > 3:
            markdown_lines.append(f"## {line.title()}")
        # Longer lines might be paragraphs
        elif len(line) > 50:
            if prev_line and len(prev_line) > 50:
                markdown_lines.append("")  # Paragraph break
            markdown_lines.append(line)
        # Short lines might be list items or nav
        elif line.startswith(('•', '-', '*', '1.', '2.')):
            markdown_lines.append(line)
        else:
            # Treat as potential heading or keep as-is
            if len(line) < 60 and not prev_line:
                markdown_lines.append(f"### {line}")
            else:
                markdown_lines.append(line)
        
        prev_line = line
    
    # Add source
    markdown = '\n'.join(markdown_lines)
    return f"Source: {url}\n\n{markdown}"


async def describe_image(url: str, prompt: str = "Describe this image in detail.") -> str:
    """Describe image using Vision AI."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            img_b64 = base64.b64encode(resp.content).decode()
        
        messages = [
            {"role": "system", "content": "You are a vision analysis assistant. Describe images accurately, noting key details, text content, diagrams, charts, and visual elements."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]}
        ]
        
        result = await _call_llm(messages, max_tokens=config.VLM_MAX_TOKENS, use_vision_params=True)
        return _safe_text(result or "Could not describe image.")
    except Exception as e:
        return f"Image error: {e}"


async def _call_llm(
    messages: list, 
    model: str = "local",
    max_tokens: int = None,
    use_vision_params: bool = False
) -> str | None:
    """Call LM Studio API."""
    if use_vision_params:
        params = {
            "model": model,
            "messages": messages,
            "temperature": config.VLM_TEMPERATURE,
            "top_p": config.VLM_TOP_P,
            "top_k": config.VLM_TOP_K,
            "repetition_penalty": config.VLM_REPETITION_PENALTY,
            "presence_penalty": config.VLM_PRESENCE_PENALTY,
            "max_tokens": max_tokens or config.VLM_MAX_TOKENS,
            "stream": False
        }
    else:
        params = {
            "model": model,
            "messages": messages,
            "temperature": config.LLM_TEMPERATURE,
            "top_p": config.LLM_TOP_P,
            "top_k": config.LLM_TOP_K,
            "repetition_penalty": config.LLM_REPETITION_PENALTY,
            "presence_penalty": config.LLM_PRESENCE_PENALTY,
            "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
            "stream": False
        }
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_LLM) as client:
            resp = await client.post(f"{config.LMSTUDIO_URL}/chat/completions", json=params)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"LLM error: {e}")
        return None


async def _fetch_text_direct(url: str) -> str | None:
    """Fetch text directly (for markdown, raw files)."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url)
            
            # GitHub main->master fallback
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
    """Fetch HTML and convert to plain text (fallback when browser fails)."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_BROWSER, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
            })
            resp.raise_for_status()
            html = resp.text
            
            # Simple HTML to text conversion
            # Remove script and style tags
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
            # Remove tags but preserve newlines for block elements
            html = re.sub(r'<(br|p|div|h[1-6]|li|tr)[^>]*>', '\n', html, flags=re.IGNORECASE)
            html = re.sub(r'<[^>]+>', '', html)
            # Unescape entities
            html = html.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            html = html.replace('&quot;', '"').replace('&#39;', "'")
            # Collapse whitespace
            lines = [line.strip() for line in html.split('\n') if line.strip()]
            return '\n'.join(lines)
    except Exception as e:
        print(f"HTML fetch failed: {e}")
        return None


def _safe_text(text: str) -> str:
    """Remove problematic Unicode."""
    replacements = {
        '\U0001f525': '[fire]', '\U0001f680': '[rocket]', '\u2217': '*',
        '\u2013': '-', '\u2014': '--', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2705': '[check]',
    }
    for char, repl in replacements.items():
        text = text.replace(char, repl)
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
