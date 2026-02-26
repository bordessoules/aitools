"""
Preload local files into the knowledge base on startup.

Drop PDFs, docs, etc. into the preload folder and they'll be indexed
automatically when the gateway starts.  Already-indexed files are skipped
(idempotent — safe to restart without re-processing).

Supported formats: everything Docling and MarkItDown handle
  - PDFs, DOCX, PPTX, XLSX (via Docling)
  - Audio, EPUB, ZIP (via MarkItDown)
  - Plain text, markdown (direct)
"""

import asyncio
import json
from pathlib import Path

import httpx

from . import config
from . import documents
from . import knowledge_base as kb
from .logger import get_logger
from .utils import extract_title

log = get_logger("preload")

# Extensions we know how to process
SUPPORTED_EXTENSIONS = (
    config.DOCUMENT_EXTENSIONS
    | {'.md', '.txt', '.csv', '.json', '.xml', '.html', '.htm'}
)


def _file_url(path: Path) -> str:
    """Convert local path to a file:// URL used as the document identifier."""
    # Use just the filename so the KB entry stays portable across containers
    return f"file://preload/{path.name}"


async def _is_already_indexed(url: str) -> bool:
    """Check if a document is already in the knowledge base."""
    if not await kb.is_available():
        return False
    return await kb.doc_exists(url)


async def _preload_file(path: Path) -> str | None:
    """Process a single file and add it to the knowledge base.

    Returns a status message or None on skip/failure.
    """
    url = _file_url(path)

    # Skip if already in KB
    if await _is_already_indexed(url):
        log.debug("Already indexed: %s", path.name)
        return None

    doc = None

    # Try Docling first, then MarkItDown fallback
    if await config.check_docling():
        doc = await _fetch_with_docling(path, url)

    if doc is None and documents.MARKITDOWN_AVAILABLE:
        doc = await _fetch_with_markitdown(path, url)

    # Fallback for plain text files
    if doc is None and path.suffix.lower() in {'.md', '.txt', '.csv', '.json', '.xml'}:
        doc = _read_text_file(path, url)

    if doc is None:
        log.warning("Failed to process: %s", path.name)
        return None

    # Add to knowledge base
    if not await kb.is_available():
        log.info("Cached %s locally (KB unavailable, will index on next restart)", path.name)
        return f"  cached: {path.name}"

    source_type = _classify_source_type(path)
    result = await kb.add_document(
        url, doc.title or path.stem, doc.full_text(), doc.chunks, source_type
    )
    log.info("Indexed: %s -> %s", path.name, result)
    return f"  indexed: {path.name}"


async def _fetch_with_docling(path: Path, url: str) -> documents.Doc | None:
    """Send local file to Docling for conversion."""
    base = config.docling_url()
    options = documents._build_docling_options()

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            with open(path, "rb") as f:
                resp = await client.post(
                    f"{base}/v1/convert/file",
                    files={"files": (path.name, f, _mime_type(path))},
                    data={"options": json.dumps(options)},
                )
            resp.raise_for_status()
            data = resp.json()

            markdown = ""
            if "document" in data:
                markdown = data["document"].get("md_content", "")
            elif "md_content" in data:
                markdown = data["md_content"]

            if not markdown or len(markdown) < 50:
                return None

            title = extract_title(markdown) or path.stem
            backend = "docling_gpu" if config.USE_DOCLING_GPU else "docling_cpu"
            documents.save(url, title, markdown, backend)
            return documents.get(url)
    except Exception as e:
        log.debug("Docling failed for %s: %s", path.name, e)
        return None


async def _fetch_with_markitdown(path: Path, url: str) -> documents.Doc | None:
    """Process file with MarkItDown."""
    try:
        from .markitdown_client import convert_file as md_convert_file
        result = await asyncio.to_thread(md_convert_file, str(path), use_vision=True)

        if not result["success"] or not result["text_content"]:
            return None

        markdown = result["text_content"]
        title = result["title"] or path.stem
        documents.save(url, title, markdown, "markitdown")
        return documents.get(url)
    except Exception as e:
        log.debug("MarkItDown failed for %s: %s", path.name, e)
        return None


def _read_text_file(path: Path, url: str) -> documents.Doc | None:
    """Read plain text/markdown files directly."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return None
        title = extract_title(text) or path.stem
        documents.save(url, title, text, "text")
        return documents.get(url)
    except Exception as e:
        log.debug("Text read failed for %s: %s", path.name, e)
        return None


_SOURCE_TYPES = {
    ".pdf": "pdf",
    ".md": "text", ".txt": "text",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".flac": "audio", ".ogg": "audio",
    ".epub": "ebook",
}


def _classify_source_type(path: Path) -> str:
    """Classify file into KB source type."""
    return _SOURCE_TYPES.get(path.suffix.lower(), "document")


def _mime_type(path: Path) -> str:
    """Guess MIME type for Docling upload."""
    ext = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(ext, "application/octet-stream")


async def preload_documents():
    """Scan preload folder and index new files into the knowledge base.

    Called once on gateway startup.  Idempotent — already-indexed files are
    skipped.  Failures are logged but don't block startup.
    """
    if not config.PRELOAD_ON_STARTUP:
        return

    preload_dir = config.PRELOAD_DIR
    if not preload_dir.exists():
        log.debug("Preload dir %s does not exist, skipping", preload_dir)
        return

    files = sorted(
        f for f in preload_dir.rglob("*")
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        log.debug("No files in preload dir")
        return

    log.info("Preloading %d files from %s", len(files), preload_dir)
    results = []

    for f in files:
        try:
            result = await _preload_file(f)
            if result:
                results.append(result)
        except Exception as e:
            log.error("Preload error for %s: %s", f.name, e)

    if results:
        print(f"\n  Preloaded {len(results)} document(s):")
        for r in results:
            print(r)
    else:
        log.info("All %d preload files already indexed", len(files))
