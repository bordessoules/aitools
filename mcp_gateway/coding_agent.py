"""
Coding agent wrapper using Goose by Block.

Spawns Goose in a Docker container with:
- Task passed via CLI arguments
- Extensions enabled via CLI flags (--with-builtin, --with-streamable-http-extension)
- Workspace mounted as volume
- Access to vLLM for LLM inference
- Access to MCP Gateway for tools (search, fetch, KB)
- Configurable timeout
- Optional project mode: Gitea-backed repos for persistent work
- Async job mode: fire-and-forget with polling (avoids MCP timeout issues)
- Reviewer mode: MCP-gateway-only agent for code reviews (no local file tools)
"""

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import config
from . import models_config
from .logger import get_logger

log = get_logger("coding_agent")

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    _DOCKER_AVAILABLE = True
except ImportError:
    _DOCKER_AVAILABLE = False


def _agent_model() -> dict:
    """Resolve agent model from models.yaml: {url, key, name}."""
    return models_config.get_agent_model()


def _mcp_gateway_url() -> str | None:
    """Get MCP gateway URL adjusted for Goose's network context.

    Goose runs with network_mode="host", so Docker DNS names (e.g. "gateway")
    won't resolve. Replace with localhost equivalent.
    Uses /mcp endpoint (Streamable HTTP transport, required by Goose).
    """
    if not config.GOOSE_MCP_GATEWAY_URL:
        return None
    url = config.GOOSE_MCP_GATEWAY_URL.replace("://gateway:", "://localhost:")
    return f"{url}/mcp"


def _build_command(task: str) -> list[str]:
    """Build Goose CLI command with extensions as flags.

    Uses --provider and --model CLI flags to force the correct model,
    preventing Goose from falling back to its default (gpt-4o-mini).
    """
    cmd = [
        "run",
        "--provider", "openai",
        "--model", _agent_model()["name"],
        "--with-builtin", "developer",
        "--text", task,
    ]

    gw = _mcp_gateway_url()
    if gw:
        cmd.extend(["--with-streamable-http-extension", gw])

    return cmd


def _build_reviewer_command(task: str) -> list[str]:
    """Build Goose CLI command for reviewer mode (no local file tools).

    The reviewer agent only has MCP gateway tools (gitea, search, fetch, KB).
    No --with-builtin developer means no shell, write_file, etc.
    Ideal for code reviews, PR analysis, issue filing.

    Uses --provider and --model CLI flags to force the correct model.
    """
    cmd = [
        "run",
        "--provider", "openai",
        "--model", _agent_model()["name"],
        "--text", task,
    ]

    gw = _mcp_gateway_url()
    if gw:
        cmd.extend(["--with-streamable-http-extension", gw])

    return cmd


# =============================================================================
# AGENT PROFILES — single registry for all agent metadata
# =============================================================================

AGENT_PROFILES = {
    "goose": {
        "label": "Developer Agent",
        "description": "Full coding agent — file editing, shell commands, and gateway tools.",
        "build_command": _build_command,
        "git_author": "Goose Agent",
        "git_email": "goose@mcp-gateway",
    },
    "goose-reviewer": {
        "label": "Review Agent",
        "description": "Review-only agent — search, fetch, and git tools. No local file access.",
        "build_command": _build_reviewer_command,
        "git_author": "Goose Reviewer",
        "git_email": "goose-reviewer@mcp-gateway",
    },
}


def get_active_profile() -> tuple[str, dict]:
    """Get the active agent profile from CODING_AGENT config.

    Returns (name, profile) tuple.
    """
    name = config.CODING_AGENT.lower()
    profile = AGENT_PROFILES.get(name)
    if profile is None:
        supported = ", ".join(AGENT_PROFILES.keys())
        log.warning("Unknown CODING_AGENT '%s' (supported: %s), falling back to goose", name, supported)
        return "goose", AGENT_PROFILES["goose"]
    return name, profile


# =============================================================================
# ASYNC JOB TRACKING
# =============================================================================
# Jobs are stored in memory — ephemeral, no persistence needed.
# Each job tracks status, output, and timing for a background agent run.

_jobs: dict[str, dict] = {}


def _get_client():
    """Create Docker client."""
    return docker.from_env()


