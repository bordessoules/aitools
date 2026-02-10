# MCP Gateway Deployment Recipes

Complete deployment matrix for all Chrome modes and transport options.

## Quick Decision Tree

```
Where are you deploying?
├── Local Machine (Your PC)
│   ├── Using Kimi CLI / Cursor / Claude Desktop
│   │   └── Recipe B: Local Chrome + stdio
│   ├── Using Web Interface / API
│   │   └── Recipe A: Local Chrome + SSE
│   └── No Chrome available / Headless only
│       └── Recipe C: Docker Playwright + SSE
├── Cloud / VPS / Docker
│   ├── Need Chrome with GUI (auth, bot detection)
│   │   └── Recipe D: Docker Playwright Headed + SSE
│   └── Headless is fine
│       └── Recipe C: Docker Playwright Headless + SSE
└── Minimal / Just testing
    └── Recipe E: MarkItDown only + stdio
```

---

## Recipe A: Local Chrome + SSE (Full Featured)

**When**: You have Chrome, want web API, multiple clients

### Architecture
```
┌─────────────────────────────────────────┐
│  Chrome (Host) + Extension              │
│       ↕                                 │
│  MCP Gateway (Docker/SSE) ←─┬─ Web UI   │
│       ↕                     ├─ API      │
│  Services                   └─ Kimi*    │
│  (SearXNG, OpenSearch, Docling)         │
└─────────────────────────────────────────┘
* Kimi via stdio-to-SSE bridge (see below)
```

### Deploy
```bash
# 1. Ensure Chrome extension is running
# 2. Copy token to .env
# 3. Deploy with SSE transport
./deploy.sh -p gpu
docker compose --profile gpu up -d
```

### .env
```env
PLAYWRIGHT_MCP_TOKEN=your_token_here
WEB_EXTRACTION_METHOD=auto
LMSTUDIO_URL=http://host.docker.internal:1234/v1
LLM_API_KEY=not-needed
```

### Access
- Gateway: http://localhost:8000/sse
- SearXNG: http://localhost:8080
- Dashboard: http://localhost:5601

---

## Recipe B: Local Chrome + stdio (Kimi CLI Native)

**When**: Using Kimi CLI, Cursor, Claude Desktop (stdio only)

### Architecture
```
┌─────────────────────────────────────────┐
│  Chrome (Host) + Extension              │
│       ↕                                 │
│  MCP Gateway (Local/stdio) ◄── Kimi CLI │
│       ↕                                 │
│  Services (Docker)                      │
│  (SearXNG, OpenSearch, Docling)         │
└─────────────────────────────────────────┘
```

### Deploy
```bash
# 1. Start services in Docker
docker compose --profile gpu up -d searxng opensearch docling-gpu

# 2. Run gateway locally with stdio
python -m src.gateway -t stdio
```

### Kimi CLI Config
```json
{
  "mcpServers": {
    "gateway": {
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "src.gateway", "-t", "stdio"],
      "workingDir": "C:\\Ai_dir\\aitools",
      "env": {
        "PYTHONPATH": "src"
      }
    }
  }
}
```

---

## Recipe C: Docker Playwright Headless + SSE

**When**: Cloud deployment, no host Chrome, CI/CD

### Architecture
```
┌─────────────────────────────────────────┐
│  MCP Gateway (Docker)                   │
│  ├─ SSE Server                          │
│  └─ Docker Playwright (headless)        │
│       ↕                                 │
│  Services                               │
│  (SearXNG, OpenSearch, Docling)         │
└─────────────────────────────────────────┘
```

### Deploy
```bash
# Use Dockerfile.playwright (includes Playwright browsers)
docker build -f Dockerfile.playwright -t mcp-gateway:playwright .

# Deploy with Playwright profile
WEB_EXTRACTION_METHOD=docker_playwright
docker compose --profile gpu up -d
```

### .env
```env
# No Chrome extension needed
PLAYWRIGHT_MCP_TOKEN=

# Use Docker Playwright
WEB_EXTRACTION_METHOD=docker_playwright
PLAYWRIGHT_DOCKER_HEADED=false

LMSTUDIO_URL=http://host.docker.internal:1234/v1
```

