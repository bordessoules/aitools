"""
Plugin loader for MCP Gateway.

Each plugin is a Python module that exports a PLUGIN dict:
    PLUGIN = {
        "name": "example",
        "env_var": "ENABLE_EXAMPLE",     # env var to enable/disable
        "default_enabled": True,          # default when env var not set
        "register": register,             # callable(mcp) -> None
        "health_checks": health_checks,   # async callable() -> list[(str, bool)]
    }

Plugins can be loaded onto a single FastMCP (all-in-one mode) or onto
separate FastMCP instances (multi-port mode) via load_plugins_multi().
"""

import importlib
import os

from ..logger import get_logger

log = get_logger("plugins")

# Plugin modules, loaded in this order.
# Adding a new plugin = adding one string here + creating the module file.
PLUGIN_MODULES = [
    "web",
    "knowledge",
    "sandbox",
    "agent",
    "gitea_plugin",
]


def _is_enabled(plugin: dict) -> bool:
    """Check if a plugin is enabled via its env var."""
    env_var = plugin.get("env_var")
    if env_var is None:
        return True
    default = "true" if plugin.get("default_enabled", True) else "false"
    return os.getenv(env_var, default).lower() == "true"


def _import_plugins() -> list[tuple[str, dict]]:
    """Import all plugin modules and return (name, PLUGIN dict) for enabled ones."""
    result = []

    for module_name in PLUGIN_MODULES:
        try:
            mod = importlib.import_module(f".{module_name}", package=__name__)
        except ImportError as e:
            log.warning("Plugin '%s' import failed: %s", module_name, e)
            continue

        plugin = getattr(mod, "PLUGIN", None)
        if plugin is None:
            log.warning("Plugin '%s' has no PLUGIN dict, skipping", module_name)
            continue

        if not _is_enabled(plugin):
            log.info("Plugin '%s' disabled via %s", plugin["name"], plugin.get("env_var"))
            continue

        result.append((module_name, plugin))

    return result


def load_plugins(mcp) -> list[dict]:
    """Import enabled plugins and register their tools with a single FastMCP.

    Returns list of loaded PLUGIN dicts (for health checks later).
    """
    loaded = []

    for module_name, plugin in _import_plugins():
        try:
            plugin["register"](mcp)
            loaded.append(plugin)
            log.info("Plugin '%s' loaded", plugin["name"])
        except Exception as e:
            log.error("Plugin '%s' register failed: %s", plugin["name"], e)

    return loaded


def load_plugins_multi(mcp_instances: dict) -> list[dict]:
    """Import enabled plugins, register each onto its own FastMCP instance.

    Args:
        mcp_instances: Dict mapping plugin name -> FastMCP instance.
                       Plugins not in the dict are skipped.

    Returns list of loaded PLUGIN dicts (for health checks later).
    """
    loaded = []

    for module_name, plugin in _import_plugins():
        mcp = mcp_instances.get(module_name)
        if mcp is None:
            log.warning("Plugin '%s' has no FastMCP instance, skipping", module_name)
            continue

        try:
            plugin["register"](mcp)
            loaded.append(plugin)
            log.info("Plugin '%s' loaded on dedicated port", plugin["name"])
        except Exception as e:
            log.error("Plugin '%s' register failed: %s", plugin["name"], e)

    return loaded


async def run_health_checks(loaded_plugins: list[dict]) -> list[tuple[str, bool]]:
    """Run health checks from all loaded plugins.

    Returns combined list of (message, ok) tuples.
    """
    all_checks = []
    for plugin in loaded_plugins:
        check_fn = plugin.get("health_checks")
        if check_fn:
            try:
                checks = await check_fn()
                all_checks.extend(checks)
            except Exception as e:
                all_checks.append(
                    (f"[WARN] {plugin['name']} health check failed: {e}", False)
                )
    return all_checks
