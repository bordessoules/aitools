# MCP Gateway

A minimal, composable MCP (Model Context Protocol) gateway for LLM tool use. Search the web and fetch content from any URL.

## Quick Start

```powershell
# Start dependencies (SearXNG + Docling)
cd ..\searxng-mcp
docker-compose --profile gpu up -d

# Start the gateway
python -m src.gateway -t sse -p 8000
```

## Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `search` | Web search | Finding information, discovering URLs |
| `fetch` | Retrieve content | Getting content from any URL |
| `fetch_section` | Get document section | When fetch returns a table of contents |

## Usage Examples

### Research Workflow
```
User: "What are the build instructions for stable-diffusion.cpp?"

LLM: search("stable-diffusion.cpp build instructions")
→ Gets: URLs to GitHub repo, docs, etc.

LLM: fetch("https://github.com/leejet/stable-diffusion.cpp")
→ Gets: README content or table of contents
```

### Large Document Handling
```
User: "What does the encyclopedia say about quantum physics?"

LLM: fetch("https://example.com/encyclopedia.pdf")
→ Returns table of contents:
   "This document is large (42 sections)..."
   [0] Introduction...
   [15] Q: Quantum mechanics to Quasars...

LLM: fetch_section("https://example.com/encyclopedia.pdf", section=15)
→ Gets section 15 content about quantum physics
```

### Direct URL Fetch
```
User: "Summarize this article"

LLM: fetch("https://arxiv.org/abs/1706.03762")
→ Gets paper abstract (auto-converted from arxiv abs to PDF)
```

## Tool Reference

### `search(query: str) -> str`
Search the web via SearXNG.

Returns search results with titles, URLs, and snippets.

### `fetch(url: str) -> str`
Fetch content from any URL.

**Content type handling:**
| URL Type | Handling |
|----------|----------|
| GitHub repos | README.md (main/master) |
| arXiv abstracts | Auto-converted to PDF |
| PDFs, DOCXs | Parsed via Docling |
| Images | Described via Vision AI |
| Web pages | HTTP extraction (browser optional) |

**Size handling:**
- Small documents (< ~4000 tokens): Returns full content
- Large documents: Returns table of contents; use `fetch_section()`

### `fetch_section(url: str, section: int) -> str`
Fetch a specific section from a large document.

Use ONLY after `fetch()` returns a table of contents.

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Required
SEARXNG_URL=http://localhost:8080
LMSTUDIO_URL=http://localhost:1234/v1

# Optional (for GPU document parsing)
DOCLING_GPU_URL=http://localhost:8002
USE_DOCLING_GPU=true

# Optional (for browser extraction)
PLAYWRIGHT_MCP_TOKEN=your_token_here
```

## Architecture

```
src/
├── gateway.py    # MCP tool definitions
├── routing.py    # URL classification, search
├── fetch.py      # Content fetching (browser, HTTP, vision)
├── documents.py  # Docling integration, caching, chunking
└── config.py     # Configuration

~400 lines total
```

## Docker Services

The gateway requires these services (run via docker-compose):

| Service | Port | Purpose |
|---------|------|---------|
| `searxng` | 8080 | Web search engine |
| `docling-gpu` | 8002 | PDF/document parsing (GPU) |

## Requirements

**Required:**
- Python 3.11+
- Docker & Docker Compose (for SearXNG search)

**Optional:**
- LM Studio or OpenAI-compatible API (for image descriptions via Vision AI)
- Docling GPU service (for PDF parsing)
- Playwright MCP Chrome extension (for JavaScript-rendered pages - HTTP fallback works without this)

## Installation

```bash
# Clone and setup
git clone <repo>
cd aitools

# Install dependencies (Python + Node.js)
python setup.py

# Configure
cp .env.example .env
# Edit .env with your settings

# Start services
docker-compose --profile gpu up -d  # in searxng-mcp directory

# Start gateway
python -m src.gateway -t sse -p 8000
```

## License

MIT
