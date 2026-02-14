"""HTTP Bridge for MCP Gateway"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Suppress unicode issues
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

import routing
import fetch as fetch_module

app = FastAPI(title="MCP Gateway HTTP Bridge")

@app.post("/tools/search")
async def http_search(query: str):
    try:
        result = await routing.search(query)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/tools/fetch")
async def http_fetch(url: str):
    try:
        result = await fetch_module.get_webpage(url)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
