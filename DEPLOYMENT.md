# Deployment Guide

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env with your settings

# 2. Deploy
./deploy.sh              # Auto-detect best profile
# or
./deploy.sh -p standard  # With Knowledge Base
# or
./deploy.sh -p minimal   # Minimal setup
```

## Three Deployment Tiers

| Tier | Profile | Use Case | GPU | Speed | Accuracy |
|------|---------|----------|-----|-------|----------|
| **Tier 1** | Full | Production | GPU LLM + Docling | ~5-15s/page | ⭐⭐⭐⭐⭐ Best |
| **Tier 2** | CPU | CPU-only | None (Granite 258M) | ~2-5s/page | ⭐⭐⭐⭐ Good |
| **Tier 3** | Minimal | Fallback | None (MarkItDown) | ~2s/page | ⭐⭐⭐ Basic |

---

## Recipe A: Full Setup (Recommended)

**When**: You have GPU for LLM, want maximum accuracy

### Architecture
- **Docling**: Layout + Tables + OCR
- **Your Qwen3-VL**: Image descriptions via API
- **Services**: SearXNG + OpenSearch

### Deploy
```bash
docker compose --profile standard up -d
python -m src.gateway -t sse -p 8000
```

### .env
```bash
LMSTUDIO_URL=http://your-server:1234/v1
LLM_API_KEY=not-needed
USE_DOCLING_GPU=false
OPENSEARCH_URL=http://localhost:9200
PLAYWRIGHT_MCP_TOKEN=  # Optional Chrome ext
```

---

## Recipe B: CPU Mode (Granite 258M)

**When**: No GPU, want offline capability

### Architecture
- **Granite Docling 258M**: One VLM does everything
- **No external LLM needed**

### Deploy
```bash
docker run -d -p 5001:5001 \
  quay.io/docling-project/docling-serve-cpu:latest

docker compose --profile minimal up -d
python -m src.gateway -t sse -p 8000
```

### .env
```bash
LMSTUDIO_URL=          # Not needed
LLM_API_KEY=
USE_DOCLING_GPU=false
OPENSEARCH_URL=        # Optional
```

---

## Recipe C: Minimal (MarkItDown)

**When**: Absolute minimal, no ML models

### Deploy
```bash
pip install "markitdown[all]"
docker compose --profile minimal up -d searxng
python -m src.gateway -t sse -p 8000
```

### .env
```bash
LMSTUDIO_URL=
LLM_API_KEY=
USE_DOCLING_GPU=false
OPENSEARCH_URL=
PLAYWRIGHT_MCP_TOKEN=
```

---

## Web Extraction Methods

Three methods are available for extracting web page content:

| Method | Speed | Quality | Requirements |
|--------|-------|---------|--------------|
| **Vision** | ~3-5s | ⭐⭐⭐⭐⭐ Best | Chrome + Playwright MCP extension |
| **Docker Playwright** | ~2-3s | ⭐⭐⭐⭐ Good | Docker (no host browser) |
| **MarkItDown** | ~0.4s | ⭐⭐⭐⭐ Good | None (Docker-friendly) |

See [WEB_EXTRACTION.md](WEB_EXTRACTION.md) for detailed configuration.

---

## Configuration Reference

### LLM Setup

| Provider | LMSTUDIO_URL | LLM_API_KEY |
|----------|--------------|-------------|
| LM Studio | `http://localhost:1234/v1` | `not-needed` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` |
| Together | `https://api.together.xyz/v1` | Your key |

### Required Environment Variables

```bash
# Minimum (for minimal setup)
SEARXNG_URL=http://localhost:8080
CACHE_DIR=./cache

# For full setup
LMSTUDIO_URL=http://localhost:1234/v1
LLM_API_KEY=not-needed
OPENSEARCH_URL=http://localhost:9200
```

---

## Size & Requirements

| Tier | Download | RAM | Disk |
|------|----------|-----|------|
| **A (Full)** | ~5GB | 8GB+ | 10GB+ |
| **B (Granite)** | ~500MB | 4GB | 2GB |
| **C (Minimal)** | ~200MB | 2GB | 1GB |

---

## Decision Tree

```
Do you have GPU for LLM?
├── YES → Recipe A (Full)
│         └── Best accuracy
└── NO → Do you need offline?
          ├── YES → Recipe B (Granite)
          │         └── Good quality, fully offline
          └── NO → Recipe C (MarkItDown)
                    └── Minimal, fastest setup
```

---

## Maintenance

```bash
# Update images
docker compose --profile standard pull

# View logs
docker compose logs -f

# Stop everything
docker compose --profile standard down
```

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues.
