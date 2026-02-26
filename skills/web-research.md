---
name: web-research
description: Search the web, fetch pages, and build a knowledge base. Covers search, fetch, fetch_section, add_to_knowledge_base, kb_search, kb_list, kb_remove.
---

# Web Research Skill

Search the web, read content from URLs, and save valuable documents to a persistent knowledge base.

## Gateway Endpoint

The MCP gateway runs at `http://localhost:8000` (or via MCP SSE transport).
All tools are available as MCP tools or via REST at `http://localhost:8000`.

## Workflow

Always follow this order for efficiency:

1. **kb_search(query)** - Check knowledge base first (instant, free)
2. **search(query)** - Web search via SearXNG if KB has nothing
3. **fetch(url)** - Read a page (one-time, not saved)
4. **add_to_knowledge_base(url)** - Save if valuable
5. **process(content, task)** - Summarize/extract/translate if needed

## Tools

### search(query: str) -> str
Web search via self-hosted SearXNG. Returns ~10 results with URLs and snippets.
```bash
# Via curl
curl -s http://localhost:8000/search -d '{"query": "python async patterns"}'
```

### fetch(url: str) -> str
Fetch and extract content from any URL. Handles:
- **Web pages**: Auto-selects best extractor (Vision > Playwright > MarkItDown)
- **PDFs/DOCs**: Parsed via Docling (IBM Granite VLM)
- **Images**: Described via vision model
- **GitHub repos**: Extracts README automatically

For large documents, returns a **table of contents** with section numbers.
Use `fetch_section(url, section=N)` to read specific sections.

### fetch_section(url: str, section: int) -> str
Read a specific section from a large document after `fetch()` returned a TOC.
Section numbers are 0-indexed as shown in the TOC.

### add_to_knowledge_base(url: str) -> str
Save a URL to OpenSearch for persistent retrieval. Uses cached content if available.
**Save**: docs, specs, tutorials, important articles.
**Don't save**: search pages, temporary content, low-quality sources.

### kb_search(query: str) -> str
Full-text search across saved documents. Fast BM25 matching.
Always try this before web search.

### kb_list() -> str
List all saved documents with titles, URLs, and types.

### kb_remove(url: str) -> str
Remove a document from the knowledge base by URL.

## Content Types

| Input | Handler | Extractor |
|-------|---------|-----------|
| `.pdf`, `.docx`, `.pptx` | document | Docling (Granite-258M VLM) |
| `.png`, `.jpg`, `.gif` | image | Vision model (Qwen3-VL) |
| `github.com/org/repo` | webpage | Raw README fetch |
| `arxiv.org/abs/...` | document | Auto-converts to PDF |
| Everything else | webpage | Vision > Playwright > MarkItDown |

## Tips
- GitHub URLs are auto-transformed to raw README URLs
- arXiv abstract URLs are auto-converted to PDF URLs
- Fetched content is cached in SQLite - repeat fetches are instant
- Large docs are chunked (~4000 tokens each) with heading-aware splitting
