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
# SERVICE URLs
# =============================================================================

# SearXNG search engine
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

# =============================================================================
# VISION API (for general vision tasks)
# =============================================================================
# OpenAI-compatible endpoint for vision models (Qwen3-VL, GPT-4V, etc.)
# Used for: picture descriptions in documents, web screenshot extraction
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
# Granite-258M ALWAYS runs inside the container (fast, no external API needed).
# Picture descriptions optionally use VISION_API_URL if enabled.

# Docling service URLs
DOCLING_URL = os.getenv("DOCLING_URL", "http://localhost:5001")
DOCLING_GPU_URL = os.getenv("DOCLING_GPU_URL", "http://localhost:5001")
USE_DOCLING_GPU = os.getenv("USE_DOCLING_GPU", "false").lower() == "true"

# Pipeline mode:
#   "vlm"      - Granite-258M handles OCR, tables, layout (best quality, runs in container)
#   "standard" - EasyOCR + Tableformer (lighter, faster, no VLM needed)
DOCLING_PIPELINE = os.getenv("DOCLING_PIPELINE", "vlm")

# VLM pipeline settings (Granite-258M runs locally in container)
DOCLING_VLM_REPO_ID = os.getenv("DOCLING_VLM_REPO_ID", "ibm-granite/granite-docling-258M")
DOCLING_VLM_MAX_TOKENS = _env_int("DOCLING_VLM_MAX_TOKENS") or 4096

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

# Vision Model (Qwen3-VL) - for image description, web extraction
VLM_TEMPERATURE = _env_float("VLM_TEMPERATURE")
VLM_TOP_P = _env_float("VLM_TOP_P")
VLM_TOP_K = _env_int("VLM_TOP_K")
VLM_MAX_TOKENS = _env_int("VLM_MAX_TOKENS")
VLM_PRESENCE_PENALTY = _env_float("VLM_PRESENCE_PENALTY")
VLM_REPETITION_PENALTY = _env_float("VLM_REPETITION_PENALTY")

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

# Preferred web extraction method: "auto", "vision", "docker_playwright", "markitdown"
# - "auto": Automatically choose best available (default)
# - "vision": Use Chrome + Vision LLM (best quality, requires Chrome ext)
# - "docker_playwright": Use Playwright in Docker (JS support, auth capable)
# - "markitdown": Use MarkItDown (fast, works in Docker, good quality)
WEB_EXTRACTION_METHOD = os.getenv("WEB_EXTRACTION_METHOD", "auto")

# Whether to wait for JavaScript to execute (for vision method)
WEB_WAIT_FOR_JS = os.getenv("WEB_WAIT_FOR_JS", "true").lower() == "true"

# =============================================================================
# CACHE
# =============================================================================

CACHE_DIR = Path(os.getenv("CACHE_DIR", "./cache"))
CACHE_DIR.mkdir(exist_ok=True)

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
# KNOWLEDGE BASE
# =============================================================================

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200")
