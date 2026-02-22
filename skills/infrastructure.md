---
name: infrastructure
description: Network topology, services, and deployment reference for the MCP Gateway stack.
---

# Infrastructure Skill

Reference for the MCP Gateway deployment across the Tailscale network.

## Network (Tailscale)

| Machine | IP | OS | Role |
|---------|----|----|------|
| **master** | `100.90.12.114` | Windows | Docker host, gateway, SearXNG, OpenSearch |
| **bluefin** | `100.64.10.17` | Linux | LM Studio (LLM inference) |
| **ia-5090x2** | `100.100.138.95` | Windows | Future: heavy GPU workloads (dual 5090) |

DNS resolves inside Docker containers (e.g., `http://bluefin:1234/v1` works).

## LLM Models on Bluefin (LM Studio :1234)

| Model | GPU | VRAM | Slots | Context | Use |
|-------|-----|------|-------|---------|-----|
| `qwen/qwen3-vl-4b` | 3060 12GB | ~8GB | 2 | 64k | Vision: screenshots, image description |
| `openai/gpt-oss-20b` | 5060ti 16GB | ~14GB | 4 | 128k | Text: process tool, summarize, etc. |
| `qwen3-coder-next` | - | - | - | - | Code generation (available, not wired) |
| `text-embedding-nomic-embed-text-v1.5` | - | - | - | - | Embeddings (available, not wired) |

## Docker Services (master)

| Service | Container | Port | Profile | Purpose |
|---------|-----------|------|---------|---------|
| gateway | mcp-gateway | 8000 | always | MCP tool server (SSE/stdio) |
| searxng | mcp-searxng | 8080 | always | Meta search engine |
| opensearch | mcp-opensearch | 9200 | standard+ | Knowledge base (BM25) |
| opensearch-dashboards | mcp-opensearch-dashboards | 5601 | standard+ | KB debug UI |
| docling-cpu | mcp-docling-cpu | 5001 | cpu | Document parsing (Granite-258M) |
| docling-gpu | mcp-docling-gpu | 5001 | gpu | Document parsing (GPU accelerated) |

## Gateway Build Variants

| Dockerfile | Image Size | Extractors Available |
|------------|-----------|---------------------|
| `Dockerfile` (default) | ~200MB | MarkItDown only |
| `Dockerfile.playwright` | ~1.5GB | Playwright + MarkItDown |

Set `GATEWAY_DOCKERFILE=Dockerfile.playwright` in `.env` for browser support.

## Deployment Profiles

```bash
docker compose --profile minimal up -d    # Gateway + SearXNG only
docker compose --profile standard up -d   # + OpenSearch KB
docker compose --profile cpu up -d        # + Docling CPU
docker compose --profile gpu up -d        # + Docling GPU (needs NVIDIA)
```

## Key Directories

| Path | Purpose |
|------|---------|
| `./cache/` | SQLite document cache (mounted into container) |
| `./auth/` | Playwright auth state (mounted into container) |
| `./src/` | Gateway Python source |
| `./skills/` | Skill files (this directory) |

## Checking Service Health

```bash
# Gateway
curl http://localhost:8000/health

# SearXNG
curl http://localhost:8080/healthz

# OpenSearch
curl http://localhost:9200/_cluster/health

# Bluefin LM Studio models
curl http://bluefin:1234/v1/models
```
