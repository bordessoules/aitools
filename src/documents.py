"""
Document caching with SQLite storage and chunking.

Caches parsed documents to avoid re-downloading and re-parsing.
Provides chunked access for large documents to stay under MCP size limits.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx
import config


@dataclass
class Doc:
    """Cached document with chunks."""
    url: str
    title: str
    markdown: str
    chunks: list[dict]

    def full_text(self) -> str:
        """Get full document text (prefer markdown, fallback to chunks)."""
        return self.markdown or "\n\n".join(c["text"] for c in self.chunks)


def _url_hash(url: str) -> str:
    """Generate short hash for URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _init_db():
    """Initialize SQLite schema if not exists."""
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS docs (
                url_hash TEXT PRIMARY KEY,
                url TEXT,
                title TEXT,
                markdown TEXT,
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


def get(url: str) -> Doc | None:
    """Get cached document or None."""
    _init_db()
    h = _url_hash(url)
    
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        row = conn.execute(
            "SELECT url, title, markdown FROM docs WHERE url_hash=?", (h,)
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
            chunks=[{"heading": c[0], "text": c[1], "tokens": c[2]} for c in chunks]
        )


def save(url: str, title: str, markdown: str):
    """Save document with chunked content."""
    _init_db()
    h = _url_hash(url)
    chunks = _chunk_content(markdown)
    
    with sqlite3.connect(config.CACHE_DIR / "cache.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO docs (url_hash, url, title, markdown) VALUES (?, ?, ?, ?)",
            (h, url, title, markdown)
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
    
    return _safe_text("\n".join(lines))


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
    return _safe_text(f"# {c['heading']}\n\n{c['text']}{nav_str}")


async def fetch_and_cache(url: str) -> Doc | None:
    """Fetch document via Docling and cache it."""
    docling_url = config.DOCLING_GPU_URL if config.USE_DOCLING_GPU else config.DOCLING_URL
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_DOCLING) as client:
            resp = await client.post(
                f"{docling_url}/v1/convert/source",
                json={
                    "sources": [{"kind": "http", "url": url}],
                    "options": {
                        "to_formats": ["md"],
                        "image_export_mode": "placeholder",
                        "do_picture_description": True,
                        "picture_description_api": {
                            "url": f"{config.LMSTUDIO_URL}/chat/completions",
                            "params": {"model": "local", "max_completion_tokens": 500},
                            "timeout": config.TIMEOUT_LLM
                        }
                    }
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
            
            title = _extract_title(markdown) or "Untitled"
            save(url, title, markdown)
            return get(url)
    except Exception as e:
        print(f"Docling error: {e}")
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


def _extract_title(text: str) -> str | None:
    """Extract title from markdown h1 or first non-empty line."""
    match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines[:5]:
        if not line.startswith('<') and len(line) < 100:
            return line
    return None


def _safe_text(text: str) -> str:
    """Remove problematic Unicode for Windows console."""
    replacements = {
        '\U0001f525': '[fire]', '\U0001f680': '[rocket]', '\u2217': '*',
        '\u2013': '-', '\u2014': '--', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2705': '[check]',
        '\u2713': '[check]', '\u2714': '[check]', '\u2717': '[x]',
        '\u2718': '[x]', '\u221a': '[sqrt]', '\u2022': '*',
        '\u2192': '->', '\u2190': '<-', '\u2191': '^', '\u2193': 'v',
    }
    for char, repl in replacements.items():
        text = text.replace(char, repl)
    return text.encode('ascii', 'ignore').decode('ascii')
