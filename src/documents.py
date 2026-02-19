"""
Document caching with SQLite storage and chunking.

Caches parsed documents to avoid re-downloading and re-parsing.
Provides chunked access for large documents to stay under MCP size limits.

Uses Docling as primary parser (GPU-accelerated), with MarkItDown as fallback
for formats Docling doesn't support (audio, YouTube, EPUB, etc.).
"""

import asyncio
import hashlib
import re
import sqlite3
from dataclasses import dataclass

import httpx
import config
from utils import safe_text, extract_title, build_llm_params

# Import MarkItDown client for fallback support
try:
    from markitdown_client import (
        should_use_markitdown,
        convert_file as md_convert_file,
        MARKITDOWN_AVAILABLE,
    )
except ImportError:
    MARKITDOWN_AVAILABLE = False
    should_use_markitdown = None
    md_convert_file = None


# =============================================================================
# DOCLING CONFIGURATION
# =============================================================================

def _build_auth_headers() -> dict:
    """Build authorization headers for vision API calls."""
    if config.VISION_API_KEY and config.VISION_API_KEY != "not-needed":
        return {"Authorization": f"Bearer {config.VISION_API_KEY}"}
    return {}


def _build_docling_options() -> dict:
    """Build Docling conversion options based on pipeline mode.

    Pipeline modes:
        - "vlm": Granite-258M runs LOCALLY in container (fast, best quality)
                 Picture descriptions optionally use VISION_API_URL
        - "standard": EasyOCR + Tableformer (lighter, no VLM needed)
    """
    options = {
        "to_formats": ["md"],
        "image_export_mode": "placeholder",
        "document_timeout": config.TIMEOUT_DOCLING,
    }

    if config.DOCLING_PIPELINE == "vlm":
        options["pipeline"] = "vlm"
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
                "max_new_tokens": config.DOCLING_VLM_MAX_TOKENS,
            },
        }
    else:
        options["do_ocr"] = True
        options["ocr_engine"] = config.DOCLING_OCR_ENGINE
        options["table_mode"] = "accurate"
        options["do_table_structure"] = True

    # Picture descriptions via Vision API (works with both pipelines)
    if config.DOCLING_DO_PICTURE_DESCRIPTION and config.VISION_API_URL:
        options["do_picture_description"] = True
        options["picture_description_api"] = {
            "url": f"{config.VISION_API_URL}/chat/completions",
            "params": build_llm_params(),
            "timeout": config.TIMEOUT_LLM,
            "headers": _build_auth_headers(),
            "prompt": "Describe this image in detail, including any text, diagrams, charts, or visual elements.",
        }

    return options


# =============================================================================
# DOCUMENT MODEL
# =============================================================================

@dataclass
class Doc:
    """Cached document with chunks."""
    url: str
    title: str
    markdown: str
    chunks: list[dict]
    backend: str = "unknown"

    def full_text(self) -> str:
        """Get full document text (prefer markdown, fallback to chunks)."""
        return self.markdown or "\n\n".join(c["text"] for c in self.chunks)


# =============================================================================
# SQLITE CACHE
# =============================================================================

_db_initialized = False


