"""
MCP Gateway Configuration - Single source of truth for all settings.

All configuration values are loaded from environment variables with sensible defaults.
Create a .env file in the project root to override defaults.
"""

import os
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _env_float(key: str) -> float | None:
    """Get float from env, return None if not set."""
    val = os.getenv(key)
    return float(val) if val else None


def _env_int(key: str) -> int | None:
    """Get int from env, return None if not set."""
    val = os.getenv(key)
    return int(val) if val else None

# =============================================================================
# LOGGING
# =============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# =============================================================================
# MULTI-PORT MCP
# =============================================================================
# Each plugin gets its own MCP port for composable tool access.
# Clients connect to the ports they need. Port 8000 keeps all tools (backward compat).
# Plugin ports map 1:1 to plugin names in PLUGIN_MODULES.

GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))  # All-in-one (backward compat)
WEB_PORT = int(os.getenv("WEB_PORT", "8001"))           # search, fetch, fetch_section, cache
KB_PORT = int(os.getenv("KB_PORT", "8002"))              # kb_search, kb_list, kb_remove, add_to_knowledge_base
AGENT_PORT = int(os.getenv("AGENT_PORT", "8003"))        # delegate_coding_agent, check_coding_job, ...
SANDBOX_PORT = int(os.getenv("SANDBOX_PORT", "8004"))    # run_code
GITEA_PLUGIN_PORT = int(os.getenv("GITEA_PLUGIN_PORT", "8005"))  # git_browse, git_pr

# Mapping from plugin name to port (used by gateway and agent composition)
PLUGIN_PORTS = {
    "web": WEB_PORT,
    "knowledge": KB_PORT,
    "agent": AGENT_PORT,
    "sandbox": SANDBOX_PORT,
    "gitea_plugin": GITEA_PLUGIN_PORT,
}

# =============================================================================
# SERVICE URLs
# =============================================================================

# SearXNG search engine
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

# =============================================================================
# LLM ENDPOINTS & MODELS
# =============================================================================
# All LLM configuration lives in config/models/.
# See mcp_gateway/models_config.py for the loader.
# Use models_config.get_vision_model() and models_config.get_agent_model()
# to resolve endpoints at runtime.

# =============================================================================
# DOCLING DOCUMENT PARSING
# =============================================================================
# Docling handles PDFs, Office docs with high-quality extraction.
# "vlm" pipeline sends page images to the vision LLM for conversion (best quality).
# "standard" pipeline uses EasyOCR + Tableformer locally (faster, no VLM needed).
# Picture descriptions optionally use the vision LLM if enabled.

# Docling service URLs
DOCLING_URL = os.getenv("DOCLING_URL", "http://localhost:5001")
DOCLING_GPU_URL = os.getenv("DOCLING_GPU_URL", "http://localhost:5001")
USE_DOCLING_GPU = os.getenv("USE_DOCLING_GPU", "false").lower() == "true"


def docling_url() -> str:
    """Return the active Docling service URL (GPU or CPU)."""
    return DOCLING_GPU_URL if USE_DOCLING_GPU else DOCLING_URL


# Cached Docling availability (set on first check, shared across modules)
_docling_available: bool | None = None


async def check_docling() -> bool:
    """Check if Docling service is available (cached after first check)."""
    global _docling_available
    if _docling_available is not None:
        return _docling_available
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{docling_url()}/health")
            _docling_available = resp.status_code == 200
    except Exception:
        _docling_available = False
    return _docling_available


# Pipeline mode:
#   "vlm"      - Sends pages to vision endpoint for conversion (best quality)
#                Falls back to local Granite-258M if no vision endpoint configured
#   "standard" - EasyOCR + Tableformer (lighter, faster, no VLM needed)
DOCLING_PIPELINE = os.getenv("DOCLING_PIPELINE", "standard")

# VLM pipeline settings
DOCLING_VLM_REPO_ID = os.getenv("DOCLING_VLM_REPO_ID", "ibm-granite/granite-docling-258M")
DOCLING_VLM_MAX_TOKENS = _env_int("DOCLING_VLM_MAX_TOKENS") or 4096
DOCLING_VLM_CONCURRENCY = _env_int("DOCLING_VLM_CONCURRENCY") or 1

# Picture descriptions (optional, uses vision endpoint from models.yaml if enabled)
# When enabled, images/charts in documents are described using the vision model
DOCLING_DO_PICTURE_DESCRIPTION = os.getenv("DOCLING_DO_PICTURE_DESCRIPTION", "true").lower() == "true"

# Standard pipeline settings (used when DOCLING_PIPELINE=standard)
DOCLING_OCR_ENGINE = os.getenv("DOCLING_OCR_ENGINE", "easyocr")

# =============================================================================
# TIMEOUTS (seconds)
# =============================================================================

