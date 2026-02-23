"""
MCP Gateway - Plugin-based tool server.

Tools are organized into plugins (mcp_gateway/plugins/), each enabled via env var:
  ENABLE_WEB_TOOLS=true        search, fetch, fetch_section, cache
  ENABLE_KB_TOOLS=true         kb_search, kb_list, kb_remove, add_to_knowledge_base
  ENABLE_CODE_EXECUTION=false  run_code
  ENABLE_CODING_AGENT=false    delegate_coding_agent, check_coding_job, list_agents, list_projects

WORKFLOW:
1. kb_search(query) - Check knowledge base first (FAST)
2. search(query) - Find new sources if needed
3. fetch(url) - Read and evaluate content
4. If valuable: add_to_knowledge_base(url) - Save for later
"""

import httpx
from mcp.server.fastmcp import FastMCP

from . import config
from . import preload as preload_module
from .plugins import load_plugins, run_health_checks

mcp = FastMCP("mcp-gateway", host="0.0.0.0")

# Load enabled plugins — registers their tools with mcp
_loaded_plugins = load_plugins(mcp)


async def startup_health_check():
    """Run health checks from all loaded plugins plus infrastructure checks."""

    checks = []

    # Plugin health checks (SearXNG, Docling, OpenSearch, Docker, Gitea, etc.)
    plugin_checks = await run_health_checks(_loaded_plugins)
    checks.extend(plugin_checks)

    # Chat UI (external service, not a plugin)
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
    from starlette.routing import Route

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
