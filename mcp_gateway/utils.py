"""
Shared utility functions.

Common helpers used across multiple modules. Avoids duplication of
safe_text (Unicode handling) and extract_title (markdown title extraction).
"""

import re


def safe_text(text: str) -> str:
    """Normalize problematic Unicode characters to ASCII equivalents.

    Maps common Unicode symbols (arrows, quotes, emoji) to readable ASCII
    representations. Falls back to stripping remaining non-ASCII chars.

    Used for Windows console compatibility and clean text output.
    """
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


def extract_title(text: str) -> str:
    """Extract title from markdown content.

    Looks for a markdown h1 heading first, then falls back to the first
    short non-HTML line in the first 5 lines.

    Args:
        text: Markdown or plain text content

    Returns:
        Extracted title, or "Untitled" if none found
    """
    match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    for line in lines[:5]:
        if not line.startswith('<') and len(line) < 100:
            return line
    return "Untitled"