def _get_host_workspace_path() -> str | None:
    """Get the host path that maps to /app/workspace by inspecting our own container mounts.

    When running inside Docker, volume mounts for sibling containers must use
    host paths. We inspect our own mounts to find the host path for /app/workspace.
    Returns None if not running in Docker or mount not found.
    """
    if not _DOCKER_AVAILABLE:
        return None
    try:
        client = docker.from_env()
        import socket
        hostname = socket.gethostname()
        container = client.containers.get(hostname)
        for mount in container.attrs.get("Mounts", []):
            if mount.get("Destination") == "/app/workspace":
                return mount["Source"]
    except Exception:
        pass
    return None


async def is_available() -> bool:
    """Check if Docker and Goose image are available."""
    if not _DOCKER_AVAILABLE:
        return False
    try:
        client = await asyncio.to_thread(_get_client)
        await asyncio.to_thread(client.ping)
        try:
            await asyncio.to_thread(client.images.get, config.GOOSE_IMAGE)
            return True
        except Exception:
            log.info("Goose image %s not found locally, will pull on first use", config.GOOSE_IMAGE)
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Git helpers for project mode
# ---------------------------------------------------------------------------

def _validate_project_name(name: str) -> str | None:
    """Validate project name. Returns error message or None if valid."""
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
        return (
            f"Invalid project name '{name}'. "
            "Use letters, numbers, hyphens, underscores. Must start with alphanumeric."
        )
    if len(name) > 64:
        return f"Project name too long ({len(name)} chars, max 64)."
    return None


async def _run_git_container(client, host_workspace: str, script: str) -> str:
    """Run a short-lived container with git for pre/post task operations.

    Overrides entrypoint because the Goose image's default entrypoint is
    the goose binary, not a shell.
    """
    output = await asyncio.to_thread(
        client.containers.run,
        image=config.GOOSE_IMAGE,
        entrypoint=["/bin/bash", "-c"],
        command=[script],
        volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network_mode="host",
        extra_hosts={"host.docker.internal": "host-gateway"},
        remove=True,
        stdout=True,
        stderr=True,
        detach=False,
    )
    return output.decode("utf-8", errors="replace").strip()


async def _git_pre_task(client, clone_url: str, host_workspace: str, workspace_path: Path, project: str):
    """Clone or pull the project repo before running Goose."""
    git_dir = workspace_path / ".git"

    # Git safe.directory needed because workspace owner differs from container user
    safe_dir = "git config --global --add safe.directory /workspace && "

    if git_dir.exists():
        log.info("Pulling latest for project '%s'", project)
        script = safe_dir + "cd /workspace && git pull origin main 2>&1 || true"
    else:
        log.info("Cloning project '%s'", project)
        # Clone into temp dir, then move .git into workspace (dir already exists from mkdir)
        script = (
            f"git clone {clone_url} /tmp/_repo 2>&1 && "
            "cp -a /tmp/_repo/.git /workspace/.git && "
            + safe_dir
            + "cd /workspace && git checkout -- . 2>/dev/null || true"
        )

    try:
        await _run_git_container(client, host_workspace, script)
    except Exception as e:
        log.warning("Git pre-task failed for '%s': %s", project, e)


async def _git_post_task(client, host_workspace: str, task: str) -> str:
    """Commit and push changes after Goose completes."""
    # Sanitize task for commit message
    safe_task = task[:200].replace('"', "'").replace("\\", "\\\\").replace("$", "")
    commit_msg = f"goose: {safe_task}"

    script = (
        'git config --global --add safe.directory /workspace && '
        'cd /workspace && '
        'git config user.name "Goose Agent" && '
        'git config user.email "goose@mcp-gateway" && '
        'git add -A && '
        'if git diff --cached --quiet; then '
        '  echo "NO_CHANGES"; '
        'else '
        f'  git commit -m "{commit_msg}" && '
        '  git push origin main 2>&1 && '
        '  echo "COMMITTED"; '
        'fi'
    )

    try:
        result = await _run_git_container(client, host_workspace, script)
        if "COMMITTED" in result:
            return "[Changes committed and pushed to Gitea]"
        return "[No changes to commit]"
    except Exception as e:
        log.warning("Git post-task failed: %s", e)
        return f"[Warning: git commit/push failed: {e}]"


