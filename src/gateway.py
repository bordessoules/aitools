"""
MCP Gateway - Minimal tool interface for web search and content retrieval.

Exposed tools:
- search(query): Search the web
- fetch(url): Fetch content from any URL (one-time use)
- fetch_section(url, section): Fetch a specific section from a large document
- add_to_knowledge_base(url): Add a document to the knowledge base
- kb_search(query): Search the knowledge base
- kb_list(): List documents in knowledge base
- kb_remove(url): Remove document from knowledge base

WORKFLOW:
1. kb_search(query) - Check knowledge base first (FAST)
2. search(query) - Find new sources if needed
3. fetch(url) - Read and evaluate content
4. add_to_knowledge_base(url) - Save valuable content for later
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

import config
import routing
import fetch as fetch_module
import documents
import knowledge_base as kb

mcp = FastMCP("mcp-gateway", host="0.0.0.0")


@mcp.tool()
async def search(query: str) -> str:
    """
    Search the web via SearXNG for current information.
    
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
async def fetch(url: str) -> str:
    """
    Fetch content from any URL for immediate reading.
    
    This is for ONE-TIME USE only. Content is not saved for later reference.
    Use this to read and evaluate whether content is worth saving.
    
    Handles content types automatically:
    - Web pages: Vision → Docker Playwright → MarkItDown
    - PDFs/DOCs: Docling → MarkItDown fallback
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
    
    Returns:
        Document content, or table of contents if document is large
    """
    url = routing.normalize_url(url)
    handler = routing.classify(url)
    
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
    This searches through all documents added with add_to_knowledge_base().
    
    Use this when:
    - Starting any research task (check what you already know)
    - The user refers to previous discussions or documents
    - You need information that was saved earlier
    
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


def run_stdio():
    mcp.run(transport="stdio")


def run_sse(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount

    async def health(request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[
        Route("/health", health),
        Mount("/", app=mcp.sse_app()),
    ])

    print(f"Starting MCP Gateway on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=8000)
    args = parser.parse_args()
    
    if args.transport == "stdio":
        run_stdio()
    else:
        run_sse(args.host, args.port)
