"""Centralized logging configuration for MCP Gateway.

Usage in any module:
    from .logger import get_logger
    log = get_logger("fetch")
    log.info("Fetching URL...")
    log.warning("Cache miss")
    log.debug("Raw HTML: %d chars", len(html))

Control via LOG_LEVEL env var (default: INFO).
Set LOG_LEVEL=DEBUG for troubleshooting, LOG_LEVEL=WARNING for quiet production.
"""

import logging
import os
import sys

# Log level from environment (default: INFO)
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Create the main logger for the gateway
logger = logging.getLogger("mcp_gateway")
logger.setLevel(getattr(logging, _log_level, logging.INFO))

# Avoid duplicate handlers if module is reloaded
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(getattr(logging, _log_level, logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger for a specific module.

    Args:
        name: Module name (e.g., 'fetch', 'documents', 'gateway')

    Returns:
        A logger instance inheriting the gateway's configuration
    """
    return logger.getChild(name)
