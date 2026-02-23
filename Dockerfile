# MCP Gateway - Docker Image
# Includes Chromium (headless + headed via Xvfb) for web extraction

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies + Xvfb for headed Playwright mode
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN uv pip install --system -r requirements.txt && \
    uv pip install --system "markitdown[all]" openai mcp httpx playwright docker

# Install Chromium for web extraction (Docker Playwright)
RUN playwright install chromium --with-deps

# Copy application code
COPY mcp_gateway/ ./mcp_gateway/

# Create cache, auth, and preload directories
RUN mkdir -p cache auth preload workspace

# Default: headed mode with Xvfb (better bot resistance than headless)
ENV PLAYWRIGHT_DOCKER_HEADED=true

# Expose gateway ports (8000=all-in-one, 8001-8004=per-plugin)
EXPOSE 8000 8001 8002 8003 8004

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start with Xvfb if headed mode enabled, otherwise headless
CMD if [ "$PLAYWRIGHT_DOCKER_HEADED" = "true" ]; then \
        Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR -noreset & \
        sleep 1; \
        DISPLAY=:99 python -m mcp_gateway.gateway -t sse -p 8000; \
    else \
        python -m mcp_gateway.gateway -t sse -p 8000; \
    fi