# ---------------------------------------------------------------------------
# Shared task setup — used by both sync and async runners
# ---------------------------------------------------------------------------

@dataclass
class TaskSetup:
    """All the data needed to run a container, computed once."""
    workspace_path: Path
    host_workspace: str
    clone_url: str | None
    volumes: dict
    environment: dict
    client: object  # docker.DockerClient
    project: str | None


async def _prepare_task(workspace: str | None = None,
                        project: str | None = None) -> TaskSetup:
    """Prepare workspace, Gitea repo, Docker volumes, and environment.

    Shared between run_task() and _run_job_background() to avoid duplication.
    Raises on errors (caller decides how to surface them).
    """
    if not _DOCKER_AVAILABLE:
        raise RuntimeError("docker Python package not installed. Run: pip install docker")

    workspace_path = Path(workspace) if workspace else config.GOOSE_WORKSPACE

    # Project mode: validate name, set up Gitea repo, use subdirectory
    clone_url = None
    if project:
        err = _validate_project_name(project)
        if err:
            raise ValueError(err)

        workspace_path = config.GOOSE_WORKSPACE / project

        from . import gitea
        clone_url = await gitea.ensure_repo(project)
        if not clone_url:
            raise RuntimeError(f"Could not create/access Gitea repo for project '{project}'. Is Gitea running?")

    workspace_path.mkdir(parents=True, exist_ok=True)
    # Make workspace writable by Goose (runs as UID 1000 'goose' user)
    os.chmod(workspace_path, 0o777)

    client = await asyncio.to_thread(_get_client)

    # Docker-in-Docker path resolution:
    # When running inside Docker, volume mounts for sibling containers must
    # use host paths, not container paths. Auto-detect by inspecting our mounts.
    host_workspace = _get_host_workspace_path()
    if host_workspace:
        log.debug("Docker-in-Docker mode: host workspace = %s", host_workspace)
        if project:
            host_workspace = f"{host_workspace}/{project}"
    else:
        host_workspace = str(workspace_path.resolve())

    volumes = {
        host_workspace: {"bind": "/workspace", "mode": "rw"},
    }

    agent = _agent_model()
    environment = {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": agent["name"],
        "OPENAI_HOST": agent["url"].rstrip("/v1").rstrip("/"),
        "OPENAI_BASE_PATH": "/v1/chat/completions",
        "OPENAI_API_KEY": agent["key"],
    }

    return TaskSetup(
        workspace_path=workspace_path,
        host_workspace=host_workspace,
        clone_url=clone_url,
        volumes=volumes,
        environment=environment,
        client=client,
        project=project,
    )


# ---------------------------------------------------------------------------
# Main task runner (synchronous — blocks until done)
# ---------------------------------------------------------------------------

async def run_task(task: str, workspace: str | None = None, project: str | None = None) -> str:
    """Run a coding task using Goose agent in a Docker container.

    Args:
        task: Natural language description of the coding task
        workspace: Optional workspace directory path (defaults to config.GOOSE_WORKSPACE)
        project: Optional project name for Gitea-backed persistent projects

    Returns:
        Goose output with task results, or error message
    """
    try:
        setup = await _prepare_task(workspace, project)
    except Exception as e:
        return f"Error: {e}"

    log.info("Running Goose task: %s (workspace: %s, project: %s)", task[:100], setup.workspace_path, project)
    start = time.monotonic()

    try:
        # Pre-task: clone or pull if project mode
        if setup.project and setup.clone_url:
            await _git_pre_task(setup.client, setup.clone_url, setup.host_workspace, setup.workspace_path, setup.project)

        # All config via env vars + CLI flags — no config file needed
        output = await asyncio.to_thread(
            setup.client.containers.run,
            image=config.GOOSE_IMAGE,
            command=_build_command(task),
            volumes=setup.volumes,
            working_dir="/workspace",
            network_mode="host",
            environment=setup.environment,
            extra_hosts={"host.docker.internal": "host-gateway"},
            mem_limit=config.GOOSE_MEMORY_LIMIT,
            remove=True,
            stdout=True,
            stderr=True,
            detach=False,
        )

        elapsed = time.monotonic() - start
        result = output.decode("utf-8", errors="replace")

        if len(result) > 100_000:
            result = result[:100_000] + "\n\n... (output truncated at 100,000 chars)"

        # Post-task: commit and push if project mode
        git_info = ""
        if setup.project and setup.clone_url:
            git_info = "\n" + await _git_post_task(setup.client, setup.host_workspace, task)

        log.info("Goose task completed in %.1fs (%d chars output)", elapsed, len(result))

        project_info = f" | project: {project}" if project else ""
        return f"{result}\n\n[Goose completed in {elapsed:.0f}s | workspace: {setup.workspace_path}{project_info}]{git_info}"

    except ContainerError as e:
        elapsed = time.monotonic() - start
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "No error output"
        log.warning("Goose task failed (exit %s) in %.1fs", e.exit_status, elapsed)
        return f"Coding agent error (exit code {e.exit_status}):\n{stderr}\n\n[Failed after {elapsed:.0f}s]"

    except ImageNotFound:
        return (
            f"Error: Goose image '{config.GOOSE_IMAGE}' not found. "
            f"Run: docker pull {config.GOOSE_IMAGE}"
        )

    except APIError as e:
        log.error("Docker API error running Goose: %s", e)
        return f"Error: Docker API error: {e.explanation or str(e)}"

    except Exception as e:
        log.error("Unexpected Goose error: %s", e)
        return f"Error: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Async job runner — fire-and-forget with polling
