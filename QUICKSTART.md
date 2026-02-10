# MCP Gateway - Quick Start

## 🚀 Choose Your Setup

| Setup | Command | Best For |
|-------|---------|----------|
| **Full (GPU)** | `./deploy.sh -p standard` | Maximum accuracy |
| **CPU (Granite)** | Use Recipe B | No GPU, offline |
| **Minimal** | `./deploy.sh -p minimal` | Simplest setup |

---

## 1-Minute Setup

### Prerequisites
```bash
# Docker & Docker Compose installed
# Copy config
cp .env.example .env
```

### Deploy
```bash
# Option 1: Full setup (requires GPU LLM)
./deploy.sh          # Auto-detects best profile

# Option 2: CPU with Granite 258M
# See DEPLOYMENT.md - Recipe B

# Option 3: Minimal (no GPU needed)
./deploy.sh -p minimal
```

---

## What You Get

### Tools Available
- `search(query)` - Web search
- `fetch(url)` - Get content from any URL
- `fetch_section(url, section)` - Get specific section
- `kb_search(query)` - Search knowledge base
- `add_to_knowledge_base(url)` - Save documents

### Access URLs
- Gateway: http://localhost:8000
- SearXNG: http://localhost:8080
- OpenSearch: http://localhost:9200 (if enabled)

---

## Configuration

### Key Variables (.env)
```bash
# LLM endpoint (optional for minimal)
LMSTUDIO_URL=http://localhost:1234/v1
LLM_API_KEY=not-needed  # or actual key

# Features
USE_DOCLING_GPU=false
OPENSEARCH_URL=http://localhost:9200

# Optional
PLAYWRIGHT_MCP_TOKEN=  # Chrome extension
```

---

## Next Steps

1. **Configure `.env`** with your settings
2. **Start services** with `./deploy.sh`
3. **Connect your MCP client** to `http://localhost:8000`
4. **Start using** the tools!

---

## Help

```bash
./deploy.sh --help           # Deployment options
docker compose logs -f       # View logs
docker compose down          # Stop everything
```

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for detailed recipes.
