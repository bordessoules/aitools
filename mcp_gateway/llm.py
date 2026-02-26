"""
Shared LLM/VLM utilities.

Single source of truth for Vision API parameter building, authentication,
and LLM calls. Used by fetch.py (web extraction) and documents.py (Docling).
"""

import httpx

from . import config
from . import models_config
from .logger import get_logger

log = get_logger("llm")

# Sentinel value for "no real API key needed" (e.g. local LM Studio)
API_KEY_PLACEHOLDER = "not-needed"


def _sampling_params() -> dict:
    """Build sampling params dict from config.

    Returns only the params that are explicitly set (not None),
    so the LLM backend uses its own defaults for unset params.
    """
    params = {}
    if config.VLM_TEMPERATURE is not None:
        params["temperature"] = config.VLM_TEMPERATURE
    if config.VLM_TOP_P is not None:
        params["top_p"] = config.VLM_TOP_P
    if config.VLM_TOP_K is not None:
        params["top_k"] = config.VLM_TOP_K
    if config.VLM_MIN_P is not None:
        params["min_p"] = config.VLM_MIN_P
    if config.VLM_PRESENCE_PENALTY is not None:
        params["presence_penalty"] = config.VLM_PRESENCE_PENALTY
    if config.VLM_REPETITION_PENALTY is not None:
        params["repetition_penalty"] = config.VLM_REPETITION_PENALTY
    return params


def build_vlm_params(max_tokens: int | None = None) -> dict:
    """Build VLM sampling parameters from config.

    Returns a dict with model name and all configured sampling params.
    Used for Docling picture descriptions, VLM pipeline, and LLM calls.

    Args:
        max_tokens: Override max tokens. If None, vLLM uses remaining context.

    Returns:
        Dict with model + sampling parameters
    """
    params = {"model": models_config.get_vision_model()["name"], **_sampling_params()}

    # Only send max_tokens when explicitly requested by the caller.
    # Otherwise vLLM fills remaining context dynamically.
    if max_tokens:
        params["max_completion_tokens"] = max_tokens

    return params


def build_auth_headers() -> dict:
    """Build authorization headers for Vision API calls.

    Returns:
        Dict with Authorization header if API key is configured, empty dict otherwise
    """
    vision = models_config.get_vision_model()
    if vision["key"] and vision["key"] != API_KEY_PLACEHOLDER:
        return {"Authorization": f"Bearer {vision['key']}"}
    return {}


def build_llm_request(messages: list, max_tokens: int | None = None) -> dict:
    """Build a complete LLM chat completion request body.

    Args:
        messages: Chat messages list
        max_tokens: Override max tokens for this request

    Returns:
        Dict ready to POST to /chat/completions
    """
    params = {
        "model": models_config.get_vision_model()["name"],
        "messages": messages,
        "stream": False,
        **_sampling_params(),
    }

    # Only send max_tokens when explicitly requested by the caller.
    # Otherwise vLLM fills remaining context dynamically.
    if max_tokens:
        params["max_tokens"] = max_tokens

    return params


async def call_llm(messages: list, max_tokens: int | None = None) -> str | None:
    """Call Vision API with configured sampling parameters.

    Args:
        messages: Chat messages list (system + user)
        max_tokens: Override max tokens for this request

    Returns:
        LLM response text, or None on failure
    """
    vision = models_config.get_vision_model()
    if not vision["url"]:
        log.warning("No vision model configured in models.yaml")
        return None

    params = build_llm_request(messages, max_tokens)

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_LLM) as client:
            resp = await client.post(
                f"{vision['url']}/chat/completions", json=params
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                log.error("LLM returned no choices: %s", data)
                return None
            return choices[0].get("message", {}).get("content")
    except Exception as e:
        log.error("LLM API error: %s", e)
        return None
