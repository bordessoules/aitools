"""Coding agent plugin with pluggable agent backends.

Provides run_coding_agent() and list_projects() tools.
The active agent is selected via CODING_AGENT env var (default: goose).

Adding a new agent backend:
1. Add an entry to AGENTS dict below
2. Add its image config var to config.py
3. Implement command/env builders (or use NotImplementedError placeholder)
"""

from .. import config
from .. import coding_agent
from .. import gitea
from ..logger import get_logger

log = get_logger("plugin.agent")

# ---------------------------------------------------------------------------
# Agent registry — each entry describes how to run an agent in Docker.
# Currently only goose is fully implemented; others are structural prep.
# ---------------------------------------------------------------------------

AGENTS = {
    "goose": {
        "label": "Goose",
        "image_config": "GOOSE_IMAGE",
        "git_author": "Goose Agent",
        "git_email": "goose@mcp-gateway",
    },
    "aider": {
        "label": "Aider",
        "image_config": "AIDER_IMAGE",
        "git_author": "Aider Agent",
        "git_email": "aider@mcp-gateway",
    },
}


def get_active_agent() -> dict:
    """Get the active agent definition from CODING_AGENT config."""
    name = config.CODING_AGENT.lower()
    agent = AGENTS.get(name)
    if agent is None:
        supported = ", ".join(AGENTS.keys())
        log.warning("Unknown CODING_AGENT '%s' (supported: %s), falling back to goose", name, supported)
        return AGENTS["goose"]
    return agent


def register(mcp):
    """Register coding agent tools with FastMCP."""

    agent = get_active_agent()
    agent_label = agent["label"]

    @mcp.tool()
    async def run_coding_agent(task: str, workspace: str | None = None, project: str | None = None) -> str:
        f"""
        Run an autonomous coding agent ({agent_label}) to complete a programming task.

        Spawns a coding agent in a Docker container that can:
        - Write and modify code files in the workspace
        - Execute shell commands
        - Use MCP gateway tools (search, fetch, knowledge base)

        Best for multi-step coding tasks:
        - "Fix the authentication bug in auth.py"
        - "Create a REST API for user management"
        - "Refactor this module to use async/await"

        NOTE: Requires a capable LLM (7B+ recommended for reliable results).

        Args:
            task: Natural language description of the coding task
            workspace: Optional workspace directory path (default: ./workspace)
            project: Optional project name for persistent work. When set, the workspace
                     is backed by a Gitea git repo. Changes are auto-committed and pushed.
                     Use the same project name across sessions to resume work.
                     Valid characters: letters, numbers, hyphens, underscores.

        Returns:
            Agent output with task results
        """
        return await coding_agent.run_task(task, workspace, project=project)

    @mcp.tool()
    async def list_projects() -> str:
        """
        List persistent coding projects stored in Gitea.

        Shows all projects created via run_coding_agent(project="name").
        Each project is a git repository with full version history.
        Browse code and commits at the Gitea web UI.

        Returns:
            List of projects with names, descriptions, last updated, and web URLs
        """
        if not await gitea.is_available():
            return "Error: Gitea is not available. Projects require Gitea to be running."

        repos = await gitea.list_repos()
        if not repos:
            return "No projects found. Create one with: run_coding_agent(task='...', project='my-project')"

        lines = [f"Found {len(repos)} project(s):\n"]
        for r in repos:
            name = r["name"]
            desc = r.get("description", "")
            updated = r.get("updated_at", "")[:10]
            url = r.get("html_url", "")
            lines.append(f"  - {name}")
            if desc:
                lines.append(f"    {desc}")
            lines.append(f"    Updated: {updated} | Browse: {url}")
        return "\n".join(lines)


async def health_checks() -> list[tuple[str, bool]]:
    """Check Docker/agent image and Gitea availability."""
    checks = []
    agent = get_active_agent()

    # Coding agent Docker availability
    try:
        if await coding_agent.is_available():
            checks.append((f"[OK] Coding Agent ({agent['label']})", True))
        else:
            checks.append(("[WARN] Coding Agent: Docker not accessible", False))
    except Exception:
        checks.append(("[WARN] Coding Agent: docker package not installed", False))

    # Gitea
    try:
        if await gitea.is_available():
            ok = await gitea.ensure_setup()
            if ok:
                checks.append(("[OK] Gitea (Git Server)", True))
            else:
                checks.append(("[WARN] Gitea reachable but setup failed", False))
        else:
            checks.append(("[INFO] Gitea not reachable - project persistence disabled", False))
    except Exception:
        checks.append(("[INFO] Gitea not available", False))

    return checks


PLUGIN = {
    "name": "agent",
    "env_var": "ENABLE_CODING_AGENT",
    "default_enabled": False,
    "register": register,
    "health_checks": health_checks,
}
