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
- process(content, task, prompt): Process text with local LLM
- cache(action, url): Manage document cache
- run_code(language, code): Execute code in isolated sandbox [requires ENABLE_CODE_EXECUTION]
- run_coding_agent(task): Run Goose coding agent [requires ENABLE_CODING_AGENT]

WORKFLOW:
1. kb_search(query) - Check knowledge base first (FAST)
2. search(query) - Find new sources if needed
3. fetch(url) - Read and evaluate content
4. If valuable: add_to_knowledge_base(url) - Save for later
5. process(content, task) - Post-process fetched content (summarize, extract, translate, analyze)
"""

import httpx
from mcp.server.fastmcp import FastMCP

from . import config
from . import code_sandbox
from . import coding_agent
from . import documents
from . import fetch as fetch_module
from . import knowledge_base as kb
from . import preload as preload_module
from . import processor
from . import routing

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
async def fetch(url: str, force_refresh: bool = False) -> str:
    """
    Fetch content from any URL for immediate reading.

    This is for ONE-TIME USE only. Content is not saved for later reference.
    Use this to read and evaluate whether content is worth saving.

    Handles content types automatically:
    - Web pages: Docling pipeline (escalating HTML sources) → tail-trim
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
    and any files preloaded from the preload folder.

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


@mcp.tool()
async def process(content: str, task: str = "summarize", prompt: str | None = None) -> str:
    """
    Process text content with a local LLM.

    Use this to post-process content retrieved via fetch(). Great for
    summarizing articles, extracting structured data, translating, or
    analyzing sentiment.

    Built-in tasks:
    - "summarize": Concise summary of key points
    - "extract": Extract structured data (names, dates, prices, entities)
    - "translate": Translate content (default: to English)
    - "analyze": Sentiment, tone, themes, and patterns

    You can also provide a custom prompt to override the built-in tasks.

    Args:
        content: Text content to process
        task: Built-in task name ("summarize", "extract", "translate", "analyze")
        prompt: Custom instruction (overrides task if provided)

    Returns:
        Processed content from LLM

    Examples:
        process(article_text, task="summarize")
        process(product_page, prompt="Extract the price and availability")
        process(foreign_text, task="translate")
    """
    return await processor.process(content, task=task, prompt=prompt)


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


@mcp.tool()
async def run_code(language: str, code: str) -> str:
    """
    Execute code in a secure, isolated Docker sandbox.

    Runs code in a fresh container with NO network access, memory/CPU limits,
    and automatic cleanup. Use this for:
    - Running Python or JavaScript code snippets
    - Testing algorithms or data processing
    - Verifying calculations or transformations

    Supported languages: "python" (or "py"), "javascript" (or "js", "node")

    Security: Each execution runs in a fresh container with:
    - No network access (completely isolated)
    - 256MB memory limit (configurable)
    - 30-second timeout (configurable)
    - Container auto-removed after execution

    Args:
        language: Programming language ("python" or "javascript")
        code: Source code to execute

    Returns:
        Execution output (stdout + stderr) with timing info
    """
    if not config.ENABLE_CODE_EXECUTION:
        return "Error: Code execution is disabled. Set ENABLE_CODE_EXECUTION=true in .env"
    return await code_sandbox.run_code(language, code)


@mcp.tool()
async def run_coding_agent(task: str, workspace: str | None = None) -> str:
    """
    Run an autonomous coding agent (Goose) to complete a programming task.

    Spawns a Goose coding agent in a Docker container that can:
    - Write and modify code files in the workspace
    - Execute shell commands
    - Use MCP gateway tools (search, fetch, knowledge base)

    Best for multi-step coding tasks:
    - "Fix the authentication bug in auth.py"
    - "Create a REST API for user management"
    - "Refactor this module to use async/await"

    NOTE: Requires a capable LLM (7B+ recommended for reliable results).

    Args:
        task: Natural language description of the coding task
        workspace: Optional workspace directory path (default: ./workspace)

    Returns:
        Agent output with task results
    """
    if not config.ENABLE_CODING_AGENT:
        return "Error: Coding agent is disabled. Set ENABLE_CODING_AGENT=true in .env"
    return await coding_agent.run_task(task, workspace)


