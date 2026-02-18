# MCP Gateway

A minimal, composable MCP (Model Context Protocol) gateway for LLM tool use. Search the web, fetch content from any URL, and build a persistent knowledge base.

**Features**: Docling-powered document extraction, web content pipeline with LLM tail-trim, headed Playwright in Docker (Xvfb), knowledge base with OpenSearch, preload folder for bootstrapping, code execution sandbox, Goose coding agent, Chat UI, and flexible deployment profiles.

## Quick Start

### Deploy with One Command

```bash
# Clone and setup
cd mcp-gateway
cp .env.example .env

# Deploy with auto-detection (recommended)
./deploy.sh          # Linux/Mac
.\deploy.ps1         # Windows
```

The deploy script will detect your hardware and recommend the best configuration.

## Deployment Profiles

| Profile | Services | RAM | GPU | LLM | Best For |
|---------|----------|-----|-----|-----|----------|
| **minimal** | Gateway + Search | 4GB | No | No | Text-only extraction, works anywhere |
| **standard** | +Knowledge Base | 8GB | No | Optional | Persistent KB, no heavy PDF processing |
| **cpu** | +Docling CPU | 16GB | No | Optional | Full features, CPU-only |
| **gpu** | +Docling GPU | 8GB+ | Yes | Optional | Best PDF processing with GPU |

```bash
# Deploy specific profile
./deploy.sh -p standard
./deploy.sh -p gpu

# Or manually with docker compose
docker compose --profile standard up -d
```

## Tools

### Web & Content Tools
| Tool | Purpose | When to Use |
|------|---------|-------------|
| `search(query)` | Web search | Finding information, discovering URLs |
| `fetch(url)` | Retrieve content | Getting content from any URL (one-time use) |
| `fetch_section(url, section)` | Get document section | When fetch returns a table of contents |

### Processing & Cache Tools
| Tool | Purpose | When to Use |
|------|---------|-------------|
| `process(content, task, prompt)` | LLM text processing | Summarize, extract, translate, analyze fetched content |
| `cache(action, url)` | Manage document cache | View stats, list cached docs, clear cache |

### Knowledge Base Tools (Requires OpenSearch)
| Tool | Purpose | When to Use |
|------|---------|-------------|
| `kb_search(query)` | Search knowledge base | **Always try this FIRST** before web search |
| `add_to_knowledge_base(url)` | Save document | After fetch(), if content is valuable |
| `kb_list()` | List saved documents | See what's in your knowledge base |
| `kb_remove(url)` | Remove document | Clean up the knowledge base |

### Agent & Code Tools (Optional, requires Docker socket)
| Tool | Purpose | When to Use |
|------|---------|-------------|
| `run_code(language, code)` | Execute code in sandbox | Running Python/JS snippets safely (no network) |
| `run_coding_agent(task)` | Autonomous coding agent | Multi-step coding tasks (Goose by Block) |

## Usage Examples

### Research Workflow
```
User: "What are the build instructions for stable-diffusion.cpp?"

LLM: search("stable-diffusion.cpp build instructions")
-> Gets: URLs to GitHub repo, docs, etc.

LLM: fetch("https://github.com/leejet/stable-diffusion.cpp")
-> Gets: README content or table of contents
```

### Building a Knowledge Base
```
User: "I want to research LLMs. Please add these papers to my knowledge base."

LLM: fetch("https://arxiv.org/abs/1706.03762")
-> Reads and evaluates the paper

LLM: add_to_knowledge_base("https://arxiv.org/abs/1706.03762")
-> Saves to knowledge base for future search

User: "What do these papers say about training efficiency?"

LLM: kb_search("training efficiency techniques")
-> Searches saved papers and returns relevant snippets
```

### Preloading Documents
Drop files into `./preload/` before starting the gateway:
```bash
cp company-docs/*.pdf ./preload/
cp api-reference.docx ./preload/
docker compose up -d
# -> Files are automatically indexed into the knowledge base on startup
```

### Large Document Handling
```
User: "What does the encyclopedia say about quantum physics?"

LLM: fetch("https://example.com/encyclopedia.pdf")
-> Returns table of contents:
   "This document is large (42 sections)..."
   [0] Introduction...
   [15] Q: Quantum mechanics to Quasars...

LLM: fetch_section("https://example.com/encyclopedia.pdf", section=15)
-> Gets section 15 content about quantum physics
```

## Tool Reference

### `search(query: str) -> str`
Search the web via SearXNG for current information.

**Best practice**: Always try `kb_search()` first! It's faster and may already have answers.

### `fetch(url: str) -> str`
Fetch content from any URL for immediate reading. Content is **NOT** saved.

