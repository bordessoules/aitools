"""Coding agent plugin with pluggable agent backends.

Provides tools:
- run_coding_agent() — synchronous, blocks until done (good for short tasks)
- delegate_coding_agent() — async, returns job ID instantly (good for long tasks)
- check_coding_job() — poll async job status
- list_coding_agents() — show available agent profiles
- list_projects() — show Gitea-backed projects

The active agent is selected via CODING_AGENT env var (default: goose).
Agent profiles (goose, goose-reviewer) are defined in coding_agent.py.

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
    "goose-reviewer": {
        "label": "Goose Reviewer",
        "image_config": "GOOSE_IMAGE",
        "git_author": "Goose Reviewer",
        "git_email": "goose-reviewer@mcp-gateway",
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

    # ------------------------------------------------------------------
    # Async tools — fire-and-forget with polling (avoids MCP timeouts)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def delegate_coding_agent(
        task: str,
        workspace: str | None = None,
        project: str | None = None,
        agent: str = "goose",
    ) -> str:
        """
        Start a coding agent asynchronously (returns immediately with a job ID).

        Unlike run_coding_agent which blocks until completion, this tool returns
        instantly. Use check_coding_job() to poll for results. Best for long-running
        tasks that would otherwise timeout.

        Available agents:
        - "goose": Full developer agent with local file tools + MCP gateway
        - "goose-reviewer": Review-only agent with MCP gateway tools only (no local files)

        Args:
            task: Natural language description of the task
            workspace: Optional workspace directory path (default: ./workspace)
            project: Optional Gitea-backed project name for persistent work
            agent: Agent profile to use (default: "goose"). Use "goose-reviewer" for code reviews.

        Returns:
            Job ID to use with check_coding_job()
        """
        return await coding_agent.run_task_async(task, workspace, project=project, agent=agent)

    @mcp.tool()
    async def check_coding_job(job_id: str) -> str:
        """
        Check the status of an async coding agent job.

        Poll this after calling delegate_coding_agent() to get results.
        Returns status (starting/running/completed/failed) and output when done.

        Args:
            job_id: The job ID returned by delegate_coding_agent()

        Returns:
            Job status and agent output (if completed)
        """
        return coding_agent.check_job(job_id)

    @mcp.tool()
    async def list_coding_agents() -> str:
        """
        List available coding agent profiles.

        Shows agent types that can be used with delegate_coding_agent(agent=...).

        Returns:
            Agent names and descriptions
        """
        return coding_agent.list_agent_profiles()

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

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
