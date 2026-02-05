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
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "pageno": 1},
                headers={"X-Forwarded-For": "127.0.0.1"}
            )
            resp.raise_for_status()
            data = resp.json()
            
            results = data.get("results", [])[:max_results]
            lines = [f"Search: '{query}'\n"]
            
            for i, r in enumerate(results, 1):
                title = r.get("title", "Untitled")
                url = r.get("url", "")
                snippet = r.get("content", "")[:200]
                lines.append(f"{i}. {title}\n   {url}\n   {snippet}...\n")
            
            return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"
