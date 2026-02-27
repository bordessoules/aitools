"""
Role-based agent delegation — maps semantic roles to agent profiles.

Agents are defined in config/agents/*.yaml. Each file specifies:
- description: shown to LLMs via list_roles()
- cli: CLI scaffold name (key in coding_agent.CLI_DRIVERS)
- model: model name (resolved by models_config.resolve())
- image: Docker image for the agent container
- mcp_ports: list of human-readable port names
- system_prompt: optional instructions for the agent
- api_key_env: env var name for cloud API keys (optional)
"""

import re
from pathlib import Path

import yaml

from . import config
from .logger import get_logger

log = get_logger("roles")

# Human-readable port aliases -> config.py port numbers
PORT_ALIAS_MAP = {
    "web": config.WEB_PORT,
    "knowledge_base": config.KB_PORT,
    "gitea": config.GITEA_PLUGIN_PORT,
    "sandbox": config.SANDBOX_PORT,
    "agent": config.AGENT_PORT,
}

_roles_cache: dict | None = None


def _load_roles() -> dict:
    """Load agent definitions from config/agents/*.yaml. Cached after first load."""
    global _roles_cache
    if _roles_cache is not None:
        return _roles_cache

    agents_dir = config.AGENTS_DIR
    _roles_cache = {}

    if not agents_dir.exists():
        log.warning("Agents dir not found: %s — no roles available", agents_dir)
        return _roles_cache

    for f in sorted(agents_dir.glob("*.yaml")):
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh) or {}
            _roles_cache[f.stem] = data
        except Exception as e:
            log.error("Failed to load agent %s: %s", f.name, e)

    log.info("Loaded %d role(s) from %s", len(_roles_cache), agents_dir)
    return _roles_cache


def reload_roles():
    """Force reload of agent configs (for hot-reload scenarios)."""
    global _roles_cache
    _roles_cache = None
    return _load_roles()


def get_role(name: str) -> dict | None:
    """Get an agent definition by name. Re-reads YAML each time for hot-reload."""
    reload_roles()
    return _roles_cache.get(name)


def resolve_mcp_ports(role: dict) -> list[int]:
    """Resolve human-readable port names to numeric ports."""
    port_names = role.get("mcp_ports", [])
    ports = []
    for name in port_names:
        port = PORT_ALIAS_MAP.get(name)
        if port is not None:
            ports.append(port)
        else:
            log.warning("Unknown port alias '%s' in role config — skipping", name)
    return ports


def list_role_names() -> list[str]:
    """Return all role names."""
    return list(_load_roles().keys())


def format_roles_list() -> str:
    """Format roles for LLM consumption."""
    roles = _load_roles()
    if not roles:
        return "No roles configured. Add YAML files to config/agents/."

    lines = ["Available roles:\n"]
    for name, role in roles.items():
        lines.append(f"  {name}: {role.get('description', '(no description)')}")
    lines.append("\nUsage: delegate_to_agent(role='<name>', task='<what to do>')")
    lines.append("Optionally pass project='<name>' for a specific Gitea project.")
    return "\n".join(lines)


def slugify_task(task: str, max_words: int = 4, max_len: int = 40) -> str:
    """Generate a project name slug from task text."""
    cleaned = re.sub(r'[^a-z0-9\s]', '', task.lower())
    words = cleaned.split()[:max_words]
    slug = '-'.join(words)
    slug = slug[:max_len].rstrip('-')
    if not slug or not slug[0].isalnum():
        slug = "project-" + slug.lstrip('-')
    return slug or "project"
