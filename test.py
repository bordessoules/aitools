"""Test MCP Gateway tools."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from gateway import search, fetch


async def test_search():
    """Test web search."""
    print("=" * 60)
    print("TEST: search('python programming')")
    print("=" * 60)
    try:
        result = await search("python programming")
        print(result[:600])
        print(f"\n[OK] Got {len(result)} chars")
        return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


async def test_fetch_http_fallback():
    """Test fetch with HTTP fallback (when browser unavailable)."""
    print("\n" + "=" * 60)
    print("TEST: fetch('https://httpbin.org/html') - HTTP fallback")
    print("=" * 60)
    try:
        result = await fetch("https://httpbin.org/html")
        print(result[:600])
        print(f"\n[OK] Got {len(result)} chars")
        return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


async def test_fetch_github():
    """Test fetch on GitHub repo (raw file fetch)."""
    print("\n" + "=" * 60)
    print("TEST: fetch('https://github.com/python/cpython') - GitHub README")
    print("=" * 60)
    try:
        result = await fetch("https://github.com/python/cpython")
        print(result[:700])
        print(f"\n[OK] Got {len(result)} chars")
        return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


async def test_kb():
    """Test knowledge base operations."""
    print("\n" + "=" * 60)
    print("TEST: Knowledge Base")
    print("=" * 60)
    try:
        from knowledge_base import is_available
        available = await is_available()
        if not available:
            print("[SKIP] OpenSearch not running (start with: docker compose -f docker-compose.opensearch.yml up -d)")
            return True  # Not a failure, just skipped

        print("OpenSearch is available!")

        from knowledge_base import add_document, search, remove_document
        result = await add_document(
            "https://example.com/test",
            "Test Document",
            "This is a test document about artificial intelligence and machine learning.",
            [{"heading": "Introduction", "text": "AI is cool", "tokens": 10}],
            "test",
        )
        print(f"Add result: {result}")

        result = await search("artificial intelligence")
        print(f"Search result: {result[:300]}...")

        await remove_document("https://example.com/test")

        print("\n[OK] Knowledge base works!")
        return True
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


async def main():
    print("\n" + "=" * 60)
    print("MCP GATEWAY - TOOL TEST")
    print("=" * 60)

    results = {}

    results["search"] = await test_search()
    results["fetch_http_fallback"] = await test_fetch_http_fallback()
    results["fetch_github"] = await test_fetch_github()
    results["knowledge_base"] = await test_kb()

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        status = "[OK]" if passed else "[FAIL]"
        print(f"{status} {name}")

    passed = sum(1 for r in results.values() if r)
    print(f"\nTotal: {passed}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())
