# MCP Gateway — AI Tool Framework

Local-first MCP server that gives any AI client (Claude Desktop, Chat UI, agents)
access to web search, document parsing, knowledge base, code execution, git repos,
and autonomous coding agents — all through composable tool ports.

## Architecture

```
Claude Desktop / Chat UI
    ↓ MCP (SSE)
MCP Gateway (Python, FastMCP)
    ├── :8000  all tools (backward compat)
    ├── :8001  web    — search, fetch, fetch_section, cache
    ├── :8002  kb     — kb_search, kb_list, kb_remove, add_to_knowledge_base
    ├── :8003  agent  — delegate_to_agent, await_agent, list_roles, list_projects
    ├── :8004  sandbox — run_code
    └── :8005  gitea  — git_browse, git_pr

LLM Inference (llama-swap, separate compose)
    └── :8085  auto-swaps models by request name

Gitea (git server for agent projects)
    └── :3001  repos, PRs, code review
```

## Key Files

- `docker-compose.yml` — gateway + services (searxng, opensearch, gitea, chat-ui)
- `docker-compose.llm.yaml` — standalone llama-swap with HF auto-download
- `config/roles.yaml` — agent role definitions (researcher, reviewer, coder)
- `mcp_gateway/plugins/` — one file per tool group, easy to add new ones
- `mcp_gateway/coding_agent.py` — Docker-based agent dispatch (vibe, goose, qwen, kimi)
- `mcp_gateway/gitea.py` — Gitea API integration
- `.env` — all configuration (LLM host, model names, API keys)

## Agent Workflow

```
delegate_to_agent(role="coder", task="...", project="my-app")
  → resolves role from config/roles.yaml
  → creates/clones Gitea repo
  → runs CLI agent in Docker container (vibe/goose/qwen/kimi)
  → agent commits and pushes to Gitea
  → await_agent(job_id) polls for result
```

Agents handle their own git (commit + push). The gateway only does a safety push
for any unpushed commits after the agent finishes.

## Models (llama-swap profiles)

| Alias | Model | Use Case |
|-------|-------|----------|
| devstral | Devstral Small 24B (IQ4_XS) | Fast coding, research |
| qwen-coder | Qwen3 Coder 30B A3B (IQ4_XS) | Code generation |
| qwen35-code | Qwen3.5 35B A3B (MXFP4) | Thinking + code (temp 0.6) |
| qwen35 | Qwen3.5 35B A3B (MXFP4) | Thinking + general (temp 1.0) |
| qwen35-fast | Qwen3.5 35B A3B (MXFP4) | No-think + general (temp 0.7) |
| qwen35-reason | Qwen3.5 35B A3B (MXFP4) | No-think + reasoning (temp 1.0) |
| qwen3-next | Qwen3 Coder Next 80B MoE | Large model, partial CPU offload |

## Adding a New Plugin

1. Create `mcp_gateway/plugins/my_plugin.py`
2. Define `register(mcp)` with `@mcp.tool()` decorated functions
3. Export `PLUGIN = {"name": "my_plugin", "env_var": "ENABLE_MY_PLUGIN", ...}`
4. Add `"my_plugin"` to `PLUGIN_MODULES` in `plugins/__init__.py`
5. Add port in `config.py` if multi-port access needed

## Adding a New Agent CLI

1. Create `Dockerfile.<agent>` in project root
2. Add profile to `AGENT_PROFILES` in `coding_agent.py`
3. Add `_build_<agent>_cmd()` and `_build_<agent>_env()` functions
4. Reference in `config/roles.yaml` under a role

## Development

```bash
# Start LLM inference (separate GPU machine OK)
docker compose -f docker-compose.llm.yaml up -d

# Start gateway + services
docker compose --profile standard up -d --build

# Rebuild gateway only (after code changes)
docker compose up -d --build gateway

# Check logs
docker logs -f mcp-gateway
```
