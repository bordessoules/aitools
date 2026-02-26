"""
MCP Gateway - Multi-port plugin-based tool server.

Each plugin runs on its own port for composable tool access:
  Port 8000  (all-in-one)  all tools — backward compatible
  Port 8001  (web)         search, fetch, fetch_section, cache
  Port 8002  (knowledge)   kb_search, kb_list, kb_remove, add_to_knowledge_base
  Port 8003  (agent)       delegate_to_agent, await_agent, list_roles, list_projects
  Port 8004  (sandbox)     run_code

Clients and agents connect only to the ports they need.
Claude Code / Chat UI connect to 8000 (all tools).
Coding agents connect to 8001+8002 (web+KB, no delegate).


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
from .plugins import load_plugins, load_plugins_multi, run_health_checks

# All-in-one instance on port 8000 (backward compat)
mcp = FastMCP("mcp-gateway", host="0.0.0.0")
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


def _build_mcp_starlette(mcp_instance: FastMCP, name: str):
    """Build a Starlette app serving both SSE and Streamable HTTP for one FastMCP."""
    import contextlib
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request):
        return JSONResponse({"status": "ok", "server": name})

    sse_app = mcp_instance.sse_app()
    streamable_app = mcp_instance.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp_instance.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", health),
            *list(streamable_app.routes),  # /mcp
            *list(sse_app.routes),         # /sse, /messages/
        ],
        lifespan=lifespan,
    )


def run_sse(host: str = "0.0.0.0", port: int = 8000):
    import asyncio
    import uvicorn

    # Run startup health check
    asyncio.get_event_loop().run_until_complete(startup_health_check())

    # Build per-plugin FastMCP instances on dedicated ports
    plugin_servers = {}
    for plugin_name, plugin_port in config.PLUGIN_PORTS.items():
        plugin_mcp = FastMCP(f"mcp-{plugin_name}", host=host)
        plugin_servers[plugin_name] = (plugin_mcp, plugin_port)

    # Register plugins on their dedicated instances
    plugin_mcps = {name: s[0] for name, s in plugin_servers.items()}
    load_plugins_multi(plugin_mcps)

    # Build Starlette apps
    main_app = _build_mcp_starlette(mcp, "mcp-gateway")
    plugin_apps = {
        name: (app, port)
        for name, (mcp_inst, port) in plugin_servers.items()
        for app in [_build_mcp_starlette(mcp_inst, f"mcp-{name}")]
    }

    # Run all servers concurrently
    async def serve_all():
        servers = []

        # Main all-in-one server
        main_config = uvicorn.Config(
            main_app, host=host, port=config.GATEWAY_PORT,
            log_level="info",
        )
        servers.append(uvicorn.Server(main_config).serve())

        # Per-plugin servers
        for name, (app, p) in plugin_apps.items():
            cfg = uvicorn.Config(app, host=host, port=p, log_level="warning")
            servers.append(uvicorn.Server(cfg).serve())

        port_info = ", ".join(f"{n}={p}" for n, (_, p) in plugin_apps.items())
        print(f"Starting MCP Gateway on {host}:{config.GATEWAY_PORT} (all-in-one)")
        print(f"Plugin ports: {port_info}")

        await asyncio.gather(*servers)

    asyncio.run(serve_all())


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
