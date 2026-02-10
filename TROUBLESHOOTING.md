# Troubleshooting Guide

## Common Issues

### 403 Forbidden on Search

**Symptom**: `Search error: SearXNG returned 403 Forbidden`

**Cause**: SearXNG doesn't have JSON format enabled or is blocking the request

**Fix**:

1. **Ensure SearXNG is running**:
   ```bash
   docker ps | grep searxng
   # If not running:
   docker compose --profile minimal up -d searxng
   ```

2. **Check SearXNG settings are mounted**:
   The `searxng-settings.yml` file must be mounted to enable JSON format.
   
   In `docker-compose.yml`:
   ```yaml
   volumes:
     - ./searxng-settings.yml:/etc/searxng/settings.yml:ro
   ```

3. **Restart SearXNG to apply settings**:
   ```bash
   docker compose restart searxng
   ```

4. **Test directly**:
   ```bash
   curl "http://localhost:8080/search?q=test&format=json"
   ```
   
   Should return JSON. If it returns HTML, the settings aren't applied.

5. **Check SearXNG logs**:
   ```bash
   docker logs mcp-searxng
   ```

### Gateway Can't Connect to Services

**Symptom**: Errors like "Cannot connect to SearXNG" or "Connection refused"

**Cause**: Docker networking issues or services not started

**Fix**:

1. **Check all services are running**:
   ```bash
   docker compose ps
   ```

2. **Check network connectivity**:
   From inside the gateway container:
   ```bash
   docker exec -it mcp-gateway sh
   # Then test:
   wget -qO- http://searxng:8080/healthz
   ```

3. **Verify environment variables**:
   ```bash
   docker exec mcp-gateway env | grep -E "SEARXNG|LMSTUDIO|DOCLING"
   ```

### Vision Extraction Not Working

**Symptom**: "Vision extraction failed" or fallback to MarkItDown

**Cause**: Chrome/Playwright not available or LM Studio not responding

**Fix**:

1. **Check LM Studio is accessible from container**:
   ```bash
   docker exec -it mcp-gateway sh
   wget -qO- http://host.docker.internal:1234/v1/models
   ```
   
   If this fails, LM Studio isn't accepting external connections.

2. **Use Tier 2/3 (no vision)**:
   Set `PLAYWRIGHT_MCP_TOKEN=` (empty) in `.env` to disable vision.
   Gateway will use MarkItDown for web pages.

### Docling GPU Not Available

**Symptom**: "Docling GPU not available - PDFs will use MarkItDown"

**Fix**:

1. **Check NVIDIA Docker runtime**:
   ```bash
   docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
   ```

2. **Use CPU version instead**:
   ```bash
   docker compose --profile cpu up -d docling-cpu
   ```

3. **Or use MarkItDown only** (Tier 3):
   Set `USE_DOCLING_GPU=false` in `.env`

## Debug Mode

Run gateway with debug logging:

```bash
docker exec -it mcp-gateway sh
python -c "
import sys
sys.path.insert(0, 'src')
import asyncio
import routing

async def test():
    result = await routing.search('test query')
    print(result)

asyncio.run(test())
"
```

## Reset Everything

If all else fails:

```bash
# Stop everything
docker compose --profile standard down

# Clear volumes (WARNING: loses cached data)
docker volume rm mcp-gateway_searxng-data mcp-gateway_opensearch-data

# Rebuild
docker compose build --no-cache

# Start fresh
docker compose up -d
```
