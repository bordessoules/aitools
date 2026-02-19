"""Shared utilities for MCP Gateway."""

import re
import unicodedata
from dataclasses import dataclass, field

import config


# =============================================================================
# RESULT TYPE
# =============================================================================

@dataclass
class Result:
    """Structured success/error return type."""
    success: bool
    content: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def ok(content: str, **meta) -> "Result":
        return Result(success=True, content=content, metadata=meta or {})

    @staticmethod
    def fail(error: str) -> "Result":
        return Result(success=False, error=error)


# =============================================================================
# TEXT UTILITIES
# =============================================================================

def safe_text(text: str) -> str:
    """Strip control characters, preserve all printable Unicode.

    Unlike the old approach (encode ascii ignore), this keeps accented
    characters, CJK, emoji, and everything else that's printable.
    Only actual control characters (category C) are removed.
    """
    return "".join(
        ch for ch in text
        if ch in ("\n", "\r", "\t") or unicodedata.category(ch)[0] != "C"
    )


def extract_title(text: str, fallback: str = "Untitled") -> str:
    """Extract title from markdown h1 or first short non-HTML line."""
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines[:5]:
        if not line.startswith("<") and len(line) < 100:
            return line
    return fallback


# =============================================================================
# LLM PARAMETERS
# =============================================================================

def build_llm_params(messages: list | None = None, max_tokens: int | None = None) -> dict:
    """Build OpenAI-compatible chat completion params from config.

    Consolidates the old _build_llm_params (fetch.py) and
    _build_vision_params (documents.py) into one function.
    Uses 'max_tokens' consistently (universally supported).
    """
    params = {"model": config.VISION_MODEL}

    if messages is not None:
        params["messages"] = messages
        params["stream"] = False

    if config.VLM_TEMPERATURE is not None:
        params["temperature"] = config.VLM_TEMPERATURE
    if config.VLM_TOP_P is not None:
        params["top_p"] = config.VLM_TOP_P
    if config.VLM_TOP_K is not None:
        params["top_k"] = config.VLM_TOP_K
    if config.VLM_PRESENCE_PENALTY is not None:
        params["presence_penalty"] = config.VLM_PRESENCE_PENALTY
    if config.VLM_REPETITION_PENALTY is not None:
        params["repetition_penalty"] = config.VLM_REPETITION_PENALTY

    if max_tokens:
        params["max_tokens"] = max_tokens
    elif config.VLM_MAX_TOKENS is not None:
        params["max_tokens"] = config.VLM_MAX_TOKENS

    return params


# =============================================================================
# HTML TO MARKDOWN
# =============================================================================

def html_to_markdown(html: str, url: str = "") -> str:
    """Convert HTML to markdown using html2text.

    Replaces the old 36-line regex converter that missed tables,
    pre blocks, h5/h6, and broke on nested HTML.
    """
    import html2text

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_emphasis = False
    h.body_width = 0  # No line wrapping
    h.ignore_tables = False
    h.protect_links = True
    h.unicode_snob = True

    text = h.handle(html)
    if url:
        text = f"Source: {url}\n\n{text}"
    return text