---

## Recipe D: Docker Playwright Headed + SSE

**When**: Sites with bot detection, need full browser

### Architecture
```
┌─────────────────────────────────────────┐
│  MCP Gateway (Docker)                   │
│  ├─ SSE Server                          │
│  └─ Docker Playwright + Xvfb (headed)   │
│       ↕                                 │
│  Services                               │
└─────────────────────────────────────────┘
```

### Deploy
```bash
# Same as Recipe C but with headed mode
WEB_EXTRACTION_METHOD=docker_playwright
PLAYWRIGHT_DOCKER_HEADED=true
docker compose --profile gpu up -d
```

### .env
```env
PLAYWRIGHT_DOCKER_HEADED=true
XVFB_DISPLAY=:99
XVFB_SCREEN_SIZE=1920x1080x24
```

---

## Recipe E: MarkItDown Only + stdio (Minimal)

**When**: No GPU, no Chrome, minimal setup

### Architecture
```
┌─────────────────────────────────────────┐
│  MCP Gateway (Local/Docker)             │
│  └─ MarkItDown only (no browser)        │
│       ↕                                 │
│  SearXNG only (no Docling/OpenSearch)   │
└─────────────────────────────────────────┘
```

### Deploy
```bash
# Minimal profile
./deploy.sh -p minimal
# or
docker compose --profile minimal up -d
```

### .env
```env
LMSTUDIO_URL=  # Optional
LLM_API_KEY=
USE_DOCLING_GPU=false
OPENSEARCH_URL=
PLAYWRIGHT_MCP_TOKEN=
WEB_EXTRACTION_METHOD=markitdown
```

---

## Multi-Client Scenarios

### Scenario 1: Kimi CLI + Web Dashboard

**Solution**: Run both transports simultaneously

```bash
# Terminal 1: stdio for Kimi
python -m src.gateway -t stdio

# Terminal 2: SSE for Web (connects to same Docker services)
docker compose --profile gpu up -d gateway
```

### Scenario 2: Multiple Kimi CLI Users

**Solution**: Each user runs their own stdio gateway, shares Docker services

```bash
# Shared infrastructure (run once)
docker compose --profile gpu up -d searxng opensearch docling-gpu

# Each user runs locally
python -m src.gateway -t stdio
```

### Scenario 3: HTTP API for Universal Access

**Solution**: Add HTTP bridge for non-MCP clients

```bash
# Start HTTP bridge alongside SSE gateway
python -m src.http_bridge -p 8001 &
python -m src.gateway -t sse -p 8000
```

Clients can use:
- SSE: `http://localhost:8000/sse` (MCP native)
- HTTP: `http://localhost:8001/tools/search` (Universal)

---

## Transport Comparison

| Transport | Multi-Client | Docker Friendly | Browser Friendly | Native MCP |
|-----------|-------------|-----------------|------------------|------------|
| **SSE** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **stdio** | ❌ No | ⚠️ Needs TTY | ❌ No | ✅ Yes |
| **HTTP** | ✅ Yes | ✅ Yes | ✅ Yes | ❌ No* |

*HTTP is not native MCP but can be bridged

---

## Chrome Mode Comparison

| Mode | Speed | Quality | Requirements | Use Case |
|------|-------|---------|--------------|----------|
| **Local Chrome** | ~3s | ⭐⭐⭐⭐⭐ Best | Chrome + Extension | Local dev, max quality |
| **Docker Headless** | ~2s | ⭐⭐⭐⭐ Good | Docker only | CI/CD, cloud |
| **Docker Headed** | ~3s | ⭐⭐⭐⭐⭐ Best | Docker + Xvfb | Bot detection sites |
| **MarkItDown** | ~0.4s | ⭐⭐⭐⭐ Good | None | Fallback, speed |

---

## Recommended Defaults

| User Type | Default Recipe |
|-----------|----------------|
| Developer (you) | Recipe B (Chrome + stdio) |
| Team/Shared | Recipe A (Chrome + SSE) |
| Cloud/Production | Recipe C (Docker Playwright) |
| Testing/Minimal | Recipe E (MarkItDown) |
