"""
Document caching with SQLite storage and chunking.

Caches parsed documents to avoid re-downloading and re-parsing.
Provides chunked access for large documents to stay under MCP size limits.

Uses Docling as primary parser (GPU-accelerated), with MarkItDown as fallback
for formats Docling doesn't support (audio, YouTube, EPUB, etc.).
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config
from .llm import build_vlm_params, build_auth_headers
from .logger import get_logger
from .utils import safe_text, extract_title

log = get_logger("documents")

# Import MarkItDown client for fallback support
try:
    from .markitdown_client import should_use_markitdown, convert_file as md_convert_file
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False


def _build_docling_options() -> dict:
    """
    Build Docling conversion options based on pipeline mode.

    Pipeline modes:
        - "vlm": Uses Vision API as external VLM (fast, best quality, no local VRAM).
                 Falls back to local Granite-258M if no VISION_API_URL is set.
        - "standard": EasyOCR + Tableformer (lighter, no VLM needed)

    Returns:
        dict with conversion options for Docling API
    """
    options = {
        "to_formats": ["md"],
        "image_export_mode": "placeholder",
        "document_timeout": config.TIMEOUT_DOCLING,
    }

    if config.DOCLING_PIPELINE == "vlm":
        options["pipeline"] = "vlm"

        if config.VISION_API_URL:
            # External VLM via API (recommended: fast, no local VRAM)
            vlm_params = build_vlm_params(max_tokens=config.DOCLING_VLM_MAX_TOKENS)

            options["vlm_pipeline_model_api"] = {
                "url": f"{config.VISION_API_URL}/chat/completions",
                "params": vlm_params,
                "headers": build_auth_headers(),
                "prompt": "Convert this page to markdown. Do not miss any text and only output the bare markdown!",
                "response_format": "markdown",
                "timeout": config.TIMEOUT_LLM,
                "scale": 2.0,
                "concurrency": config.DOCLING_VLM_CONCURRENCY,
            }
        else:
            # Local Granite-258M (slow, requires manual model setup, ~24GB VRAM)
            options["vlm_pipeline_model_local"] = {
                "repo_id": config.DOCLING_VLM_REPO_ID,
                "inference_framework": "transformers",
                "transformers_model_type": "automodel-imagetexttotext",
                "prompt": "Convert this page to docling.",
                "response_format": "doctags",
                "scale": 2.0,
                "temperature": 0.0,
                "extra_generation_config": {
                    "skip_special_tokens": False,
                    "max_new_tokens": config.DOCLING_VLM_MAX_TOKENS
                }
            }

    else:
        # Standard pipeline: EasyOCR + Tableformer
        options["do_ocr"] = True
        options["ocr_engine"] = config.DOCLING_OCR_ENGINE
        options["table_mode"] = "accurate"
        options["do_table_structure"] = True

    # Picture descriptions via Vision API (works with both pipelines)
    if config.DOCLING_DO_PICTURE_DESCRIPTION and config.VISION_API_URL:
        options["do_picture_description"] = True
        options["picture_description_api"] = {
            "url": f"{config.VISION_API_URL}/chat/completions",
            "params": build_vlm_params(),
            "timeout": config.TIMEOUT_LLM,
            "headers": build_auth_headers(),
            "prompt": "Describe this image in detail, including any text, diagrams, charts, or visual elements.",
        }

    return options


@dataclass
class Doc:
    """Cached document with chunks."""
    url: str
    title: str
    markdown: str
    chunks: list[dict]
    backend: str = "unknown"  # Extraction backend used

    def full_text(self) -> str:
        """Get full document text (prefer markdown, fallback to chunks)."""
        return self.markdown or "\n\n".join(c["text"] for c in self.chunks)


def _url_hash(url: str) -> str:
    """Generate short hash for URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _init_db():
    """Initialize SQLite schema if not exists."""
    try:
        with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS docs (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    markdown TEXT,
                    backend TEXT,  -- Extraction backend used (docling_gpu, markitdown, vision, etc.)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    url_hash TEXT,
                    idx INTEGER,
                    heading TEXT,
                    text TEXT,
                    tokens INTEGER,
                    PRIMARY KEY (url_hash, idx)
                );
            """)

            # Migration: Add backend column if it doesn't exist (for existing databases)
            cursor = conn.execute("PRAGMA table_info(docs)")
            columns = [row[1] for row in cursor.fetchall()]
            if "backend" not in columns:
                conn.execute("ALTER TABLE docs ADD COLUMN backend TEXT")
                log.info("DB migration: added 'backend' column to docs table")
    except sqlite3.Error as e:
        log.error("Failed to initialize cache database: %s", e)


def get(url: str) -> Doc | None:
    """Get cached document or None."""
    _init_db()
    h = _url_hash(url)
    
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        row = conn.execute(
            "SELECT url, title, markdown, backend FROM docs WHERE url_hash=?", (h,)
        ).fetchone()
        if not row:
            return None
        
        chunks = conn.execute(
            "SELECT heading, text, tokens FROM chunks WHERE url_hash=? ORDER BY idx", (h,)
        ).fetchall()
        
        return Doc(
            url=row[0],
            title=row[1],
            markdown=row[2],
            chunks=[{"heading": c[0], "text": c[1], "tokens": c[2]} for c in chunks],
            backend=row[3] or "unknown"
        )


def save(url: str, title: str, markdown: str, backend: str = "unknown"):
    """Save document with chunked content."""
    _init_db()
    h = _url_hash(url)
    chunks = _chunk_content(markdown)
    
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO docs (url_hash, url, title, markdown, backend) VALUES (?, ?, ?, ?, ?)",
            (h, url, title, markdown, backend)
        )
        conn.execute("DELETE FROM chunks WHERE url_hash=?", (h,))
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                (h, i, chunk["heading"], chunk["text"], chunk["tokens"])
            )


def delete(url: str):
    """Remove from cache."""
    h = _url_hash(url)
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.execute("DELETE FROM docs WHERE url_hash=?", (h,))
        conn.execute("DELETE FROM chunks WHERE url_hash=?", (h,))


def cache_action(action: str, url: str = "") -> str:
    """Manage cache (stats, list, clear, clear_all)."""
    _init_db()
    
    if action == "stats":
        with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
            doc_count = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            return f"Cached: {doc_count} docs, {chunk_count} chunks"
    
    if action == "list":
        with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
            rows = conn.execute("SELECT url, title FROM docs ORDER BY created_at DESC").fetchall()
            return "\n".join(f"- {r[1] or 'Untitled'}: {r[0][:80]}" for r in rows[:20])
    
    if action == "clear" and url:
        delete(url)
        return f"Cleared: {url[:80]}"
    
    if action == "clear_all":
        with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM docs")
        return "Cache cleared"
    
    return f"Unknown action: {action}"


def format_toc(doc: Doc) -> str:
    """Format document table of contents for LLM."""
    lines = [
        f"# {doc.title or 'Untitled'}",
        f"Source: {doc.url}",
        ""
    ]
    
    total_tokens = sum(c['tokens'] for c in doc.chunks)
    lines.append(f"This document is large ({len(doc.chunks)} sections, ~{total_tokens} tokens).")
    lines.append("This is the table of contents. Use fetch_section(url, section=N) to retrieve a specific section.\n")
    
    for i, c in enumerate(doc.chunks):
        preview = c['text'][:80].replace('\n', ' ')
        lines.append(f"[{i}] {c['heading']}: {preview}... ({c['tokens']}t)")
    
    return safe_text("\n".join(lines))


def format_chunk(doc: Doc, section: int) -> str:
    """Format a specific section for LLM."""
    if section < 0 or section >= len(doc.chunks):
        return f"Error: Section {section} not found. Document has {len(doc.chunks)} sections (0-{len(doc.chunks)-1})."
    
    c = doc.chunks[section]
    nav = []
    if section > 0:
        nav.append(f"fetch_section(url, section={section - 1}) for previous")
    if section < len(doc.chunks) - 1:
        nav.append(f"fetch_section(url, section={section + 1}) for next")
    
    nav_str = f"\n\n[{', '.join(nav)}]" if nav else ""
    return safe_text(f"# {c['heading']}\n\n{c['text']}{nav_str}")


async def fetch_and_cache(url: str) -> Doc | None:
    """
    Fetch document with deployment-flexible extraction.
    
    Automatically selects best available method:
    - Docling GPU (best quality, requires GPU)
    - MarkItDown (works everywhere, CPU-only)
    
    MarkItDown is used for:
    - Audio, YouTube, EPUB, ZIP (Docling doesn't support)
    - Fallback when Docling unavailable (cloud deployment without GPU)
    """
    # Check if MarkItDown should handle this URL (special formats)
    if MARKITDOWN_AVAILABLE and should_use_markitdown(url):
        return await _fetch_with_markitdown(url)
    
    # Check if Docling is available
    docling_available = await config.check_docling()

    # Try Docling first (if available)
    if docling_available:
        doc = await _fetch_with_docling(url)
        if doc:
            return doc
    
    # Fallback to MarkItDown (works without GPU)
    if MARKITDOWN_AVAILABLE:
        log.info("Docling unavailable or failed, using MarkItDown fallback")
        doc = await _fetch_with_markitdown(url)
        if doc:
            return doc
    
    return None


async def _fetch_with_docling(url: str) -> Doc | None:
    """Fetch document using Docling service."""
    url_base = config.docling_url()

    # Build options based on pipeline mode (vlm or standard)
    options = _build_docling_options()

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            resp = await client.post(
                f"{url_base}/v1/convert/source",
                json={
                    "sources": [{"kind": "http", "url": url}],
                    "options": options
                }
            )
            resp.raise_for_status()
            data = resp.json()
            
            # Extract markdown from response
            markdown = ""
            if "document" in data:
                markdown = data["document"].get("md_content", "")
            elif "md_content" in data:
                markdown = data["md_content"]
            
            if not markdown:
                return None
            
            title = extract_title(markdown) or "Untitled"
            backend = "docling_gpu" if config.USE_DOCLING_GPU else "docling_cpu"
            save(url, title, markdown, backend)
            return get(url)
    except Exception as e:
        log.error("Docling error: %s", e)
        return None


async def _fetch_with_markitdown(url: str) -> Doc | None:
    """Fetch document using MarkItDown as fallback."""
    if not MARKITDOWN_AVAILABLE:
        return None
    
    try:
        # MarkItDown sync call (run in thread if needed)
        import asyncio
        result = await asyncio.to_thread(
            md_convert_file, url, use_vision=True
        )
        
        if not result['success']:
            log.error("MarkItDown error: %s", result.get('error', 'Unknown'))
            return None
        
        markdown = result['text_content']
        title = result['title'] or "Untitled"
        
        if not markdown:
            return None
        
        save(url, title, markdown, "markitdown")
        return get(url)
    except Exception as e:
        log.error("MarkItDown error: %s", e)
        return None


def _chunk_content(text: str) -> list[dict]:
    """Split content into heading-aware chunks using configured chunk size."""
    max_chars = config.CHUNK_SIZE_TOKENS * config.CHARS_PER_TOKEN
    
    # Split by headings
    sections = re.split(r'(?m)(?=^#{1,6}\s)', text)
    chunks = []
    current = ""
    current_heading = "Introduction"
    
    for section in sections:
        if not section.strip():
            continue
        
        lines = section.split('\n', 1)
        heading = lines[0].strip('# ') if lines[0].startswith('#') else current_heading
        content = lines[1] if len(lines) > 1 else section
        
        if len(current) + len(content) > max_chars and current:
            chunks.append({
                "heading": current_heading,
                "text": current.strip(),
                "tokens": len(current) // config.CHARS_PER_TOKEN
            })
            current = content
            current_heading = heading
        else:
            current += f"\n\n{heading}\n{content}"
            current_heading = heading
    
    if current.strip():
        chunks.append({
            "heading": current_heading,
            "text": current.strip(),
            "tokens": len(current) // config.CHARS_PER_TOKEN
        })
    
    return chunks or [{"heading": "Content", "text": text, "tokens": len(text) // config.CHARS_PER_TOKEN}]


