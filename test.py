"""Test MCP Gateway tools."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from gateway import search, fetch
from fetch import _fetch_html_as_text


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
        # This will try browser first, then fall back to HTTP
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


async def test_http_fallback_direct():
    """Test HTTP fallback directly (no browser)."""
    print("\n" + "=" * 60)
    print("TEST: _fetch_html_as_text() - Direct HTTP")
    print("=" * 60)
    try:
        result = await _fetch_html_as_text("https://httpbin.org/html")
        print(result[:500])
        print(f"\n[OK] Got {len(result)} chars")
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
    results["http_fallback_direct"] = await test_http_fallback_direct()
    results["fetch_http_fallback"] = await test_fetch_http_fallback()
    results["fetch_github"] = await test_fetch_github()
    
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
