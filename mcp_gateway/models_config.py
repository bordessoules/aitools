"""LLM model configuration — one YAML file per model.

Scans config/models/*.yaml. Each file defines a model:
  - endpoint: LLM API base URL
  - key: API key (default: "not-needed")
  - name: model name sent to endpoint (default: filename)
  - vision: true if this is the vision model
  - sampling: optional per-model sampling params

Each resolved model returns: {"url": ..., "key": ..., "name": ..., "sampling": ...}
"""

import os
from pathlib import Path

import yaml

from .logger import get_logger

log = get_logger("models_config")

_MODELS_DIR = Path(os.getenv("MODELS_DIR", "./config/models"))
_cache: dict[str, dict] | None = None
_vision_name: str | None = None


def _load() -> dict[str, dict]:
    """Load and cache all model YAML files from the models directory."""
    global _cache, _vision_name
    if _cache is not None:
        return _cache

    _cache = {}
    _vision_name = None

    if not _MODELS_DIR.exists():
        log.warning("Models dir not found at %s", _MODELS_DIR)
        return _cache

    for f in sorted(_MODELS_DIR.glob("*.yaml")):
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh) or {}
            name = f.stem
            url = data.get("endpoint", "").rstrip("/")
            _cache[name] = {
                "url": url,
                "key": data.get("key", "not-needed"),
                "name": data.get("name", name),
                "sampling": data.get("sampling", {}),
            }
            if data.get("vision"):
                _vision_name = name
        except Exception as e:
            log.error("Failed to load model %s: %s", f.name, e)

    log.info("Loaded %d model(s) from %s", len(_cache), _MODELS_DIR)
    return _cache


def reload():
    """Force reload all model configs."""
    global _cache
    _cache = None
    return _load()


def resolve(model_name: str) -> dict:
    """Resolve a model name to {url, key, name, sampling}.

    The model_name must match a filename in config/models/ (minus .yaml).
    """
    models = _load()
    model = models.get(model_name)
    if not model:
        raise KeyError(f"Model '{model_name}' not found in {_MODELS_DIR}")
    return model


def get_vision_model() -> dict:
    """Get the vision model (the one with vision: true)."""
    _load()
    if not _vision_name:
        return {"url": "", "key": "not-needed", "name": "", "sampling": {}}
    return _cache[_vision_name]


def list_models() -> dict[str, dict]:
    """Return all configured models."""
    return dict(_load())