**Content type handling:**
| URL Type | Method | Notes |
|----------|--------|-------|
| Web pages | Docling pipeline + tail-trim | Escalating HTML sources, LLM trims nav junk |
| PDFs, DOCXs | Docling GPU/CPU | Best table/formula extraction |
| Images | Vision AI (Qwen3-VL) | Image descriptions |
| GitHub repos | Raw README extraction | Direct content fetch |
| YouTube, Audio | MarkItDown | Transcription |
| EPUB, ZIP | MarkItDown | Fallback formats |

**Size handling:**
- Small documents (< ~4000 tokens): Returns full content
- Large documents: Returns table of contents; use `fetch_section()`

### `add_to_knowledge_base(url: str) -> str`
Save a document to your knowledge base for future recall.

**When to save:**
- Official documentation and manuals
- Technical specifications
- Authoritative tutorials or guides
- Research papers or articles

**Do NOT save:**
- Search result pages or forum discussions
- Content you're just exploring
- Outdated or temporary information

### `kb_search(query: str) -> str`
Search your knowledge base for previously saved documents.

**FAST and FREE** - always call this before web search!

## Local Development

For development without Docker:

```bash
# 1. Setup
python setup.py              # Installs uv, creates venv

# 2. Configure
cp .env.example .env         # Edit with your settings

# 3. Start required services
docker compose --profile minimal up -d searxng

# 4. Start gateway locally
.\start.ps1 -Transport sse   # SSE for web clients (default)
.\start.ps1 -Transport stdio # stdio for Kimi CLI, Cursor
```

### Multi-Transport Support

The gateway supports two transport modes:

| Transport | Use Case | Multi-Client |
|-----------|----------|--------------|
| **SSE** | Web clients, APIs, multiple users | Yes |
| **stdio** | Kimi CLI, Cursor, Claude Desktop | Single client |

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Required
VISION_API_URL=http://host.docker.internal:8100/v1

# Optional: GPU document processing
USE_DOCLING_GPU=true

# Optional: Playwright web extraction (Chrome extension)
PLAYWRIGHT_MCP_TOKEN=your_token_here
```

See `.env.example` for full configuration options and deployment mode presets.

## Architecture

```
mcp_gateway/
├── __init__.py            # Package init (version)
├── gateway.py             # MCP tool definitions, startup health check
├── routing.py             # URL classification, search
├── fetch.py               # Web extraction: Docling pipeline + tail-trim
├── documents.py           # Document parsing, caching, chunking
├── preload.py             # Preload local files into KB on startup
├── processor.py           # LLM content processing (summarize, extract, etc.)
├── llm.py                 # Shared LLM/VLM parameter building
├── markitdown_client.py   # MarkItDown client + Vision AI fallback
├── docker_playwright.py   # Docker Playwright with Xvfb, auth, ad blocking
├── knowledge_base.py      # OpenSearch integration
├── code_sandbox.py        # Code execution in isolated Docker containers
├── coding_agent.py        # Goose coding agent wrapper
├── config.py              # Configuration (single source of truth)
├── logger.py              # Centralized logging
└── utils.py               # Shared helpers (safe_text, extract_title)
```

**Document Pipeline:**
- **Docling GPU/CPU**: Complex PDFs, academic papers, tables (best quality)
- **MarkItDown**: Audio, YouTube, EPUBs, simple PDFs (works everywhere)

**Web Pipeline (two concerns, separated):**
1. **Get HTML** (escalating bot resistance):
   - Docling direct HTTP (2s, ~70% of sites)
   - Docker Playwright headed/Xvfb (7s, JS rendering)
   - Local Chrome via Playwright MCP (max bot resistance)
2. **Convert to markdown**: Always Docling, then LLM tail-trim (11 tokens to remove nav junk)

**Knowledge Base:**
- **Cache** (SQLite): Automatic, every fetch cached, powers TOC/chunking
- **Knowledge Base** (OpenSearch): Manual, `add_to_knowledge_base()`, full-text search
- **Preload**: Drop files in `./preload/`, indexed on startup

**Agent & Code Execution** (optional, requires Docker socket):
- **Code Sandbox**: `run_code()` spawns isolated containers (no network, mem/CPU limits)
- **Goose Agent**: `run_coding_agent()` spawns Goose in Docker with access to vLLM + MCP tools

## Chat UI

HuggingFace Chat UI is included in `standard`, `cpu`, and `gpu` profiles. Access at http://localhost:3000.

The Chat UI connects to your vLLM instance for chat. MCP tool calling is available but commented out by default (requires a model with function calling support).

## Security Notes

### Docker Socket Access
When `ENABLE_CODE_EXECUTION` or `ENABLE_CODING_AGENT` is enabled, the gateway container requires access to the Docker socket (`/var/run/docker.sock`). This allows it to create sandbox and agent containers.

Both features are disabled by default. Only enable on trusted machines.

### Code Sandbox Isolation
- No network access (completely isolated)
- 256MB memory limit (configurable)
- 30-second timeout (configurable)
- Containers auto-removed after execution

## License

MIT
