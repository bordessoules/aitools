"""Web retrieval and cache management plugin.

Provides search, fetch, fetch_section, and cache tools for web content retrieval.
"""

import httpx

from .. import config
from .. import documents
from .. import fetch as fetch_module
from .. import models_config
from .. import routing
from ..logger import get_logger

log = get_logger("plugin.web")


def register(mcp):
    """Register web retrieval tools with FastMCP."""

    @mcp.tool()
    async def search(query: str) -> str:
        """
        Search the web for current information.

        BEST PRACTICE: Before calling this, try kb_search() first!
        Your knowledge base is faster and may already contain relevant answers.

        Use web search when:
        - kb_search() returned no relevant results
        - You need the latest or real-time information
        - The user asks about current events or recent developments
        - The topic is likely not in your knowledge base

        Args:
            query: Search query

        Returns:
            Search results with URLs and snippets
        """
        return await routing.search(query, max_results=10)

    @mcp.tool()
    async def fetch(url: str, force_refresh: bool = False) -> str:
        """
        Fetch content from any URL for immediate reading.

        This is for ONE-TIME USE only. Content is not saved for later reference.
        Use this to read and evaluate whether content is worth saving.

        Handles content types automatically:
        - Web pages: Rendered and converted to markdown
        - PDFs/DOCs: Parsed and converted to markdown
        - Images: Described via vision AI
        - GitHub repos: README extracted

        SMART SIZE HANDLING:
        - Small documents: Returns full content immediately
        - Large documents: Returns a table of contents with section numbers.
          When you see "This is the table of contents", use fetch_section(url, section=N)
          to retrieve the specific section you need.

        WORKFLOW:
        1. fetch(url) - Read and evaluate the content
        2. If valuable: call add_to_knowledge_base(url) to save it

        Args:
            url: Any URL to fetch content from
            force_refresh: Bypass cache and re-fetch content (useful for dynamic/stale pages)

        Returns:
            Document content, or table of contents if document is large
        """
        url = routing.normalize_url(url)
        handler = routing.classify(url)

        # Clear cache if force_refresh requested
        if force_refresh:
            documents.delete(url)

        # Images: describe via vision
        if handler == "image":
            content = await fetch_module.describe_image(url)
            return content

        # Documents (PDFs, DOCs): parse with Docling
        if handler == "document":
            doc = documents.get(url)
            if doc is None:
                doc = await documents.fetch_and_cache(url)
            if doc is None:
                return f"Error: Could not fetch document from {url}"

            content = doc.full_text()

            # Check size - return full or TOC
            estimated_tokens = len(content) // config.CHARS_PER_TOKEN

            if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
                return content

            return documents.format_toc(doc)

        # Webpages: fetch via browser
        content = await fetch_module.get_webpage(url)
        return content

    @mcp.tool()
    async def fetch_section(url: str, section: int) -> str:
        """
        Fetch a specific section from a large document.

        Use this ONLY after fetch() returned a table of contents telling you to use
        fetch_section(). The section numbers (0, 1, 2...) are shown in the table of contents.

        Args:
            url: Same URL used in fetch()
            section: Section number from the table of contents (0, 1, 2, ...)

        Returns:
            The content of that specific section
        """
        url = routing.normalize_url(url)

        # Get from cache (document must have been fetched already)
        doc = documents.get(url)

        if doc is None:
            # Try to fetch if it's a document
            handler = routing.classify(url)
            if handler == "document":
                doc = await documents.fetch_and_cache(url)

        if doc is None:
            return f"Error: Document not found. Use fetch('{url}') first."

        return documents.format_chunk(doc, section)

    @mcp.tool()
    async def cache(action: str = "stats", url: str = "") -> str:
        """
        Manage the document cache.

        Use this to inspect or clean up cached documents. Cached documents
        are used by fetch() to avoid re-downloading content.

        Actions:
        - "stats": Show cache statistics (document count, chunk count)
        - "list": List cached documents (most recent first, max 20)
        - "clear": Remove a specific URL from cache (requires url parameter)
        - "clear_all": Remove ALL cached documents

        Args:
            action: Cache action ("stats", "list", "clear", "clear_all")
            url: URL to clear (only used with action="clear")

        Returns:
            Cache information or confirmation message
        """
        return documents.cache_action(action, url)


async def health_checks() -> list[tuple[str, bool]]:
    """Check SearXNG, Vision API, Docling, Docker Playwright, Preload folder."""
    checks = []

    # SearXNG
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.SEARXNG_URL}/healthz")
            if resp.status_code == 200:
                checks.append(("[OK] SearXNG", True))
            else:
                checks.append((f"[WARN] SearXNG responded {resp.status_code}", False))
    except Exception:
        checks.append(("[WARN] SearXNG not reachable - search will not work", False))

    # Vision API / LLM
    vision = models_config.get_vision_model()
    if vision["url"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{vision['url']}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("id", "?") for m in data.get("data", [])[:3]]
                    checks.append((f"[OK] Vision API ({', '.join(models)})", True))
                else:
                    checks.append(("[WARN] Vision API responded but no models", False))
        except Exception:
            checks.append(("[WARN] Vision API not reachable - LLM features disabled", False))
    else:
        checks.append(("[INFO] Vision API not configured - LLM features disabled", False))

    # Docling
    docling_label = "Docling GPU" if config.USE_DOCLING_GPU else "Docling CPU"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.docling_url()}/health")
            if resp.status_code == 200:
                checks.append((f"[OK] {docling_label}", True))
            else:
                checks.append((f"[WARN] {docling_label} responded {resp.status_code}", False))
    except Exception:
        checks.append((f"[INFO] {docling_label} not reachable - using MarkItDown for documents", False))

    # Docker Playwright
    try:
        from ..docker_playwright import is_available
        if await is_available():
            checks.append(("[OK] Docker Playwright", True))
        else:
            checks.append(("[INFO] Docker Playwright not available", False))
    except ImportError:
        checks.append(("[INFO] Docker Playwright not installed", False))

    # Preload folder (informational, not counted as a service)
    preload_dir = config.PRELOAD_DIR
    if config.PRELOAD_ON_STARTUP and preload_dir.exists():
        file_count = sum(1 for f in preload_dir.rglob("*") if f.is_file() and not f.name.startswith("."))
        if file_count:
            checks.append((f"  Preload: {file_count} document(s) to index", True))
        # empty = nothing to report

    return checks


PLUGIN = {
    "name": "web",
    "env_var": "ENABLE_WEB_TOOLS",
    "default_enabled": True,
    "register": register,
    "health_checks": health_checks,
}