TIMEOUT_SEARCH = int(os.getenv("TIMEOUT_SEARCH", "30"))
TIMEOUT_BROWSER = int(os.getenv("TIMEOUT_BROWSER", "60"))
TIMEOUT_DOCLING = int(os.getenv("TIMEOUT_DOCLING", "300"))  # 5 min for large PDFs
TIMEOUT_LLM = int(os.getenv("TIMEOUT_LLM", "120"))
TIMEOUT_OPENSEARCH = int(os.getenv("TIMEOUT_OPENSEARCH", "10"))

# =============================================================================
# CHUNKING CONFIGURATION
# =============================================================================

# Tokens per chunk (adjust based on model size)
# 4B-8B models: 4000 (conservative)
# 14B+ models: 8000
CHUNK_SIZE_TOKENS = int(os.getenv("CHUNK_SIZE_TOKENS", "4000"))

# Auto-return full content threshold (tokens)
# If document is under this size, return full content instead of TOC
AUTO_FULL_THRESHOLD_TOKENS = int(os.getenv("AUTO_FULL_THRESHOLD_TOKENS", "4000"))

# Characters per token (approximation for sizing)
CHARS_PER_TOKEN = 4

# =============================================================================
# LLM SAMPLING PARAMETERS
# =============================================================================
# Vision model sampling is now per-model in config/models/*.yaml (sampling: section).
# VLM_* env vars removed — edit the vision model's YAML file instead.

# =============================================================================
# BROWSER/PLAYWRIGHT
# =============================================================================

PLAYWRIGHT_MCP_TOKEN = os.getenv("PLAYWRIGHT_MCP_TOKEN", "")

# =============================================================================
# DOCKER PLAYWRIGHT AUTHENTICATION
# =============================================================================

# Path to pre-authenticated browser session (for Docker deployments)
# This allows reusing a logged-in session across container restarts
# To create: login locally with Chrome, copy storage state to this path
PLAYWRIGHT_AUTH_STATE = Path(os.getenv("PLAYWRIGHT_AUTH_STATE", "./auth/playwright-state.json"))

# Enable headed mode in Docker (requires Xvfb)
# Set to true if you need extensions, visual debugging, or to bypass bot detection
PLAYWRIGHT_DOCKER_HEADED = os.getenv("PLAYWRIGHT_DOCKER_HEADED", "false").lower() == "true"

# Display settings for Xvfb (when headed mode enabled in Docker)
XVFB_DISPLAY = os.getenv("XVFB_DISPLAY", ":99")
XVFB_SCREEN_SIZE = os.getenv("XVFB_SCREEN_SIZE", "1920x1080x24")

# =============================================================================
# WEB EXTRACTION PREFERENCES
# =============================================================================

# Preferred web extraction method: "auto", "docling", "local_chrome", "docker_playwright", "markitdown"
# - "auto": Docling pipeline (escalates HTML sources) → Playwright+MarkItDown fallback
# - "docling": Force Docling pipeline (direct HTTP → Docker Playwright → local Chrome)
# - "local_chrome": Force local Chrome → Docling → tail-trim (max bot resistance)
# - "docker_playwright": Force Docker Playwright + MarkItDown (no LLM)
# - "markitdown": Force MarkItDown only (fast, no browser needed)
WEB_EXTRACTION_METHOD = os.getenv("WEB_EXTRACTION_METHOD", "auto")

# Whether to wait for JavaScript to execute (for Playwright-based methods)
WEB_WAIT_FOR_JS = os.getenv("WEB_WAIT_FOR_JS", "true").lower() == "true"

# =============================================================================
# CACHE
# =============================================================================

CACHE_DIR = Path(os.getenv("CACHE_DIR", "./cache"))
CACHE_DIR.mkdir(exist_ok=True)

# =============================================================================
# AGENTS CONFIG
# =============================================================================
# Agent definitions for delegate_to_agent() — one YAML per role in config/agents/.

AGENTS_DIR = Path(os.getenv("AGENTS_DIR", "./config/agents"))

# =============================================================================
# PRELOAD FOLDER
# =============================================================================
# Drop files (PDFs, docs, etc.) into this folder and they'll be indexed into
# the knowledge base on startup.  Runs once per file — already-indexed files
# are skipped automatically (idempotent).
PRELOAD_DIR = Path(os.getenv("PRELOAD_DIR", "./preload"))
PRELOAD_ON_STARTUP = os.getenv("PRELOAD_ON_STARTUP", "true").lower() == "true"

# =============================================================================
# CONTENT TYPE CLASSIFICATION
# =============================================================================

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
DOCUMENT_EXTENSIONS = {
    # Docling-supported formats
    '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
    # MarkItDown fallback formats
    '.wav', '.mp3', '.m4a', '.flac', '.ogg', '.epub', '.zip',
}

# URL patterns for automatic transformation
URL_TRANSFORMS = [
    # arXiv: abstract -> PDF
    (r'arxiv\.org/abs/([\w\-\.]+)', r'arxiv.org/pdf/\1.pdf'),
    # bioRxiv/medRxiv: content -> full PDF
    (r'(biorxiv|medrxiv)\.org/content/([^/]+/[^/]+)(?!\.full\.pdf)', r'\1.org/content/\2.full.pdf'),
]

