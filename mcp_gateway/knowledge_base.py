"""
Knowledge Base with OpenSearch backend.

Provides semantic search over documents fetched via Docling.
"""

import hashlib
from datetime import datetime

import httpx

from . import config
from .logger import get_logger

log = get_logger("kb")

INDEX_NAME = "mcp_knowledge_base"


def _doc_id(url: str) -> str:
    """Generate unique document ID from URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


async def is_available() -> bool:
    """Check if OpenSearch is available."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OPENSEARCH_URL}/_cluster/health")
            return resp.status_code == 200
    except Exception:
        return False


async def doc_exists(url: str) -> bool:
    """Check if a document with the given URL is already indexed."""
    try:
        doc_id = _doc_id(url)
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.head(
                f"{config.OPENSEARCH_URL}/{INDEX_NAME}/_doc/{doc_id}"
            )
            return resp.status_code == 200
    except Exception:
        return False


async def init_index() -> bool:
    """Initialize the knowledge base index if it doesn't exist."""
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_OPENSEARCH) as client:
            # Check if index exists
            resp = await client.head(f"{config.OPENSEARCH_URL}/{INDEX_NAME}")
            if resp.status_code == 200:
                return True
            
            # Create index with mappings
            index_config = {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "index": {
                        "analysis": {
                            "analyzer": {
                                "default": {
                                    "type": "standard"
                                }
                            }
                        }
                    }
                },
                "mappings": {
                    "properties": {
                        "url": {"type": "keyword"},
                        "title": {"type": "text", "analyzer": "standard"},
                        "content": {"type": "text", "analyzer": "standard"},
                        "chunks": {
                            "type": "nested",
                            "properties": {
                                "heading": {"type": "text"},
                                "text": {"type": "text", "analyzer": "standard"},
                                "tokens": {"type": "integer"}
                            }
                        },
                        "added_at": {"type": "date"},
                        "source_type": {"type": "keyword"}  # pdf, webpage, github, etc.
                    }
                }
            }
            
            resp = await client.put(
                f"{config.OPENSEARCH_URL}/{INDEX_NAME}",
                json=index_config
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        log.error("OpenSearch init error: %s", e)
        return False


async def add_document(url: str, title: str, content: str, chunks: list, source_type: str = "document") -> str:
    """
    Add a document to the knowledge base.
    
    Args:
        url: Document URL (unique identifier)
        title: Document title
        content: Full document content
        chunks: List of chunks with heading, text, tokens
        source_type: Type of source (pdf, webpage, github, etc.)
    
    Returns:
        Success message or error
    """
    if not await is_available():
        return "Error: OpenSearch not available. Start it with: docker compose --profile standard up -d opensearch"
    
    await init_index()
    
    doc_id = _doc_id(url)
    
    doc = {
        "url": url,
        "title": title or "Untitled",
        "content": content[:100000],  # Limit content size
        "chunks": chunks[:100],  # Limit number of chunks
        "added_at": datetime.now().isoformat(),
        "source_type": source_type
    }
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_OPENSEARCH) as client:
            resp = await client.put(
                f"{config.OPENSEARCH_URL}/{INDEX_NAME}/_doc/{doc_id}",
                json=doc
            )
            if resp.status_code in (200, 201):
                return f"Added to knowledge base: {title[:60]}... (ID: {doc_id[:8]})"
            return f"Error adding document: {resp.text}"
    except Exception as e:
        return f"Error: {e}"


async def search(query: str, max_results: int = 5) -> str:
    """
    Search the knowledge base.
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        Search results
    """
    if not await is_available():
        return "Error: OpenSearch not available. Start it with: docker compose --profile standard up -d opensearch"
    
    await init_index()
    
    # Use bool query: flat multi_match on title/content + nested query on chunks
    search_body = {
        "size": max_results,
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^2", "content"],
                            "type": "best_fields",
                            "fuzziness": "AUTO"
                        }
                    },
                    {
                        "nested": {
                            "path": "chunks",
                            "query": {
                                "match": {
                                    "chunks.text": {
                                        "query": query,
                                        "fuzziness": "AUTO"
                                    }
                                }
                            },
                            "inner_hits": {
                                "size": 1,
                                "highlight": {
                                    "fields": {
                                        "chunks.text": {"fragment_size": 200, "number_of_fragments": 1}
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        },
        "highlight": {
            "fields": {
                "content": {"fragment_size": 200, "number_of_fragments": 2}
            }
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_OPENSEARCH) as client:
            resp = await client.post(
                f"{config.OPENSEARCH_URL}/{INDEX_NAME}/_search",
                json=search_body
            )
            resp.raise_for_status()
            data = resp.json()
            
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            
            if not hits:
                return f"No results found for: '{query}'"
            
            lines = [f"Knowledge base search: '{query}' ({total} total results)\n"]
            
            for i, hit in enumerate(hits, 1):
                source = hit["_source"]
                title = source.get("title", "Untitled")
                url = source.get("url", "")
                score = hit.get("_score", 0)
                
                # Get highlighted snippets (from content or nested chunk inner_hits)
                highlights = hit.get("highlight", {})
                snippets = highlights.get("content", [])
                if not snippets:
                    # Check nested inner_hits for chunk highlights
                    inner = hit.get("inner_hits", {}).get("chunks", {}).get("hits", {}).get("hits", [])
                    for ih in inner:
                        chunk_hl = ih.get("highlight", {}).get("chunks.text", [])
                        if chunk_hl:
                            snippets = chunk_hl
                            break
                snippet = snippets[0][:300] if snippets else source.get("content", "")[:300]
                
                lines.append(f"{i}. {title}")
                lines.append(f"   URL: {url}")
                lines.append(f"   Score: {score:.2f}")
                lines.append(f"   {snippet}...\n")
            
            return "\n".join(lines)
            
    except Exception as e:
        return f"Search error: {e}"


async def list_documents(max_results: int = 20) -> str:
    """List all documents in the knowledge base."""
    if not await is_available():
        return "Error: OpenSearch not available."
    
    await init_index()
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_OPENSEARCH) as client:
            resp = await client.post(
                f"{config.OPENSEARCH_URL}/{INDEX_NAME}/_search",
                json={
                    "size": max_results,
                    "sort": [{"added_at": {"order": "desc"}}],
                    "_source": ["url", "title", "added_at", "source_type"]
                }
            )
            resp.raise_for_status()
            data = resp.json()
            
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            
            if not hits:
                return "Knowledge base is empty. Use add_to_knowledge_base(url) to add documents."
            
            lines = [f"Knowledge base: {total} documents\n"]
            
            for hit in hits:
                source = hit["_source"]
                title = source.get("title", "Untitled")
                url = source.get("url", "")[:60]
                doc_type = source.get("source_type", "unknown")
                added = source.get("added_at", "unknown")[:10]
                
                lines.append(f"• [{doc_type}] {title[:50]}")
                lines.append(f"  {url}... (added: {added})")
            
            return "\n".join(lines)
            
    except Exception as e:
        return f"List error: {e}"


async def remove_document(url: str) -> str:
    """Remove a document from the knowledge base."""
    if not await is_available():
        return "Error: OpenSearch not available."
    
    doc_id = _doc_id(url)
    
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_OPENSEARCH) as client:
            resp = await client.delete(f"{config.OPENSEARCH_URL}/{INDEX_NAME}/_doc/{doc_id}")
            if resp.status_code == 200:
                return f"Removed from knowledge base: {url[:60]}..."
            elif resp.status_code == 404:
                return f"Document not found in knowledge base: {url[:60]}..."
            return f"Error: {resp.text}"
    except Exception as e:
        return f"Error: {e}"
