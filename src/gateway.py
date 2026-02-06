"""
MCP Gateway - Minimal tool interface for web search and content retrieval.

Exposed tools:
- search(query): Search the web
- fetch(url, add_to_kb): Fetch content from any URL
- fetch_section(url, section): Fetch a specific section from a large document
- kb_search(query): Search the knowledge base
- kb_list(): List documents in knowledge base
- kb_remove(url): Remove document from knowledge base
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
    Search the web via SearXNG and return results.
    
    Args:
        query: Search query
    
    Returns:
        Search results with URLs and snippets
    """
    return await routing.search(query, max_results=10)


@mcp.tool()
async def fetch(url: str, add_to_kb: bool = False) -> str:
    """
    Fetch content from any URL.
    
    Handles content types automatically:
    - Web pages: Retrieved via browser (with HTTP fallback)
    - PDFs/DOCs: Parsed and extracted as text
    - Images: Described via vision AI
    - GitHub repos: README extracted
    
    SMART SIZE HANDLING:
    - Small documents: Returns full content immediately
    - Large documents: Returns a table of contents with section numbers.
      When you see "This is the table of contents", use fetch_section(url, section=N) 
      to retrieve the specific section you need.
    
    KNOWLEDGE BASE:
    - Set add_to_kb=True to also add this document to the searchable knowledge base
    - Use kb_search() to search previously added documents
    
    Args:
        url: Any URL to fetch content from
        add_to_kb: If True, also adds the document to the knowledge base for later search
    
    Returns:
        Document content, or table of contents if document is large
    """
    url = routing.normalize_url(url)
    handler = routing.classify(url)
    
    # Images: describe via vision
    if handler == "image":
        content = await fetch_module.describe_image(url)
        if add_to_kb:
            await kb.add_document(url, "Image", content, [], "image")
        return content
    
    # Documents (PDFs, DOCs): parse with Docling
    if handler == "document":
        doc = documents.get(url)
        if doc is None:
            doc = await documents.fetch_and_cache(url)
        if doc is None:
            return f"Error: Could not fetch document from {url}"
        
        content = doc.full_text()
        
        # Add to knowledge base if requested
        if add_to_kb:
            await kb.add_document(url, doc.title or "Untitled", content, doc.chunks, "document")
        
        # Check size - return full or TOC
        estimated_tokens = len(content) // config.CHARS_PER_TOKEN
        
        if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
            return content
        
        return documents.format_toc(doc)
    
    # Webpages: fetch via browser
    content = await fetch_module.get_webpage(url)
    
    # Add to knowledge base if requested
    if add_to_kb and not content.startswith("Error:"):
        # Extract title from first line if possible
        title = "Web Page"
        lines = content.split('\n')
        for line in lines[:5]:
            if line.strip() and not line.startswith('Source:'):
                title = line.strip()[:100]
                break
        
        await kb.add_document(url, title, content, [], "webpage")
    
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
async def kb_search(query: str) -> str:
    """
    Search the knowledge base for previously added documents.
    
    This searches through all documents that were added with fetch(url, add_to_kb=True).
    Uses semantic search to find relevant content.
    
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
        url: URL of the document to remove (must match the URL used in fetch)
    
    Returns:
        Confirmation of removal or error message
    """
    return await kb.remove_document(url)


def run_stdio():
    mcp.run(transport="stdio")


def run_sse(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    print(f"Starting MCP Gateway on {host}:{port}")
    uvicorn.run(mcp.sse_app(), host=host, port=port)


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
