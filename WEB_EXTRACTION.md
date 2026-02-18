# Web Extraction Options

MCP Gateway supports three methods for extracting content from web pages, configurable via environment variables.

## Extraction Methods

### 1. Vision (Chrome + Qwen3-VL) - Best Quality

Uses Chrome browser to render the page and Qwen3-VL to extract clean markdown.

**Pros:**
- ✅ Best quality - handles JavaScript, complex layouts
- ✅ AI-powered content extraction (ignores ads, nav, etc.)
- ✅ Excellent for documentation, articles, complex sites

**Cons:**
- ❌ Requires Chrome + Playwright MCP extension
- ❌ Slower (~3-5s per page)
- ❌ Cannot run in Docker (needs Chrome on host)

**Setup:**
```bash
# 1. Install Playwright MCP Chrome extension
# 2. Start extension server in Chrome
# 3. Copy token to .env

# .env
PLAYWRIGHT_MCP_TOKEN=your_token_here
WEB_EXTRACTION_METHOD=vision
```

---

### 2. Docker Playwright - Full Browser in Docker

Playwright with Chromium running inside Docker container.

**Pros:**
- ✅ Full browser with JavaScript support
- ✅ Works in Docker (no host Chrome needed)
- ✅ Pre-authenticated sessions supported
- ✅ Ad blocking and cookie popup handling

**Cons:**
- ❌ Requires larger Docker image (~500MB)
- ❌ Slower than MarkItDown (~2-3s per page)

**Setup:**
```bash
# .env
WEB_EXTRACTION_METHOD=docker_playwright
PLAYWRIGHT_DOCKER_HEADED=false
```

---

## Docker Playwright Authentication

### Setting Up Pre-Authenticated Sessions

#### For Google/Gmail Login

**Step 1: Create Auth State Locally**

```python
# save_auth.py
import asyncio
from src.docker_playwright import save_auth_state

async def main():
    await save_auth_state(
        email="your.email@gmail.com",
        password="your-app-password",  # Use App Password, not your real password
        url="https://accounts.google.com"
    )

asyncio.run(main())
```

Run locally (not in Docker):
```bash
python save_auth.py
# Complete login in the browser window
# Auth state will be saved to ./auth/playwright-state.json
```

**Step 2: Mount Auth to Docker**

```bash
# Auth file is automatically mounted if in ./auth directory
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/auth:/app/auth:ro \
  -e WEB_EXTRACTION_METHOD=docker_playwright \
  mcp-gateway:latest
```

Or in `docker-compose.yml`:
```yaml
services:
  gateway:
    volumes:
      - ./auth:/app/auth:ro
    environment:
      - WEB_EXTRACTION_METHOD=docker_playwright
      - PLAYWRIGHT_AUTH_STATE=/app/auth/playwright-state.json
```

### Extraction Modes

#### Headless Mode (Default)
```env
PLAYWRIGHT_DOCKER_HEADED=false
```
Fast, no display needed, works for most sites.

#### Headed Mode with Xvfb
```env
PLAYWRIGHT_DOCKER_HEADED=true
```
For sites that detect headless browsers or need visual debugging.

### Features

**Ad Blocking**
Automatically blocks common ad/tracking domains (Google Ads, Facebook trackers, etc.)

**Cookie Popup Handling**
Auto-clicks common cookie consent buttons (Accept, Reject, Essential only, etc.)

---

### 3. MarkItDown - Fast & Simple

Converts HTML to markdown using rule-based extraction.

**Pros:**
- ✅ Fast (~0.4s per page)
- ✅ Works in Docker (no browser needed)
- ✅ Good quality for static sites
- ✅ No token costs
- ✅ Smallest footprint

**Cons:**
- ❌ Limited JavaScript execution
- ❌ May miss complex layouts

**Setup:**
```bash
# .env - No special setup needed
WEB_EXTRACTION_METHOD=markitdown
```

---

## Configuration

### Environment Variables

| Variable | Options | Default | Description |
|----------|---------|---------|-------------|
| `WEB_EXTRACTION_METHOD` | `auto`, `vision`, `docker_playwright`, `markitdown` | `auto` | Preferred extraction method |
| `WEB_PAGE_LOAD_TIMEOUT` | Number (seconds) | `10` | Page load timeout for browser methods |
| `WEB_WAIT_FOR_JS` | `true`, `false` | `true` | Wait for JavaScript execution |
| `PLAYWRIGHT_MCP_TOKEN` | Token string | (empty) | Chrome extension token |
| `PLAYWRIGHT_DOCKER_HEADED` | `true`, `false` | `false` | Headed mode in Docker |

### Auto Mode Behavior

When `WEB_EXTRACTION_METHOD=auto` (default):

1. **Try Vision** if Chrome extension is available
2. **Try Docker Playwright** if available
3. **Fallback to MarkItDown** (always works)

### Force Specific Method

You can force a specific method in `.env`:

```bash
# Always use Vision (requires Chrome)
WEB_EXTRACTION_METHOD=vision

# Always use Docker Playwright
WEB_EXTRACTION_METHOD=docker_playwright

# Always use MarkItDown (fastest, minimal)
WEB_EXTRACTION_METHOD=markitdown
```

Or via the `fetch` tool:
```python
# The fetch tool accepts an optional force_method parameter
fetch(url, force_method="markitdown")
```

---

## Recommendations by Deployment

| Deployment | Recommended Method | Why |
|------------|-------------------|-----|
| **Local Dev (Full)** | `auto` or `vision` | Best quality when Chrome available |
| **Docker/Cloud** | `docker_playwright` or `markitdown` | Full browser or fast fallback |
| **Minimal/VPS** | `markitdown` | Fastest, lowest resources |
| **Chrome Available** | `vision` | Maximum accuracy |

---

## Troubleshooting

### Vision method not working
```bash
# Check Chrome extension is running
curl http://localhost:3000/health

# Verify token is set
echo $PLAYWRIGHT_MCP_TOKEN
```

### MarkItDown not available
```bash
# Install MarkItDown
pip install "markitdown[all]"
```

### Force fallback method
```bash
# If vision keeps failing, force markitdown
WEB_EXTRACTION_METHOD=markitdown
```
