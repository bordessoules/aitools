"""Role-based agent delegation plugin.

Provides tools:
- delegate_to_agent(role, task, project) -- fire-and-forget, returns job_id
- check_agent_job(job_id) -- progressive-backoff poll (35s, 40s, 45s...)
- list_roles() -- show available roles
- list_projects() -- show Gitea-backed projects

Roles are defined in config/roles.yaml. Each role maps to a CLI scaffold,
model, and set of MCP tool ports.
"""

from .. import coding_agent
from .. import gitea
from .. import roles
from ..logger import get_logger

log = get_logger("plugin.agent")


def register(mcp):
    """Register role-based agent tools with FastMCP."""

    @mcp.tool()
    async def delegate_to_agent(
        role: str,
        task: str,
        project: str | None = None,
    ) -> str:
        """
        Delegate a task to a specialized agent role.

        The agent runs in an isolated Docker container with access to specific
        tools based on its role. Returns immediately with a job reference.
        Use check_agent_job(job_id) to poll for results.

        Use list_roles() to see available roles and their capabilities.

        Args:
            role: Agent role (e.g., "researcher", "reviewer", "coder").
                  Use list_roles() to see options.
            task: What the agent should do (natural language description)
            project: Optional project name for persistent code in Gitea.
                     If not provided, a name is auto-generated from the task.

        Returns:
            Job reference with instructions to check status.
        """
        role_def = roles.get_role(role)
        if role_def is None:
            available = roles.format_roles_list()
            return f"Unknown role '{role}'.\n\n{available}"

        # Auto-generate project name if not provided
        if project is None:
            project = roles.slugify_task(task)

        # Resolve role config -> agent execution parameters
        agent_name = role_def.get("agent", "vibe")
        model = role_def.get("model", None)
        mcp_ports = roles.resolve_mcp_ports(role_def)
        system_prompt = role_def.get("system_prompt", None)

        log.info("Delegating to role '%s' (agent=%s, model=%s, project=%s)",
                 role, agent_name, model, project)

        job_id = await coding_agent.run_task_fire(
            task=task,
            agent=agent_name,
            model=model,
            mcp_ports_override=mcp_ports,
            project=project,
            system_prompt=system_prompt,
        )

        return (
            f"Agent dispatched.\n"
            f"  Role: {role}\n"
            f"  Project: {project}\n"
            f"  Job: {job_id}\n\n"
            f"Call check_agent_job(job_id='{job_id}') to poll for results."
        )

    @mcp.tool()
    async def check_agent_job(job_id: str) -> str:
        """
        Check on an agent job with progressive backoff.

        Each call holds the connection for an increasing amount of time
        (35s, 40s, 45s, ...) before returning. If the agent finishes
        during the wait, the full result is returned immediately.

        Args:
            job_id: The job ID returned by delegate_to_agent.

        Returns:
            The agent's result if finished, or a status update if still running.
        """
        return await coding_agent.await_agent(job_id)

    @mcp.tool()
    async def list_roles() -> str:
        """
        List available agent roles and their capabilities.

        Each role has a specific focus and set of tools.
        Use the role name with delegate_to_agent(role=..., task=...).
        """
        return roles.format_roles_list()

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
            return (
                "No projects found. Create one with:\n"
                "  delegate_to_agent(role='coder', task='...', project='my-project')"
            )

        lines = [f"Found {len(repos)} project(s):\n"]
        for r in repos:
            lines.append(f"  - {r['name']}")
            if r.get("description"):
                lines.append(f"    {r['description']}")
            lines.append(
                f"    Updated: {r.get('updated_at', '')[:10]}"
                f" | Browse: {r.get('html_url', '')}"
            )
        return "\n".join(lines)


async def health_checks() -> list[tuple[str, bool]]:
    """Check Docker and Gitea availability."""
    checks = []

    try:
        if await coding_agent.is_available():
            checks.append(("[OK] Coding Agent (Docker)", True))
        else:
            checks.append(("[WARN] Agent: backend not accessible", False))
    except Exception:
        checks.append(("[WARN] Agent: backend not installed", False))

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