def _url_hash(url: str) -> str:
    """Generate short hash for URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _init_db():
    """Initialize SQLite schema (runs once)."""
    global _db_initialized
    if _db_initialized:
        return

    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS docs (
                url_hash TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                markdown TEXT,
                backend TEXT,
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

        # Migration: Add backend column if missing (for existing databases)
        cursor = conn.execute("PRAGMA table_info(docs)")
        columns = [row[1] for row in cursor.fetchall()]
        if "backend" not in columns:
            conn.execute("ALTER TABLE docs ADD COLUMN backend TEXT")
            print("[DB Migration] Added 'backend' column to docs table")

    _db_initialized = True


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
            backend=row[3] or "unknown",
        )


def save(url: str, title: str, markdown: str, backend: str = "unknown"):
    """Save document with chunked content."""
    _init_db()
    h = _url_hash(url)
    chunks = _chunk_content(markdown)

    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO docs (url_hash, url, title, markdown, backend) VALUES (?, ?, ?, ?, ?)",
            (h, url, title, markdown, backend),
        )
        conn.execute("DELETE FROM chunks WHERE url_hash=?", (h,))
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                (h, i, chunk["heading"], chunk["text"], chunk["tokens"]),
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


# =============================================================================
# FORMATTING
# =============================================================================

def format_toc(doc: Doc) -> str:
    """Format document table of contents for LLM."""
    lines = [
        f"# {doc.title or 'Untitled'}",
        f"Source: {doc.url}",
        "",
    ]

    total_tokens = sum(c["tokens"] for c in doc.chunks)
    lines.append(f"This document is large ({len(doc.chunks)} sections, ~{total_tokens} tokens).")
    lines.append("This is the table of contents. Use fetch_section(url, section=N) to retrieve a specific section.\n")

    for i, c in enumerate(doc.chunks):
        preview = c["text"][:80].replace("\n", " ")
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


# =============================================================================
# DOCUMENT FETCHING (Docling / MarkItDown)
# =============================================================================

async def fetch_and_cache(url: str) -> Doc | None:
    """Fetch document with deployment-flexible extraction.

    Automatically selects best available method:
    - Docling GPU/CPU (best quality for PDFs/docs)
    - MarkItDown (works everywhere, handles audio/YouTube/EPUB/ZIP)
    """
    # MarkItDown handles special formats Docling doesn't support
    if MARKITDOWN_AVAILABLE and should_use_markitdown and should_use_markitdown(url):
        return await _fetch_with_markitdown(url)

    # Try Docling first
    if await _check_docling_available():
        doc = await _fetch_with_docling(url)
        if doc:
            return doc

    # Fallback to MarkItDown
    if MARKITDOWN_AVAILABLE:
        print("[Doc] Docling unavailable or failed, using MarkItDown fallback...")
        return await _fetch_with_markitdown(url)

    return None


async def _check_docling_available() -> bool:
    """Check if Docling service is available."""
    try:
        docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{docling_url}/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _fetch_with_docling(url: str) -> Doc | None:
    """Fetch document using Docling service.

    Tries sync endpoint first. If Docling returns 404 (common with VLM
    pipeline on newer Docling-serve versions), falls back to async+poll.
    """
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
    options = _build_docling_options()
    payload = {
        "sources": [{"kind": "http", "url": url}],
        "options": options,
    }

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            markdown = await _docling_convert(client, docling_url, payload)

            # If VLM pipeline failed, retry with standard pipeline
            if not markdown and options.get("pipeline") == "vlm":
                print("[Docling] VLM pipeline failed, retrying with standard...")
                fallback_options = {
                    "to_formats": ["md"],
                    "image_export_mode": options.get("image_export_mode", "placeholder"),
                    "document_timeout": config.TIMEOUT_DOCLING,
                    "do_ocr": True,
                    "ocr_engine": config.DOCLING_OCR_ENGINE,
                    "table_mode": "accurate",
                    "do_table_structure": True,
                }
                fallback_payload = {
                    "sources": [{"kind": "http", "url": url}],
                    "options": fallback_options,
                }
                markdown = await _docling_convert(client, docling_url, fallback_payload)

            if not markdown:
                return None

            title = extract_title(markdown)
            backend = "docling_gpu" if config.USE_DOCLING_GPU else "docling_cpu"
            save(url, title, markdown, backend)
            return get(url)
    except Exception as e:
        print(f"[Docling Error] {e}")
        return None


async def _docling_convert(
    client: httpx.AsyncClient, docling_url: str, payload: dict
) -> str | None:
    """Try sync Docling convert, fall back to async+poll if needed."""
    resp = await client.post(f"{docling_url}/v1/convert/source", json=payload)

    if resp.status_code == 404:
        # VLM pipeline may require async — try async+poll
        print("[Docling] Sync 404, trying async+poll...")
        return await _docling_async_poll(client, docling_url, payload)

    resp.raise_for_status()
    data = resp.json()
    return _extract_docling_markdown(data)


def _extract_docling_markdown(data: dict) -> str:
    """Extract markdown from Docling response (handles multiple formats)."""
    if "document" in data:
        return data["document"].get("md_content", "")
    return data.get("md_content", "")


async def _docling_async_poll(
    client: httpx.AsyncClient, docling_url: str, payload: dict
) -> str | None:
    """Submit async Docling job and poll until complete."""
    import asyncio

    resp = await client.post(f"{docling_url}/v1/convert/source/async", json=payload)
    resp.raise_for_status()
    task_data = resp.json()
    task_id = task_data.get("task_id")
    if not task_id:
        print("[Docling] Async submit returned no task_id")
        return None

    print(f"[Docling] Async task: {task_id}")

    # Poll with backoff: 2s, 2s, 4s, 4s, 8s, 8s...
    poll_interval = 2
    elapsed = 0
    while elapsed < config.TIMEOUT_DOCLING:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        status_resp = await client.get(f"{docling_url}/v1/status/poll/{task_id}")
        status_data = status_resp.json()
        status = status_data.get("task_status", "").lower()

        if status in ("success", "completed"):
            print(f"[Docling] Task completed in {elapsed}s")
            result_resp = await client.get(f"{docling_url}/v1/result/{task_id}")
            result_data = result_resp.json()
            return _extract_docling_markdown(result_data)

        if status in ("failure", "error"):
            print(f"[Docling] Task failed: {status_data}")
            return None

        # Increase interval after first few polls
        if elapsed > 10:
            poll_interval = min(poll_interval * 2, 8)

    print(f"[Docling] Async poll timed out after {elapsed}s")
    return None


async def _fetch_with_markitdown(url: str) -> Doc | None:
    """Fetch document using MarkItDown as fallback."""
    if not MARKITDOWN_AVAILABLE or md_convert_file is None:
        return None

    try:
        result = await asyncio.to_thread(md_convert_file, url, use_vision=True)

        if not result["success"]:
            print(f"[MarkItDown Error] {result.get('error', 'Unknown')}")
            return None

        markdown = result["text_content"]
        title = result["title"] or "Untitled"

        if not markdown:
            return None

        save(url, title, markdown, "markitdown")
        return get(url)
    except Exception as e:
        print(f"[MarkItDown Error] {e}")
        return None


# =============================================================================
# CHUNKING
# =============================================================================

def _chunk_content(text: str) -> list[dict]:
    """Split content into heading-aware chunks using configured chunk size."""
    max_chars = config.CHUNK_SIZE_TOKENS * config.CHARS_PER_TOKEN

    sections = re.split(r"(?m)(?=^#{1,6}\s)", text)
    chunks = []
    current = ""
    current_heading = "Introduction"

    for section in sections:
        if not section.strip():
            continue

        lines = section.split("\n", 1)
        heading = lines[0].strip("# ") if lines[0].startswith("#") else current_heading
        content = lines[1] if len(lines) > 1 else section

        if len(current) + len(content) > max_chars and current:
            # Flush current chunk
            chunks.append({
                "heading": current_heading,
                "text": current.strip(),
                "tokens": len(current) // config.CHARS_PER_TOKEN,
            })
            current = content
            current_heading = heading
        else:
            # Merge into current chunk — only set heading if starting fresh
            if not current:
                current_heading = heading
            current += f"\n\n{heading}\n{content}"

    if current.strip():
        chunks.append({
            "heading": current_heading,
            "text": current.strip(),
            "tokens": len(current) // config.CHARS_PER_TOKEN,
        })

    return chunks or [{"heading": "Content", "text": text, "tokens": len(text) // config.CHARS_PER_TOKEN}]