# ---------------------------------------------------------------------------

async def _run_job_background(job_id: str, task: str, workspace: str | None,
                              project: str | None, profile_name: str):
    """Background coroutine that runs an agent job and stores the result."""
    job = _jobs[job_id]

    try:
        profile = AGENT_PROFILES.get(profile_name, AGENT_PROFILES["goose"])
        job["status"] = "running"

        setup = await _prepare_task(workspace, project)

        # Pre-task: clone or pull if project mode
        if setup.project and setup.clone_url:
            await _git_pre_task(setup.client, setup.clone_url, setup.host_workspace, setup.workspace_path, setup.project)

        # Run container in detached mode (non-blocking)
        build_cmd = profile["build_command"]
        container = await asyncio.to_thread(
            setup.client.containers.run,
            image=config.GOOSE_IMAGE,
            command=build_cmd(task),
            volumes=setup.volumes,
            working_dir="/workspace",
            network_mode="host",
            environment=setup.environment,
            extra_hosts={"host.docker.internal": "host-gateway"},
            mem_limit=config.GOOSE_MEMORY_LIMIT,
            remove=False,  # Don't auto-remove — we need to read logs
            stdout=True,
            stderr=True,
            detach=True,  # Non-blocking!
        )

        log.info("Job %s: container %s started", job_id[:8], container.short_id)

        # Wait for container to finish (non-blocking via asyncio)
        exit_info = await asyncio.to_thread(container.wait)
        exit_code = exit_info.get("StatusCode", -1)

        # Read output
        output = await asyncio.to_thread(container.logs, stdout=True, stderr=True)
        result = output.decode("utf-8", errors="replace")

        # Clean up container
        try:
            await asyncio.to_thread(container.remove)
        except Exception:
            pass

        if len(result) > 100_000:
            result = result[:100_000] + "\n\n... (output truncated at 100,000 chars)"

        # Post-task: commit and push if project mode
        git_info = ""
        if setup.project and setup.clone_url:
            git_info = "\n" + await _git_post_task(setup.client, setup.host_workspace, task)

        elapsed = time.monotonic() - job["started_at"]
        log.info("Job %s: done in %.0fs (exit=%d)", job_id[:8], elapsed, exit_code)

        project_info = f" | project: {project}" if project else ""
        job["status"] = "completed" if exit_code == 0 else "failed"
        job["exit_code"] = exit_code
        job["output"] = f"{result}\n\n[{profile['label']} completed in {elapsed:.0f}s | workspace: {setup.workspace_path}{project_info}]{git_info}"
        job["finished_at"] = time.monotonic()

    except Exception as e:
        log.error("Job %s: error — %s", job_id[:8], e)
        job["status"] = "failed"
        job["output"] = f"{type(e).__name__}: {e}"
        job["finished_at"] = time.monotonic()


