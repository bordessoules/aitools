"""URL routing, search, and URL classification."""

import re
from urllib.parse import urlparse
from pathlib import Path
import httpx

# Import all configuration from centralized config module
from config import (
    SEARXNG_URL,
    TIMEOUT_SEARCH,
    IMAGE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    URL_TRANSFORMS,
    GITHUB_PATTERNS,
)


def normalize_url(url: str) -> str:
    """Transform URLs to direct content URLs (e.g., arxiv abs -> PDF, GitHub -> raw)."""
    # Try academic transforms first
    for pattern, replacement in URL_TRANSFORMS:
        if re.search(pattern, url, re.IGNORECASE):
            return re.sub(pattern, replacement, url, flags=re.IGNORECASE)
    
    # Try GitHub transforms (README files and blob URLs)
    for pattern, replacement in GITHUB_PATTERNS:
        if re.search(pattern, url):
            # Try main branch first, then master
            transformed = re.sub(pattern, replacement, url)
            if 'main/' in transformed or 'master/' in transformed:
                return transformed
            # For blob URLs, replacement already includes branch
            return transformed
    
    return url


def classify(url: str) -> str:
    """Classify URL as 'image', 'document', or 'webpage' based on extension and patterns."""
    path = urlparse(url).path.split("?")[0]
    ext = Path(path).suffix.lower()
    
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    # PDF patterns (even without extension)
    if re.search(r'arxiv\.org/pdf/|/pdf$|\.pdf\?', url, re.IGNORECASE):
        return "document"
    # Markdown treated as webpage
    if ext == '.md':
        return "webpage"
    return "webpage"


async def search(query: str, max_results: int = 10) -> str:
    """Search the web via SearXNG."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEARCH) as client:
            # Headers to satisfy SearXNG bot detection
            headers = {
                "Accept": "application/json",
                "User-Agent": "MCP-Gateway/1.0",
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1"
            }
            
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "pageno": 1},
                headers=headers
            )
            
            # Handle 403 specifically
            if resp.status_code == 403:
                return f"Search error: SearXNG returned 403 Forbidden.\n\nPossible fixes:\n1. Check SearXNG is running: docker ps | grep searxng\n2. Verify limiter.toml allows Docker networks\n3. Check SEARXNG_URL in .env matches your setup\n\nCurrent URL: {SEARXNG_URL}"
            
            resp.raise_for_status()
            data = resp.json()
            
            results = data.get("results", [])[:max_results]
            if not results:
                return f"Search: '{query}'\nNo results found."
            
            lines = [f"Search: '{query}'\n"]
            
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                url = r.get("url", "")
                snippet = r.get("content", "")[:200]
                lines.append(f"{i}. {title}\n   {url}\n   {snippet}...\n")
            
            return "\n".join(lines)
    except httpx.ConnectError as e:
        return f"Search error: Cannot connect to SearXNG at {SEARXNG_URL}.\n\nIs SearXNG running?\nStart with: docker compose up -d searxng"
    except Exception as e:
        return f"Search error: {type(e).__name__}: {e}"
