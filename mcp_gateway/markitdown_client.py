"""
MarkItDown client with vision API integration.

Falls back to MarkItDown for formats Docling doesn't support:
- Audio (WAV, MP3) - transcription
- YouTube URLs
- EPUB files
- ZIP archives
- And more...

Uses vision API (Qwen3-VL, GPT-4V, etc.) for image descriptions.
"""

import os
import tempfile
from pathlib import Path

import httpx

from . import config
from .utils import extract_title

# Import markitdown - may not be installed
try:
    from markitdown import MarkItDown
    from openai import OpenAI
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False
    OpenAI = None  # type: ignore


def _get_vision_client():
    """Create OpenAI client pointing to vision API endpoint."""
    if not MARKITDOWN_AVAILABLE:
        return None

    if not config.VISION_API_URL:
        return None  # No vision API configured

    base_url = config.VISION_API_URL.rstrip('/')
    api_key = config.VISION_API_KEY

    return OpenAI(
        base_url=base_url,
        api_key=api_key
    )


def should_use_markitdown(url: str) -> bool:
    """Check if URL/file should use MarkItDown instead of Docling."""
    url_lower = url.lower()
    
    # YouTube URLs
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return True
    
    # Audio files
    audio_exts = ('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.wma')
    if any(url_lower.endswith(ext) for ext in audio_exts):
        return True
    
    # EPUB
    if url_lower.endswith('.epub'):
        return True
    
    # ZIP
    if url_lower.endswith('.zip'):
        return True
    
    return False


def _fetch_with_browser_headers(url: str) -> str | None:
    """Fetch URL with browser-like headers to bypass anti-bot protection."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
                f.write(resp.text)
                return f.name
    except Exception:
        return None


def convert_file(file_path: str | Path, use_vision: bool = True) -> dict:
    """
    Convert a file using MarkItDown with optional LM Studio vision.
    
    Args:
        file_path: Path to file or URL
        use_vision: Whether to use Qwen3-VL for image descriptions
    
    Returns:
        Dict with 'text_content', 'title', and metadata
    """
    if not MARKITDOWN_AVAILABLE:
        return {
            'success': False,
            'error': 'MarkItDown not installed. Run: uv pip install "markitdown[all]" openai',
            'text_content': '',
            'title': ''
        }
    
    temp_file = None
    try:
        # Setup MarkItDown with LM Studio for vision
        # LIMITATION: MarkItDown does not support passing sampling parameters
        # (temperature, top_p, etc.) to the LLM client. It uses the LLM server's
        # defaults. Configure sampling in your LLM server (LM Studio, llama.cpp)
        # if you need specific values for MarkItDown image descriptions.
        md_kwargs = {}

        if use_vision:
            client = _get_vision_client()
            if client:
                md_kwargs['llm_client'] = client
                md_kwargs['llm_model'] = config.VISION_MODEL
                md_kwargs['llm_prompt'] = (
                    "Describe this image in detail, focusing on any text, charts, "
                    "diagrams, or visual information that would be relevant for document analysis."
                )
        
        md = MarkItDown(**md_kwargs)
        
        # Handle URLs vs local files
        file_path_str = str(file_path)
        if file_path_str.startswith(('http://', 'https://')):
            # Try with browser headers first (for sites like Medium with anti-bot protection)
            temp_file = _fetch_with_browser_headers(file_path_str)
            if temp_file:
                result = md.convert(temp_file)
            else:
                # Fallback to direct URL
                result = md.convert(file_path_str)
        else:
            result = md.convert(file_path_str)
        
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)
        
        # Extract title from content
        title = extract_title(result.text_content)
        
        return {
            'success': True,
            'text_content': result.text_content,
            'title': title,
            'metadata': getattr(result, 'metadata', {})
        }
        
    except Exception as e:
        # Clean up temp file on error
        if temp_file and os.path.exists(temp_file):
            os.unlink(temp_file)
        return {
            'success': False,
            'error': str(e),
            'text_content': '',
            'title': ''
        }


if __name__ == "__main__":
    # Quick test
    print(f"MarkItDown available: {MARKITDOWN_AVAILABLE}")
    
    if MARKITDOWN_AVAILABLE:
        # Test with a simple URL or file
        test_url = "https://en.wikipedia.org/wiki/Markdown"
        print(f"\nTesting with: {test_url}")
        result = convert_file(test_url, use_vision=False)
        
        if result['success']:
            print(f"Title: {result['title']}")
            print(f"Content preview:\n{result['text_content'][:500]}...")
        else:
            print(f"Error: {result['error']}")
