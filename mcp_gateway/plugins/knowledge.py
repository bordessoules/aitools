"""Knowledge base plugin.

Provides tools for managing a persistent knowledge base backed by OpenSearch.
add_to_knowledge_base fetches content internally — no dependency on the web plugin.
"""

import httpx

from .. import config
from .. import documents
from .. import fetch as fetch_module
from .. import knowledge_base as kb
from .. import routing
from ..logger import get_logger

log = get_logger("plugin.knowledge")


def register(mcp):
    """Register knowledge base tools with FastMCP."""

    @mcp.tool()
    async def add_to_knowledge_base(url: str) -> str:
        """
        Add a URL to your knowledge base for future recall.

        Use this AFTER reading with fetch() when the content is valuable and
        reference-worthy. Think of it as bookmarking important sources.

        WHEN TO SAVE:
        - Official documentation and manuals
        - Technical specifications and datasheets
        - Authoritative tutorials or guides
        - Important research papers or articles
        - Code repositories with useful examples
        - Content the user explicitly says to save

        DO NOT SAVE:
        - Search result pages or forum discussions
        - Content you're just exploring (might be irrelevant)
        - Outdated or low-quality sources
        - Temporary or time-sensitive information

        If the URL was recently fetched, uses cached content.
        Otherwise, fetches then saves.

        Args:
            url: URL to add to the knowledge base

        Returns:
            Confirmation message with document title
        """
        url = routing.normalize_url(url)
        handler = routing.classify(url)

        # Images
        if handler == "image":
            doc = documents.get(url)
            if doc:
                content = doc.full_text()
            else:
                content = await fetch_module.describe_image(url)
            return await kb.add_document(url, "Image", content, [], "image")

        # Documents
        if handler == "document":
            doc = documents.get(url)
            if doc is None:
                doc = await documents.fetch_and_cache(url)
            if doc is None:
                return f"Error: Could not fetch document from {url}"

            content = doc.full_text()
            return await kb.add_document(url, doc.title or "Untitled", content, doc.chunks, "document")

        # Webpages
        doc = documents.get(url)
        if doc:
            # Use cached content
            content = doc.full_text()
            title = doc.title or "Web Page"
        else:
            # Fetch then save
            content = await fetch_module.get_webpage(url)
            if content.startswith("Error:"):
                return f"Error: Could not fetch webpage from {url}"

            # Extract title from first line if possible
            title = "Web Page"
            lines = content.split('\n')
            for line in lines[:5]:
                if line.strip() and not line.startswith('Source:'):
                    title = line.strip()[:100]
                    break

        return await kb.add_document(url, title, content, [], "webpage")

    @mcp.tool()
    async def kb_search(query: str) -> str:
        """
        Search your knowledge base for previously saved documents.

        FAST and FREE - always call this FIRST before web search!
        This searches through all documents added with add_to_knowledge_base()
        and any pre-indexed reference material.

        Use this when:
        - Starting any research task (check what you already know)
        - The user refers to previous discussions or documents
        - You need information that was saved earlier
        - Looking for content from preloaded company docs or reference material

        Only use web search (search()) if kb_search returns no relevant results,
        or if you need the latest/current information.

        Args:
            query: Search query

        Returns:
            Search results from the knowledge base with relevant snippets
        """
        return await kb.search(query)

    @mcp.tool()
    async def kb_list() -> str:
        """
        List all documents in the knowledge base.

        Returns a summary of each document including its title, URL,
        source type (webpage, pdf, github, etc.), and when it was added.
        Documents are sorted by most recently added first.

        Returns:
            List of documents with titles, URLs, and source types
        """
        return await kb.list_documents()

    @mcp.tool()
    async def kb_remove(url: str) -> str:
        """
        Remove a document from the knowledge base.

        Args:
            url: URL of the document to remove (must match the URL used in add_to_knowledge_base)

        Returns:
            Confirmation of removal or error message
        """
        return await kb.remove_document(url)


async def health_checks() -> list[tuple[str, bool]]:
    """Check OpenSearch availability."""
    checks = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OPENSEARCH_URL}/_cluster/health")
            if resp.status_code == 200:
                checks.append(("[OK] OpenSearch (Knowledge Base)", True))
            else:
                checks.append(("[WARN] OpenSearch responded but unhealthy", False))
    except Exception:
        checks.append(("[INFO] OpenSearch not reachable - knowledge base disabled", False))
    return checks


PLUGIN = {
    "name": "knowledge",
    "env_var": "ENABLE_KB_TOOLS",
    "default_enabled": True,
    "register": register,
    "health_checks": health_checks,
}
