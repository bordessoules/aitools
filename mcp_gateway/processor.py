"""
LLM-based content processing.

Exposes the local VLM as a general-purpose text processor with built-in tasks
(summarize, extract, translate, analyze) and custom prompt support.

An LLM calling the MCP gateway can use process() to post-process content
retrieved via fetch() — summarize articles, extract entities, translate, etc.

Uses the same Vision API backend as web extraction (fetch.py call_llm).
"""

from . import config
from .llm import call_llm
from .logger import get_logger

log = get_logger("processor")

# Built-in task prompts
TASK_PROMPTS = {
    "summarize": (
        "Summarize the following content concisely. "
        "Focus on the key points and main ideas."
    ),
    "extract": (
        "Extract structured data from the following content. "
        "Look for names, dates, numbers, prices, locations, and other entities. "
        "Format as a clear list or structured output."
    ),
    "translate": (
        "Translate the following content. "
        "If no target language is specified in the user prompt, translate to English."
    ),
    "analyze": (
        "Analyze the following content. "
        "Identify the sentiment, tone, key themes, and any notable patterns or insights."
    ),
}

# Maximum content length to process (50KB)
MAX_CONTENT_CHARS = 50000


async def process(
    content: str,
    task: str = "summarize",
    prompt: str | None = None,
) -> str:
    """
    Process text content with the local LLM.

    Args:
        content: Text content to process
        task: Built-in task name ("summarize", "extract", "translate", "analyze")
        prompt: Custom instruction (overrides task if provided)

    Returns:
        Processed content from LLM, or error message
    """
    if not content:
        return "Error: No content provided to process."

    if not config.VISION_API_URL:
        return "Error: LLM processing unavailable. Set VISION_API_URL in .env to enable."

    # Get the system prompt
    if prompt:
        system_prompt = prompt
    else:
        system_prompt = TASK_PROMPTS.get(task)
        if not system_prompt:
            valid_tasks = ", ".join(TASK_PROMPTS.keys())
            return f"Error: Unknown task '{task}'. Valid tasks: {valid_tasks}"

    # Truncate content if too long
    if len(content) > MAX_CONTENT_CHARS:
        log.info("Content truncated from %d to %d chars", len(content), MAX_CONTENT_CHARS)
        content = content[:MAX_CONTENT_CHARS] + "\n\n[content truncated]"

    log.info("Processing %d chars with task='%s'", len(content), task if not prompt else "custom")

    # Call the LLM API
    result = await call_llm(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        max_tokens=8000,
    )

    if not result:
        log.warning("LLM returned empty response")
        return "Error: LLM returned empty response. Check that your Vision API is running."

    log.info("Processing complete: %d chars output", len(result))
    return result
