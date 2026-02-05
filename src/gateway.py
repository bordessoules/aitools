"""
MCP Gateway - Minimal tool interface for web search and content retrieval.

Exposed tools:
- search(query): Search the web
- fetch(url): Fetch content from any URL
- fetch_section(url, section): Fetch a specific section from a large document
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

import config
import routing
import fetch as fetch_module
import documents

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
async def fetch(url: str) -> str:
    """
    Fetch content from any URL.
    
    Handles content types automatically:
    - Web pages: Retrieved via browser
    - PDFs/DOCs: Parsed and extracted as text
    - Images: Described via vision AI
    - GitHub repos: README extracted
    
    SMART SIZE HANDLING:
    - Small documents: Returns full content immediately
    - Large documents: Returns a table of contents with section numbers.
      When you see "This is the table of contents", use fetch_section(url, section=N) 
      to retrieve the specific section you need.
    
    Args:
        url: Any URL to fetch content from
    
    Returns:
        Document content, or table of contents if document is large
    """
    url = routing.normalize_url(url)
    handler = routing.classify(url)
    
    # Images: describe via vision
    if handler == "image":
        return await fetch_module.describe_image(url)
    
    # Documents (PDFs, DOCs): parse with Docling
    if handler == "document":
        doc = documents.get(url)
        if doc is None:
            doc = await documents.fetch_and_cache(url)
        if doc is None:
            return f"Error: Could not fetch document from {url}"
        
        # Check size - return full or TOC
        content = doc.full_text()
        estimated_tokens = len(content) // config.CHARS_PER_TOKEN
        
        if estimated_tokens <= config.AUTO_FULL_THRESHOLD_TOKENS:
            return content
        
        return documents.format_toc(doc)
    
    # Webpages: fetch via browser
    return await fetch_module.get_webpage(url)


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
