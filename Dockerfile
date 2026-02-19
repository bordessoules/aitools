# MCP Gateway - Docker Image
# Supports Tier 2/3 deployments (MarkItDown without Chrome)

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN uv pip install --system -r requirements.txt && \
    uv pip install --system "markitdown[all]" openai mcp httpx

# Copy application code
COPY src/ ./src/

# Create cache directory
RUN mkdir -p cache

# Expose gateway port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run gateway
CMD ["python", "-m", "src.gateway", "-t", "dual", "-p", "8000"]
