"""Coding agent plugin — tools for running coding agents in Docker.

Provides tools:
- run_coding_agent() — synchronous, blocks until done (short tasks)
- delegate_coding_agent() — async, returns job ID instantly (long tasks)
- check_coding_job() — poll async job status
- list_coding_agents() — show available agent profiles
- list_projects() — show Gitea-backed projects

Agent profiles are defined in coding_agent.py. All agents run in Docker.
"""

from .. import coding_agent
from .. import gitea
from ..logger import get_logger

log = get_logger("plugin.agent")


def register(mcp):
    """Register coding agent tools with FastMCP."""

    @mcp.tool()
    async def run_coding_agent(task: str, workspace: str | None = None,
                               project: str | None = None) -> str:
        """
        Run an autonomous coding agent to complete a programming task.
        Blocks until completion. Best for short tasks.

        The agent runs in a Docker container and can:
        - Write and modify code files in the workspace
        - Execute shell commands
        - Use MCP gateway tools (search, fetch, knowledge base)

        Args:
            task: Natural language description of the coding task
            workspace: Optional workspace directory path (default: ./workspace)
            project: Optional project name for persistent work (Gitea-backed git repo)

        Returns:
            Agent output with task results
        """
        return await coding_agent.run_task(task, workspace, project=project)

    @mcp.tool()
    async def delegate_coding_agent(
        task: str,
        agent: str = "goose",
        project: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        branch: str = "main",
        max_turns: int = 0,
        system_prompt: str | None = None,
        workspace: str | None = None,
    ) -> str:
        """
        Start a coding agent asynchronously (returns immediately with a job ID).

        Use check_coding_job() to poll for results. Best for long-running tasks.

        Agents run in isolated Docker containers with workspace mounted.
        Use list_coding_agents() to see available agents.

        Args:
            task: What the agent should do (natural language)
            agent: Agent profile (default: "goose"). See list_coding_agents()
            project: Gitea project name (auto-creates repo if needed)
            owner: Gitea repo owner (alternative to project, for existing repos)
            repo: Gitea repo name (used with owner)
            branch: Git branch to work on (default: main)
            max_turns: Max reasoning steps (0 = agent default)
            system_prompt: Extra instructions prepended to the task
            workspace: Optional workspace directory override

        Returns:
            Job ID to use with check_coding_job()
        """
        return await coding_agent.run_task_async(
            task, workspace=workspace, project=project, agent=agent,
            owner=owner, repo=repo, branch=branch,
            max_turns=max_turns, system_prompt=system_prompt)

    @mcp.tool()
    async def check_coding_job(job_id: str) -> str:
        """
        Check the status of an async coding agent job.

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
        """
        return coding_agent.list_agent_profiles()

    @mcp.tool()
    async def list_projects() -> str:
        """
        List persistent coding projects stored in Gitea.

        Each project is a git repository with full version history.
        """
        if not await gitea.is_available():
            return "Error: Gitea is not available. Projects require Gitea to be running."

        repos = await gitea.list_repos()
        if not repos:
            return "No projects found. Create one with: delegate_coding_agent(task='...', project='my-project')"

        lines = [f"Found {len(repos)} project(s):\n"]
        for r in repos:
            lines.append(f"  - {r['name']}")
            if r.get("description"):
                lines.append(f"    {r['description']}")
            lines.append(f"    Updated: {r.get('updated_at', '')[:10]} | Browse: {r.get('html_url', '')}")
        return "\n".join(lines)


async def health_checks() -> list[tuple[str, bool]]:
    """Check Docker and Gitea availability."""
    checks = []

    try:
        if await coding_agent.is_available():
            checks.append(("[OK] Coding Agent (Docker)", True))
        else:
            checks.append(("[WARN] Coding Agent: Docker not accessible", False))
    except Exception:
        checks.append(("[WARN] Coding Agent: docker package not installed", False))

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