# GitHub patterns for raw content extraction
GITHUB_PATTERNS = [
    # Repo root → README
    (r'github\.com/([^/]+/[^/]+)/?$', r'raw.githubusercontent.com/\1/main/README.md'),
    (r'github\.com/([^/]+/[^/]+)/tree/.*', r'raw.githubusercontent.com/\1/main/README.md'),
    # Blob URLs → raw file
    (r'github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.*)', r'raw.githubusercontent.com/\1/\2/\3'),
]

# =============================================================================
# PLUGIN ENABLE FLAGS
# =============================================================================
# Control which tool groups are loaded. Disabled plugins don't register tools.

ENABLE_WEB_TOOLS = os.getenv("ENABLE_WEB_TOOLS", "true").lower() == "true"
ENABLE_KB_TOOLS = os.getenv("ENABLE_KB_TOOLS", "true").lower() == "true"
ENABLE_CODE_EXECUTION = os.getenv("ENABLE_CODE_EXECUTION", "false").lower() == "true"
ENABLE_CODING_AGENT = os.getenv("ENABLE_CODING_AGENT", "false").lower() == "true"

# =============================================================================
# KNOWLEDGE BASE
# =============================================================================

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200")

# =============================================================================
# CODE EXECUTION SANDBOX
# =============================================================================
# run_code() tool — execute Python/JavaScript in isolated Docker containers.
# Requires Docker socket access (gateway container must mount /var/run/docker.sock).
# Sandboxes run with no network, memory/CPU limits, and auto-removal.

CODE_SANDBOX_PYTHON_IMAGE = os.getenv("CODE_SANDBOX_PYTHON_IMAGE", "python:3.11-slim")
CODE_SANDBOX_NODE_IMAGE = os.getenv("CODE_SANDBOX_NODE_IMAGE", "node:20-slim")
CODE_SANDBOX_TIMEOUT = int(os.getenv("CODE_SANDBOX_TIMEOUT", "30"))
CODE_SANDBOX_MEMORY_LIMIT = os.getenv("CODE_SANDBOX_MEMORY_LIMIT", "256m")
CODE_SANDBOX_CPU_LIMIT = float(os.getenv("CODE_SANDBOX_CPU_LIMIT", "1.0"))

# =============================================================================
# CODING AGENT
# =============================================================================
# delegate_to_agent() / check_agent_job() tools — spawns a coding agent in Docker.
# Agent connects to LLM endpoint and to MCP gateway for tools.
# Requires Docker socket access (same as code execution).

# Per-agent Docker images
GOOSE_IMAGE = os.getenv("GOOSE_IMAGE", "mcp-goose:latest")
QWEN_IMAGE = os.getenv("QWEN_IMAGE", "mcp-qwen:latest")
VIBE_IMAGE = os.getenv("VIBE_IMAGE", "mcp-vibe:latest")
KIMI_IMAGE = os.getenv("KIMI_IMAGE", "mcp-kimi:latest")
GOOSE_WORKSPACE = Path(os.getenv("GOOSE_WORKSPACE", "./workspace"))
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "600"))
# Agent LLM config is now in config/models/ (defaults.agent)
GOOSE_MCP_GATEWAY_URL = os.getenv("GOOSE_MCP_GATEWAY_URL", "http://gateway:8000")
GOOSE_MEMORY_LIMIT = os.getenv("GOOSE_MEMORY_LIMIT", "2g")

# =============================================================================
# GITEA GIT SERVER
# =============================================================================
# Self-hosted Git server for persistent coding projects.
# When project= is passed to run_agent(), the workspace is backed by
# a Gitea repository. Gitea runs inside Docker; Goose (network_mode=host)
# accesses it via localhost.

GITEA_URL = os.getenv("GITEA_URL", "http://gitea:3000")  # Internal Docker URL
GITEA_HOST_PORT = int(os.getenv("GITEA_HOST_PORT", "3001"))  # Exposed on host
GITEA_PUBLIC_URL = os.getenv("GITEA_PUBLIC_URL", "")  # URL for browser links (e.g. Tailscale HTTPS)
GITEA_ADMIN_USER = os.getenv("GITEA_ADMIN_USER", "goose")
GITEA_ADMIN_PASSWORD = os.getenv("GITEA_ADMIN_PASSWORD", "goose-gitea-local")
GITEA_ADMIN_EMAIL = os.getenv("GITEA_ADMIN_EMAIL", "goose@localhost")

# =============================================================================
# CHAT UI
# =============================================================================
# HuggingFace Chat UI is a standalone Docker service, not an MCP tool.
# Configuration is primarily in docker-compose.yml and .env.

CHAT_UI_PORT = int(os.getenv("CHAT_UI_PORT", "3000"))