async def startup_health_check():
    """Check which services are available on startup and log status."""

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
    if config.VISION_API_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{config.VISION_API_URL}/models")
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
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
    docling_label = "Docling GPU" if config.USE_DOCLING_GPU else "Docling CPU"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{docling_url}/health")
            if resp.status_code == 200:
                checks.append((f"[OK] {docling_label}", True))
            else:
                checks.append((f"[WARN] {docling_label} responded {resp.status_code}", False))
    except Exception:
        checks.append((f"[INFO] {docling_label} not reachable - using MarkItDown for documents", False))

    # OpenSearch
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OPENSEARCH_URL}/_cluster/health")
            if resp.status_code == 200:
                checks.append(("[OK] OpenSearch (Knowledge Base)", True))
            else:
                checks.append(("[WARN] OpenSearch responded but unhealthy", False))
    except Exception:
        checks.append(("[INFO] OpenSearch not reachable - knowledge base disabled", False))

    # Docker Playwright
    try:
        from .docker_playwright import is_available
        if await is_available():
            checks.append(("[OK] Docker Playwright", True))
        else:
            checks.append(("[INFO] Docker Playwright not available", False))
    except ImportError:
        checks.append(("[INFO] Docker Playwright not installed", False))

    # Preload folder
    preload_dir = config.PRELOAD_DIR
    if config.PRELOAD_ON_STARTUP and preload_dir.exists():
        file_count = sum(1 for f in preload_dir.rglob("*") if f.is_file() and not f.name.startswith("."))
        if file_count:
            checks.append((f"[OK] Preload folder ({file_count} files)", True))
        else:
            checks.append(("[INFO] Preload folder empty", False))
    else:
        checks.append(("[INFO] No preload folder", False))

    # Code Execution Sandbox
    if config.ENABLE_CODE_EXECUTION:
        try:
            if await code_sandbox.is_available():
                checks.append(("[OK] Code Sandbox (Docker)", True))
            else:
                checks.append(("[WARN] Code Sandbox: Docker not accessible", False))
        except Exception:
            checks.append(("[WARN] Code Sandbox: docker package not installed", False))
    else:
        checks.append(("[INFO] Code Sandbox disabled", False))

    # Coding Agent (Goose)
    if config.ENABLE_CODING_AGENT:
        try:
            if await coding_agent.is_available():
                checks.append(("[OK] Coding Agent (Goose)", True))
            else:
                checks.append(("[WARN] Coding Agent: Docker not accessible", False))
        except Exception:
            checks.append(("[WARN] Coding Agent: docker package not installed", False))
    else:
        checks.append(("[INFO] Coding Agent disabled", False))

    # Chat UI (external service)
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"http://localhost:{config.CHAT_UI_PORT}")
            if resp.status_code == 200:
                checks.append(("[OK] Chat UI", True))
            else:
                checks.append(("[INFO] Chat UI not reachable", False))
    except Exception:
        checks.append(("[INFO] Chat UI not running", False))

    # Print summary
    ok_count = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n{'=' * 50}")
    print(f"MCP Gateway - Service Status ({ok_count}/{total} available)")
    print(f"{'=' * 50}")
    for msg, _ in checks:
        print(f"  {msg}")
    print(f"{'=' * 50}")

    # Preload documents into KB (after health checks so we know what's available)
    await preload_module.preload_documents()
    print()


def run_stdio():
    mcp.run(transport="stdio")


def run_sse(host: str = "0.0.0.0", port: int = 8000):
    import asyncio
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount

    async def health(request):
        return JSONResponse({"status": "ok"})

    # Run startup health check
    asyncio.get_event_loop().run_until_complete(startup_health_check())

    # Serve both MCP transports:
    # - SSE at /sse + /messages/ (for existing MCP clients like Claude Code)
    # - Streamable HTTP at /mcp (for Goose and newer MCP clients)
    import contextlib

    sse_app_instance = mcp.sse_app()
    streamable_app = mcp.streamable_http_app()

    # Streamable HTTP needs its session manager started via lifespan
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    # Combine routes from both transports into one Starlette app
    sse_routes = list(sse_app_instance.routes)
    streamable_routes = list(streamable_app.routes)

    app = Starlette(
        routes=[
            Route("/health", health),
            *streamable_routes,  # /mcp
            *sse_routes,         # /sse, /messages/
        ],
        lifespan=lifespan,
    )

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
