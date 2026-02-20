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
- process(content, task, prompt): Process text with LLM

WORKFLOW:
1. kb_search(query) - Check knowledge base first (FAST)
2. search(query) - Find new sources if needed
3. fetch(url) - Read and evaluate content
4. add_to_knowledge_base(url) - Save valuable content for later
5. process(content) - Summarize, extract, translate, or analyze
"""

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
import httpx

import config
import routing
import fetch as fetch_module
import documents
import knowledge_base as kb
import gitea

mcp = FastMCP("mcp-gateway", host="0.0.0.0")


# Health endpoint for Docker healthcheck
from starlette.responses import JSONResponse
from starlette.routing import Route


def _health(request):
    return JSONResponse({"status": "ok"})


_health_route = Route("/health", _health)


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


# =============================================================================
# PROCESS TOOL - Text processing via LLM
# =============================================================================

BUILTIN_TASKS = {
    "summarize": "Provide a concise summary of the key points in this text. Focus on the most important information.",
    "extract": "Extract structured data from this text: names, dates, prices, key entities. Return as a clean list.",
    "translate": "Translate this text to English. Preserve formatting and structure.",
    "analyze": "Analyze this text for: sentiment, tone, main themes, and notable patterns. Be concise.",
}


@mcp.tool()
async def process(content: str, task: str = "summarize", prompt: Optional[str] = None) -> str:
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
    api_url = config.LLM_API_URL
    if not api_url:
        return "Error: No LLM endpoint configured. Set LLM_API_URL or VISION_API_URL in .env"

    # Build the system prompt
    if prompt:
        system_prompt = prompt
    elif task in BUILTIN_TASKS:
        system_prompt = BUILTIN_TASKS[task]
    else:
        return f"Error: Unknown task '{task}'. Use: {', '.join(BUILTIN_TASKS.keys())} or provide a custom prompt."

    # Truncate content to avoid blowing context (keep ~12k tokens worth)
    max_chars = 48000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[...truncated, {len(content)} total chars]"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    params = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_LLM) as client:
            resp = await client.post(f"{api_url}/chat/completions", json=params)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        return f"Error: LLM request timed out after {config.TIMEOUT_LLM}s"
    except Exception as e:
        return f"Error: LLM processing failed: {e}"


# =============================================================================
# GITEA TOOLS - Git collaboration via Gitea REST API
# =============================================================================

@mcp.tool()
async def gitea_list_repos() -> str:
    """List Git repositories on the local Gitea server.

    Returns:
        List of repositories with names and descriptions
    """
    return await gitea.list_repos()


@mcp.tool()
async def gitea_create_repo(name: str, description: str = "", private: bool = False) -> str:
    """Create a new Git repository on Gitea.

    Args:
        name: Repository name (e.g. "my-project")
        description: Short description of the repository
        private: Whether the repo should be private

    Returns:
        Confirmation with repository URL
    """
    return await gitea.create_repo(name, description, private)


@mcp.tool()
async def gitea_list_branches(owner: str, repo: str) -> str:
    """List branches in a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name

    Returns:
        List of branch names
    """
    return await gitea.list_branches(owner, repo)


@mcp.tool()
async def gitea_create_branch(
    owner: str, repo: str, branch_name: str, from_branch: str = "main"
) -> str:
    """Create a new branch in a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name
        branch_name: Name for the new branch
        from_branch: Branch to create from (default: main)

    Returns:
        Confirmation message
    """
    return await gitea.create_branch(owner, repo, branch_name, from_branch)


@mcp.tool()
async def gitea_get_file(
    owner: str, repo: str, filepath: str, branch: str = "main"
) -> str:
    """Read a file from a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name
        filepath: Path to the file (e.g. "src/main.py")
        branch: Branch to read from (default: main)

    Returns:
        File content
    """
    return await gitea.get_file(owner, repo, filepath, branch)


@mcp.tool()
async def gitea_put_file(
    owner: str, repo: str, filepath: str, content: str, message: str, branch: str = "main"
) -> str:
    """Create or update a file in a Gitea repository (creates a commit).

    Args:
        owner: Repository owner username
        repo: Repository name
        filepath: Path for the file (e.g. "src/main.py")
        content: File content to write
        message: Commit message
        branch: Branch to commit to (default: main)

    Returns:
        Confirmation message
    """
    return await gitea.put_file(owner, repo, filepath, content, message, branch)


@mcp.tool()
async def gitea_create_pr(
    owner: str, repo: str, title: str, body: str, head: str, base: str = "main"
) -> str:
    """Create a pull request in a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name
        title: PR title
        body: PR description
        head: Source branch name
        base: Target branch name (default: main)

    Returns:
        Confirmation with PR number and URL
    """
    return await gitea.create_pull_request(owner, repo, title, body, head, base)


@mcp.tool()
async def gitea_list_prs(owner: str, repo: str, state: str = "open") -> str:
    """List pull requests in a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name
        state: Filter by state: "open", "closed", or "all"

    Returns:
        List of pull requests with details
    """
    return await gitea.list_pull_requests(owner, repo, state)


@mcp.tool()
async def gitea_add_pr_review(
    owner: str, repo: str, pr_index: int, body: str, event: str = "COMMENT"
) -> str:
    """Submit a review on a pull request.

    Args:
        owner: Repository owner username
        repo: Repository name
        pr_index: Pull request number
        body: Review comment text
        event: Review type: "APPROVE", "REQUEST_CHANGES", or "COMMENT"

    Returns:
        Confirmation message
    """
    return await gitea.create_pr_review(owner, repo, pr_index, body, event)


@mcp.tool()
async def gitea_create_issue(
    owner: str, repo: str, title: str, body: str = ""
) -> str:
    """Create an issue in a Gitea repository.

    Use issues to track tasks for agent collaboration.

    Args:
        owner: Repository owner username
        repo: Repository name
        title: Issue title
        body: Issue description (markdown)

    Returns:
        Confirmation with issue number and URL
    """
    return await gitea.create_issue(owner, repo, title, body)


@mcp.tool()
async def gitea_list_issues(owner: str, repo: str, state: str = "open") -> str:
    """List issues in a Gitea repository.

    Args:
        owner: Repository owner username
        repo: Repository name
        state: Filter by state: "open", "closed", or "all"

    Returns:
        List of issues with titles and labels
    """
    return await gitea.list_issues(owner, repo, state)


def run_stdio():
    mcp.run(transport="stdio")


def run_dual(host: str = "0.0.0.0", port: int = 8000):
    """Serve both SSE and streamable HTTP transports on one port.

    Endpoints:
      /sse, /messages/  - SSE transport (Claude Code, Claude Desktop)
      /mcp              - Streamable HTTP transport (Goose, newer clients)
      /health           - Health check
    """
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    sse_app = mcp.sse_app()
    http_app = mcp.streamable_http_app()

    # Combine routes from both transport apps (non-overlapping paths):
    #   /health            - Health check
    #   /sse, /messages    - SSE transport (Claude Code, Claude Desktop)
    #   /mcp               - Streamable HTTP transport (Goose, newer clients)
    routes = [_health_route] + list(sse_app.routes) + list(http_app.routes)

    # Streamable HTTP requires a lifespan context for session management
    app = Starlette(routes=routes, lifespan=http_app.router.lifespan_context)
    print(f"Starting MCP Gateway on {host}:{port}")
    print(f"  SSE:             http://{host}:{port}/sse")
    print(f"  Streamable HTTP: http://{host}:{port}/mcp")
    print(f"  Health:          http://{host}:{port}/health")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--transport", choices=["stdio", "dual"], default="dual")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "stdio":
        run_stdio()
    else:
        run_dual(args.host, args.port)
