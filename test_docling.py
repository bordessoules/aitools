"""Test Docling PDF parsing."""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from documents import fetch_and_cache


async def main():
    print("Testing Docling with arxiv PDF...")
    print("URL: https://arxiv.org/pdf/1706.03762.pdf")
    
    try:
        doc = await fetch_and_cache('https://arxiv.org/pdf/1706.03762.pdf')
        if doc:
            print(f"Success! Title: {doc.title[:60]}")
            print(f"Chunks: {len(doc.chunks)}")
            if doc.chunks:
                print(f"\nFirst chunk preview:")
                print(doc.chunks[0]['text'][:300])
        else:
            print("Failed to fetch document")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