async def run_task_async(task: str, workspace: str | None = None,
                         project: str | None = None,
                         agent: str = "goose") -> str:
    """Start a coding task asynchronously. Returns immediately with a job ID.

    Use check_job() to poll for results. This avoids MCP timeout issues
    on long-running tasks.

    Args:
        task: Natural language description of the coding task
        workspace: Optional workspace directory path
        project: Optional Gitea-backed project name
        agent: Agent profile to use: "goose" (developer) or "goose-reviewer"

    Returns:
        Job ID and status message
    """
    if agent not in AGENT_PROFILES:
        available = ", ".join(AGENT_PROFILES.keys())
        return f"Error: Unknown agent '{agent}'. Available: {available}"

    job_id = uuid.uuid4().hex[:12]
    profile = AGENT_PROFILES[agent]

    _jobs[job_id] = {
        "status": "starting",
        "agent": agent,
        "agent_label": profile["label"],
        "task": task[:200],
        "project": project,
        "started_at": time.monotonic(),
        "finished_at": None,
        "exit_code": None,
        "output": None,
        "await_count": 0,
    }

    # Fire and forget
    asyncio.create_task(_run_job_background(job_id, task, workspace, project, agent))

    return (
        f"Job started: {job_id}\n"
        f"Agent: {profile['label']}\n"
        f"Project: {project or '(none)'}\n"
        f"\nUse check_agent_job('{job_id}') to poll for results."
    )


def _await_backoff(count: int) -> int:
    """Progressive backoff: 35s, 40s, 45s, 50s... capped at 60s."""
    return min(35 + (count * 5), 60)


async def await_agent(job_id: str) -> str:
    """Wait for an agent job with progressive backoff.

    First call waits 35s, second 40s, third 45s, etc. (capped at 60s).
    Returns the result if the agent finishes during the wait, otherwise
    returns a status update prompting the caller to try again.
    """
    if job_id not in _jobs:
        return check_job(job_id)

    job = _jobs[job_id]

    # Already done? Return full result immediately.
    if job.get("status") in ("completed", "failed"):
        return check_job(job_id)

    # Progressive backoff
    count = job.get("await_count", 0)
    wait_secs = _await_backoff(count)
    job["await_count"] = count + 1

    # Hold connection for wait_secs, checking every 2s
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        if job.get("status") in ("completed", "failed"):
            return check_job(job_id)

    elapsed = int(time.monotonic() - job["started_at"])
    return (
        f"Agent still working ({elapsed}s elapsed). "
        f"Call await_agent('{job_id}') again to check."
    )


def check_job(job_id: str) -> str:
    """Check the status of an async coding job.

    Args:
        job_id: The job ID returned by run_task_async()

    Returns:
        Job status and output (if completed)
    """
    if job_id not in _jobs:
        if _jobs:
            recent = sorted(_jobs.items(), key=lambda x: x[1]["started_at"], reverse=True)[:5]
            lines = [f"Job '{job_id}' not found. Recent jobs:"]
            for jid, j in recent:
                elapsed = time.monotonic() - j["started_at"]
                lines.append(f"  {jid}: {j['status']} — {j['agent_label']} ({elapsed:.0f}s)")
            return "\n".join(lines)
        return f"Job '{job_id}' not found. No jobs have been started."

    job = _jobs[job_id]
    elapsed = time.monotonic() - job["started_at"]

    lines = [
        f"Job: {job_id}",
        f"Status: {job['status']}",
        f"Agent: {job['agent_label']}",
        f"Task: {job['task']}",
        f"Elapsed: {elapsed:.0f}s",
    ]

    if job["status"] in ("completed", "failed"):
        if job["exit_code"] is not None:
            lines.append(f"Exit code: {job['exit_code']}")
        lines.append("")
        lines.append("--- Agent Output ---")
        lines.append(job.get("output", "(no output)"))
    else:
        lines.append(f"\nStill working... check again in a few seconds.")

    return "\n".join(lines)


def list_agent_profiles() -> str:
    """List available agent profiles.

    Returns:
        Agent names and descriptions
    """
    lines = ["Available agent profiles:\n"]
    for name, profile in AGENT_PROFILES.items():
        lines.append(f"  {name}: {profile['description']}")
    return "\n".join(lines)
