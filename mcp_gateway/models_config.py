"""Unified LLM model & endpoint configuration.

Loads config/models.yaml which defines:
  - endpoints: named LLM servers (url + key)
  - models: named model configs pointing to an endpoint
  - defaults: which model to use for agent vs vision

Each resolved model returns: {"url": ..., "key": ..., "name": ...}
"""

import os
from pathlib import Path

import yaml

from .logger import get_logger

log = get_logger("models_config")

_MODELS_FILE = Path(os.getenv("MODELS_FILE", "./config/models.yaml"))
_config: dict | None = None


def _load() -> dict:
    """Load and cache models.yaml."""
    global _config
    if _config is not None:
        return _config

    if not _MODELS_FILE.exists():
        log.warning("models.yaml not found at %s — using empty config", _MODELS_FILE)
        _config = {"endpoints": {}, "models": {}, "defaults": {}}
        return _config

    with open(_MODELS_FILE) as f:
        _config = yaml.safe_load(f) or {}

    # Ensure sections exist
    _config.setdefault("endpoints", {})
    _config.setdefault("models", {})
    _config.setdefault("defaults", {})

    log.info(
        "Loaded models.yaml: %d endpoints, %d models",
        len(_config["endpoints"]),
        len(_config["models"]),
    )
    return _config


def reload():
    """Force reload models.yaml (e.g. after editing)."""
    global _config
    _config = None
    return _load()


def resolve(model_name: str) -> dict:
    """Resolve a model name to {url, key, name}.

    Looks up the model in models.yaml, finds its endpoint,
    and returns the full connection info.
    """
    cfg = _load()
    model_cfg = cfg["models"].get(model_name)
    if not model_cfg:
        raise KeyError(f"Model '{model_name}' not found in models.yaml")

    endpoint_name = model_cfg.get("endpoint")
    if not endpoint_name:
        raise KeyError(f"Model '{model_name}' has no endpoint defined")

    endpoint = cfg["endpoints"].get(endpoint_name)
    if not endpoint:
        raise KeyError(
            f"Endpoint '{endpoint_name}' (used by model '{model_name}') "
            f"not found in models.yaml"
        )

    return {
        "url": endpoint["url"],
        "key": endpoint.get("key", "not-needed"),
        "name": model_cfg.get("name", model_name),
    }


def get_agent_model() -> dict:
    """Get the default agent model config: {url, key, name}."""
    cfg = _load()
    model_name = cfg["defaults"].get("agent")
    if not model_name:
        log.warning("No defaults.agent in models.yaml")
        return {"url": "", "key": "not-needed", "name": ""}
    return resolve(model_name)


def get_vision_model() -> dict:
    """Get the default vision model config: {url, key, name}."""
    cfg = _load()
    model_name = cfg["defaults"].get("vision")
    if not model_name:
        log.warning("No defaults.vision in models.yaml")
        return {"url": "", "key": "not-needed", "name": ""}
    return resolve(model_name)


def list_endpoints() -> dict:
    """Return all configured endpoints."""
    return _load()["endpoints"]


def list_models() -> dict:
    """Return all configured models."""
    return _load()["models"]
