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
"""

import asyncio
import os
import re
import time
from pathlib import Path

from . import config
from .logger import get_logger

log = get_logger("coding_agent")

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    _DOCKER_AVAILABLE = True
except ImportError:
    _DOCKER_AVAILABLE = False


def _goose_model() -> str:
    """Resolve Goose model: GOOSE_MODEL if set, else VISION_MODEL."""
    return config.GOOSE_MODEL or config.VISION_MODEL


def _goose_api_key() -> str:
    """Resolve Goose API key: GOOSE_API_KEY if set, else VISION_API_KEY."""
    from .llm import API_KEY_PLACEHOLDER
    return config.GOOSE_API_KEY or config.VISION_API_KEY or API_KEY_PLACEHOLDER


def _build_command(task: str) -> list[str]:
    """Build Goose CLI command with extensions as flags."""
    cmd = [
        "run",
        "--with-builtin", "developer",
        "--text", task,
    ]

    # Add MCP gateway as Streamable HTTP extension if configured.
    # Goose runs with network_mode="host", so Docker DNS names (e.g. "gateway")
    # won't resolve. Replace with localhost equivalent.
    # Uses /mcp endpoint (Streamable HTTP transport, required by Goose).
    if config.GOOSE_MCP_GATEWAY_URL:
        gw_url = config.GOOSE_MCP_GATEWAY_URL
        gw_url = gw_url.replace("://gateway:", "://localhost:")
        cmd.extend([
            "--with-streamable-http-extension",
            f"{gw_url}/mcp",
        ])

    return cmd


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
# Main task runner
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
    if not _DOCKER_AVAILABLE:
        return "Error: docker Python package not installed. Run: pip install docker"

    workspace_path = Path(workspace) if workspace else config.GOOSE_WORKSPACE

    # Project mode: validate name, set up Gitea repo, use subdirectory
    clone_url = None
    if project:
        err = _validate_project_name(project)
        if err:
            return f"Error: {err}"

        workspace_path = config.GOOSE_WORKSPACE / project

        try:
            from . import gitea
            clone_url = await gitea.ensure_repo(project)
            if not clone_url:
                return f"Error: Could not create/access Gitea repo for project '{project}'. Is Gitea running?"
        except Exception as e:
            log.error("Gitea repo setup failed for '%s': %s", project, e)
            return f"Error setting up project repo: {e}"

    workspace_path.mkdir(parents=True, exist_ok=True)
    # Make workspace writable by Goose (runs as UID 1000 'goose' user)
    os.chmod(workspace_path, 0o777)

    log.info("Running Goose task: %s (workspace: %s, project: %s)", task[:100], workspace_path, project)
    start = time.monotonic()

    try:
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

        # Pre-task: clone or pull if project mode
        if project and clone_url:
            await _git_pre_task(client, clone_url, host_workspace, workspace_path, project)

        # All config via env vars + CLI flags — no config file needed
        output = await asyncio.to_thread(
            client.containers.run,
            image=config.GOOSE_IMAGE,
            command=_build_command(task),
            volumes=volumes,
            working_dir="/workspace",
            network_mode="host",
            environment={
                "GOOSE_PROVIDER": "openai",
                "GOOSE_MODEL": _goose_model(),
                "OPENAI_HOST": config.GOOSE_LLM_URL.rstrip("/v1").rstrip("/"),
                "OPENAI_BASE_PATH": "/v1/chat/completions",
                "OPENAI_API_KEY": _goose_api_key(),
            },
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
        if project and clone_url:
            git_info = "\n" + await _git_post_task(client, host_workspace, task)

        log.info("Goose task completed in %.1fs (%d chars output)", elapsed, len(result))

        project_info = f" | project: {project}" if project else ""
        return f"{result}\n\n[Goose completed in {elapsed:.0f}s | workspace: {workspace_path}{project_info}]{git_info}"

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
