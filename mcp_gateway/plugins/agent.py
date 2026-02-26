"""Agent plugin.

Provides tools for delegating tasks to autonomous agents.
"""

from .. import coding_agent
from .. import gitea
from ..logger import get_logger

log = get_logger("plugin.agent")


def register(mcp):
    """Register agent tools with FastMCP."""

    _, active_profile = coding_agent.get_active_profile()
    agent_label = active_profile["label"]

    @mcp.tool()
    async def run_agent(task: str, workspace: str | None = None, project: str | None = None) -> str:
        """
        Run an autonomous agent to complete a task.

        The agent can:
        - Write and modify code files in the workspace
        - Execute shell commands
        - Use tools (search, fetch, knowledge base)

        Best for multi-step tasks:
        - "Fix the authentication bug in auth.py"
        - "Create a REST API for user management"
        - "Research best practices for caching strategies"

        Args:
            task: Natural language description of the task
            workspace: Optional workspace directory path
            project: Optional project name for persistent work. When set,
                     changes are version-controlled. Use the same project name
                     across sessions to resume work.

        Returns:
            Agent output with task results
        """
        return await coding_agent.run_task(task, workspace, project=project)

    # ------------------------------------------------------------------
    # Async tools — fire-and-forget with polling (avoids MCP timeouts)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def delegate_agent(
        task: str,
        workspace: str | None = None,
        project: str | None = None,
        agent: str = "goose",
    ) -> str:
        """
        Start an agent asynchronously (returns immediately with a job ID).

        Returns instantly — use check_agent_job() to wait for results.
        Best for long-running tasks that would otherwise timeout.

        Available agents: use list_agents() to see options.

        Args:
            task: Natural language description of the task
            workspace: Optional workspace directory path
            project: Optional project name for persistent work
            agent: Agent profile to use. See list_agents() for options.

        Returns:
            Job ID to use with check_agent_job()
        """
        return await coding_agent.run_task_async(task, workspace, project=project, agent=agent)

    @mcp.tool()
    async def check_agent_job(job_id: str) -> str:
        """
        Check the status of an async agent job.

        Poll this after calling delegate_agent() to get results.
        Waits 35-60 seconds per call, checking periodically.
        Returns status (starting/running/completed/failed) and output when done.

        Args:
            job_id: The job ID returned by delegate_agent()

        Returns:
            Job status and agent output (if completed)
        """
        return await coding_agent.await_agent(job_id)

    @mcp.tool()
    async def list_agents() -> str:
        """
        List available agent profiles.

        Shows agent types that can be used with delegate_agent(agent=...).

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
        List persistent projects.

        Each project is a git repository with full version history.

        Returns:
            List of projects with names, descriptions, last updated, and web URLs
        """
        if not await gitea.is_available():
            return "Error: Git server is not available. Projects require git server to be running."

        repos = await gitea.list_repos()
        if not repos:
            return "No projects found. Create one with: run_agent(task='...', project='my-project')"

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
    """Check agent backend and git server availability."""
    checks = []
    _, active_profile = coding_agent.get_active_profile()

    # Agent backend availability
    try:
        if await coding_agent.is_available():
            checks.append((f"[OK] Agent ({active_profile['label']})", True))
        else:
            checks.append(("[WARN] Agent: backend not accessible", False))
    except Exception:
        checks.append(("[WARN] Agent: backend not installed", False))

    # Git server
    try:
        if await gitea.is_available():
            ok = await gitea.ensure_setup()
            if ok:
                checks.append(("[OK] Git Server", True))
            else:
                checks.append(("[WARN] Git server reachable but setup failed", False))
        else:
            checks.append(("[INFO] Git server not reachable - project persistence disabled", False))
    except Exception:
        checks.append(("[INFO] Git server not available", False))

    return checks


PLUGIN = {
    "name": "agent",
    "env_var": "ENABLE_CODING_AGENT",
    "default_enabled": False,
    "register": register,
    "health_checks": health_checks,
}
