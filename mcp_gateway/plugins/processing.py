"""Content processing plugin.

Provides process() tool for LLM-based text processing (summarize, extract, translate, analyze).
"""

from .. import processor
from ..logger import get_logger

log = get_logger("plugin.processing")


def register(mcp):
    """Register content processing tools with FastMCP."""

    @mcp.tool()
    async def process(content: str, task: str = "summarize", prompt: str | None = None) -> str:
        """
        Process text content with a local LLM.

        Use this to post-process content retrieved via fetch(). Great for
        summarizing articles, extracting structured data, translating, or
        analyzing sentiment.

        Built-in tasks:
        - "summarize": Concise summary of key points
        - "extract": Extract structured data (names, dates, prices, entities)
        - "translate": Translate content (default: to English)
        - "analyze": Sentiment, tone, themes, and patterns

        You can also provide a custom prompt to override the built-in tasks.

        Args:
            content: Text content to process
            task: Built-in task name ("summarize", "extract", "translate", "analyze")
            prompt: Custom instruction (overrides task if provided)

        Returns:
            Processed content from LLM

        Examples:
            process(article_text, task="summarize")
            process(product_page, prompt="Extract the price and availability")
            process(foreign_text, task="translate")
        """
        return await processor.process(content, task=task, prompt=prompt)


async def health_checks() -> list[tuple[str, bool]]:
    """No dedicated health checks — Vision API is checked by the web plugin."""
    return []


PLUGIN = {
    "name": "processing",
    "env_var": "ENABLE_PROCESSING_TOOLS",
    "default_enabled": True,
    "register": register,
    "health_checks": health_checks,
}
