"""
Role-based agent delegation — maps semantic roles to agent profiles.

Roles are defined in config/roles.yaml. Each role specifies:
- description: shown to LLMs via list_roles()
- agent: CLI scaffold name (key in coding_agent.AGENT_PROFILES)
- model: model alias (resolved by coding_agent._resolve_model)
- mcp_ports: list of human-readable port names
- system_prompt: optional instructions for the agent
"""

import re
from pathlib import Path

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
    """Load roles from YAML config. Cached after first load."""
    global _roles_cache
    if _roles_cache is not None:
        return _roles_cache

    roles_file = config.ROLES_FILE
    try:
        import yaml
        with open(roles_file) as f:
            data = yaml.safe_load(f)
        _roles_cache = data.get("roles", {})
        log.info("Loaded %d role(s) from %s", len(_roles_cache), roles_file)
        return _roles_cache
    except FileNotFoundError:
        log.warning("Roles file not found: %s — no roles available", roles_file)
        _roles_cache = {}
        return _roles_cache
    except Exception as e:
        log.error("Failed to load roles from %s: %s", roles_file, e)
        _roles_cache = {}
        return _roles_cache


def reload_roles():
    """Force reload of roles config (for hot-reload scenarios)."""
    global _roles_cache
    _roles_cache = None
    return _load_roles()


def get_role(name: str) -> dict | None:
    """Get a role definition by name. Re-reads YAML each time for hot-reload."""
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
        return "No roles configured. Check config/roles.yaml."

    lines = ["Available roles:\n"]
    for name, role in roles.items():
        lines.append(f"  {name}: {role.get('description', '(no description)')}")
    lines.append("\nUsage: delegate_to_agent(role='<name>', task='<what to do>')")
    lines.append("Optionally pass project='<name>' for a specific Gitea project.")
    return "\n".join(lines)


def slugify_task(task: str, max_words: int = 4, max_len: int = 40) -> str:
    """Generate a project name slug from task text.

    Examples:
        "Research MCP skills for Claude" -> "research-mcp-skills-for"
        "Fix the login bug"             -> "fix-the-login-bug"
        "!!!weird task"                  -> "project-weird-task"
    """
    # Lowercase, keep only alphanumeric and spaces
    cleaned = re.sub(r'[^a-z0-9\s]', '', task.lower())
    words = cleaned.split()[:max_words]
    slug = '-'.join(words)
    # Truncate and clean trailing hyphens
    slug = slug[:max_len].rstrip('-')
    # Ensure valid project name (must start with alphanumeric)
    if not slug or not slug[0].isalnum():
        slug = "project-" + slug.lstrip('-')
    return slug or "project"
