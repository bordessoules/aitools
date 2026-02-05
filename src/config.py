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
# SERVICE URLs
# =============================================================================

# SearXNG search engine
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

# LM Studio (OpenAI-compatible API)
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1")

# Docling services
DOCLING_URL = os.getenv("DOCLING_URL", "http://localhost:8001")
DOCLING_GPU_URL = os.getenv("DOCLING_GPU_URL", "http://localhost:8002")
USE_DOCLING_GPU = os.getenv("USE_DOCLING_GPU", "false").lower() == "true"

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
# LLM HYPERPARAMETERS
# =============================================================================

# Vision Model (Qwen3-VL) - for image description, VLM extraction
VLM_TEMPERATURE = float(os.getenv("VLM_TEMPERATURE", "0.7"))
VLM_TOP_P = float(os.getenv("VLM_TOP_P", "0.8"))
VLM_TOP_K = int(os.getenv("VLM_TOP_K", "20"))
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", "16384"))
VLM_PRESENCE_PENALTY = float(os.getenv("VLM_PRESENCE_PENALTY", "1.5"))
VLM_REPETITION_PENALTY = float(os.getenv("VLM_REPETITION_PENALTY", "1.0"))

# Text Model (Qwen3-8B) - for analysis, summarization
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "1.0"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "1.0"))
LLM_TOP_K = int(os.getenv("LLM_TOP_K", "40"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "32768"))
LLM_PRESENCE_PENALTY = float(os.getenv("LLM_PRESENCE_PENALTY", "2.0"))
LLM_REPETITION_PENALTY = float(os.getenv("LLM_REPETITION_PENALTY", "1.0"))

# =============================================================================
# BROWSER/PLAYWRIGHT
# =============================================================================

PLAYWRIGHT_MCP_TOKEN = os.getenv("PLAYWRIGHT_MCP_TOKEN", "")

# =============================================================================
# CACHE
# =============================================================================

CACHE_DIR = Path(os.getenv("CACHE_DIR", "./cache"))
CACHE_DIR.mkdir(exist_ok=True)

# =============================================================================
# CONTENT TYPE CLASSIFICATION
# =============================================================================

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx'}

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
