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
# SERVICE URLs
# =============================================================================

# SearXNG search engine
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

# =============================================================================
# VISION API (for general vision tasks)
# =============================================================================
# OpenAI-compatible endpoint for vision models (Qwen3-VL, GPT-4V, etc.)
# Used for: picture descriptions in documents, web content tail-trimming
# Can be: LM Studio, llama.cpp, vLLM, OpenAI, Together, any OpenAI-compatible API
#
# Examples:
#   Local LM Studio:    http://localhost:1234/v1
#   Local llama.cpp:    http://localhost:8080/v1
#   Remote server:      http://192.168.1.100:1234/v1
#   Cloud (Together):   https://api.together.xyz/v1
#   Cloud (OpenAI):     https://api.openai.com/v1
#   Docker internal:    http://host.docker.internal:1234/v1

VISION_API_URL = os.getenv("VISION_API_URL") or os.getenv("LMSTUDIO_URL") or None  # Backward compat
VISION_API_KEY = os.getenv("VISION_API_KEY") or os.getenv("LLM_API_KEY", "not-needed")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen3-vl-4b")  # Model name for vision tasks

# =============================================================================
# DOCLING DOCUMENT PARSING
# =============================================================================
# Docling handles PDFs, Office docs with high-quality extraction.
# "vlm" pipeline sends page images to VISION_API_URL for conversion (best quality).
# "standard" pipeline uses EasyOCR + Tableformer locally (faster, no VLM needed).
# Picture descriptions optionally use VISION_API_URL if enabled.

# Docling service URLs
DOCLING_URL = os.getenv("DOCLING_URL", "http://localhost:5001")
DOCLING_GPU_URL = os.getenv("DOCLING_GPU_URL", "http://localhost:5001")
USE_DOCLING_GPU = os.getenv("USE_DOCLING_GPU", "false").lower() == "true"


def docling_url() -> str:
    """Resolved Docling URL (GPU or CPU based on config)."""
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
#   "vlm"      - Sends pages to VISION_API_URL for conversion (best quality)
#                Falls back to local Granite-258M if no VISION_API_URL
#   "standard" - EasyOCR + Tableformer (lighter, faster, no VLM needed)
DOCLING_PIPELINE = os.getenv("DOCLING_PIPELINE", "standard")

# VLM pipeline settings
DOCLING_VLM_REPO_ID = os.getenv("DOCLING_VLM_REPO_ID", "ibm-granite/granite-docling-258M")
DOCLING_VLM_MAX_TOKENS = _env_int("DOCLING_VLM_MAX_TOKENS") or 4096
DOCLING_VLM_CONCURRENCY = _env_int("DOCLING_VLM_CONCURRENCY") or 1

# Picture descriptions (optional, uses VISION_API_URL if enabled)
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
# Read from .env - no defaults here to ensure explicit configuration.
# These override whatever defaults the LLM wrapper (LM Studio, llama.cpp) uses.

# Vision Model sampling - used for VLM pipeline and picture descriptions.
# Set these to match your model's recommended sampling.
# Tested presets:
#   Qwen3-VL (2B/4B/8B): temp=0.1, top_p=0.8, top_k=20, presence_penalty=0.0, repetition_penalty=1.0
#   LFM2.5-VL-1.6B:      temp=0.1, min_p=0.15, repetition_penalty=1.05
VLM_TEMPERATURE = _env_float("VLM_TEMPERATURE")
VLM_TOP_P = _env_float("VLM_TOP_P")
VLM_TOP_K = _env_int("VLM_TOP_K")
VLM_MIN_P = _env_float("VLM_MIN_P")
VLM_PRESENCE_PENALTY = _env_float("VLM_PRESENCE_PENALTY")
VLM_REPETITION_PENALTY = _env_float("VLM_REPETITION_PENALTY")
# VLM_MAX_TOKENS removed — vLLM fills remaining context dynamically.
# Callers pass explicit max_tokens when needed (e.g. Docling=4096, fetch=8000).

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
# CODING AGENT (pluggable: goose, aider)
# =============================================================================
# run_coding_agent() tool — spawns a coding agent in Docker.
# Agent connects to vLLM for LLM and to our MCP gateway for tools.
# Requires Docker socket access (same as code execution).
# Switch agent via CODING_AGENT env var.

CODING_AGENT = os.getenv("CODING_AGENT", "goose")

# Per-agent Docker images (only the selected agent's image is used)
GOOSE_IMAGE = os.getenv("GOOSE_IMAGE", "mcp-goose:latest")
AIDER_IMAGE = os.getenv("AIDER_IMAGE", "paulgauthier/aider:latest")
GOOSE_WORKSPACE = Path(os.getenv("GOOSE_WORKSPACE", "./workspace"))
GOOSE_TIMEOUT = int(os.getenv("GOOSE_TIMEOUT", "300"))
GOOSE_LLM_URL = os.getenv("GOOSE_LLM_URL", "http://host.docker.internal:8100/v1")
GOOSE_MODEL = os.getenv("GOOSE_MODEL", "")  # Falls back to VISION_MODEL if empty
GOOSE_API_KEY = os.getenv("GOOSE_API_KEY", "")  # Falls back to VISION_API_KEY if empty
GOOSE_MCP_GATEWAY_URL = os.getenv("GOOSE_MCP_GATEWAY_URL", "http://gateway:8000")
GOOSE_MEMORY_LIMIT = os.getenv("GOOSE_MEMORY_LIMIT", "2g")

# =============================================================================
# GITEA GIT SERVER
# =============================================================================
# Self-hosted Git server for persistent coding projects.
# When project= is passed to run_coding_agent(), the workspace is backed by
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
