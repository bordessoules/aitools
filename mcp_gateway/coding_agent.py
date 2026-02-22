"""
Coding agent — runs CLI agents in isolated Docker containers.

All agents run in Docker with:
- Workspace mounted as volume
- network_mode="host" (access local LLM APIs + MCP gateway)
- Optional Gitea-backed project mode (clone before, push after)
- Async job mode with polling (avoids MCP timeout issues)

Supported agents:
- goose: Full coding agent (Goose by Block) — local file tools + MCP gateway
- goose-reviewer: Review-only Goose — MCP gateway only, no local files
- goose-devstral: Goose + devstral via llama.cpp (128k ctx)
- qwen: Qwen Code CLI with local LM Studio
- qwen-cloud: Qwen Code CLI via qwen.ai cloud
- qwen-devstral: Qwen CLI + devstral via llama.cpp
- vibe: Mistral Vibe CLI + devstral
- kimi: Kimi Code CLI (cloud subscription)
"""

import asyncio
import json
import os
import re
import time
import uuid
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


# =============================================================================
# DYNAMIC CONFIG — re-read .env without restart
# =============================================================================

_ENV_FILE = Path(__file__).parent.parent / ".env"


def cfg(key: str, default: str = "") -> str:
    """Read config from .env (re-read each call, no restart needed).

    This lets you change model, host, API key etc. in .env and the next
    job picks it up without restarting the gateway.
    """
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ENV_FILE).get(key, os.getenv(key, default))
    except ImportError:
        return os.getenv(key, default)


# =============================================================================
# GOOSE HELPERS
# =============================================================================

def _goose_model() -> str:
    return cfg("GOOSE_MODEL") or config.VISION_MODEL


def _goose_api_key() -> str:
    from .llm import API_KEY_PLACEHOLDER
    return cfg("GOOSE_API_KEY") or config.VISION_API_KEY or API_KEY_PLACEHOLDER


def _mcp_gateway_url() -> str | None:
    """MCP gateway URL adjusted for network_mode=host containers.

    Docker DNS names (e.g. "gateway") don't resolve with network_mode=host,
    so we replace with localhost. Uses /mcp (Streamable HTTP transport).
    """
    url = cfg("GOOSE_MCP_GATEWAY_URL", config.GOOSE_MCP_GATEWAY_URL)
    if not url:
        return None
    url = url.replace("://gateway:", "://localhost:")
    return f"{url}/mcp"


# =============================================================================
# COMMAND BUILDERS — one per agent profile
# =============================================================================
# Each returns a CLI args list. The Docker image's entrypoint handles the rest.
# max_turns=0 means "don't pass --max-turns" (agent uses its default).

def _build_goose_cmd(task: str, max_turns: int = 0) -> list[str]:
    cmd = [
        "run", "--provider", "openai", "--model", _goose_model(),
        "--with-builtin", "developer",
        "--no-session", "--no-profile",
        "--text", task,
    ]
    gw = _mcp_gateway_url()
    if gw:
        cmd.extend(["--with-streamable-http-extension", gw])
    if max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


def _build_goose_reviewer_cmd(task: str, max_turns: int = 0) -> list[str]:
    """Reviewer: MCP gateway only, no --with-builtin developer."""
    cmd = [
        "run", "--provider", "openai", "--model", _goose_model(),
        "--no-session", "--no-profile", "--quiet",
        "--text", task,
    ]
    gw = _mcp_gateway_url()
    if gw:
        cmd.extend(["--with-streamable-http-extension", gw])
    if max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


def _build_goose_devstral_cmd(task: str, max_turns: int = 0) -> list[str]:
    model = cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf")
    cmd = [
        "run", "--provider", "openai", "--model", model,
        "--with-builtin", "developer",
        "--no-session", "--no-profile",
        "--text", task,
    ]
    gw = _mcp_gateway_url()
    if gw:
        cmd.extend(["--with-streamable-http-extension", gw])
    if max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


def _build_qwen_cmd(task: str, max_turns: int = 0) -> list[str]:
    """Qwen Code CLI with local LLM via OpenAI-compatible endpoint."""
    return ["qwen", "-y", "--auth-type", "openai", "-p", task]


def _build_qwen_cloud_cmd(task: str, max_turns: int = 0) -> list[str]:
    """Qwen Code CLI with qwen.ai cloud. Needs DASHSCOPE_API_KEY."""
    return ["qwen", "-y", "--auth-type", "qwen-oauth", "-p", task]


def _build_qwen_devstral_cmd(task: str, max_turns: int = 0) -> list[str]:
    """Qwen Code CLI with devstral via OpenAI-compatible endpoint."""
    return ["qwen", "-y", "--auth-type", "openai", "-p", task]


def _vibe_config_script() -> str:
    """Shell snippet that writes ~/.vibe/config.toml from env vars.

    Expects VIBE_API_BASE, VIBE_MODEL_NAME and (optionally) VIBE_API_KEY
    to be set in the container environment.
    """
    return """
mkdir -p ~/.vibe
cat > ~/.vibe/config.toml << 'TOMLEOF'
active_model = "local"
enable_telemetry = false
disable_welcome_banner_animation = true

[[providers]]
name = "custom"
api_base = "__VIBE_API_BASE__/v1"
api_key_env_var = "__VIBE_KEY_VAR__"
api_style = "openai"
backend = "generic"

[[models]]
name = "__VIBE_MODEL_NAME__"
provider = "custom"
alias = "local"
TOMLEOF
sed -i "s|__VIBE_API_BASE__|${VIBE_API_BASE}|" ~/.vibe/config.toml
sed -i "s|__VIBE_MODEL_NAME__|${VIBE_MODEL_NAME}|" ~/.vibe/config.toml
sed -i "s|__VIBE_KEY_VAR__|${VIBE_KEY_VAR:-}|" ~/.vibe/config.toml
"""


def _build_vibe_cmd(task: str, max_turns: int = 0) -> list[str]:
    """Mistral Vibe CLI — writes config.toml then runs vibe."""
    vibe_args = [
        "-p", task,
        "--workdir", "/workspace",
        "--enabled-tools", r"re:^(bash|grep|read_file|search_replace|write_file|task|todo|mcp-gateway_gitea_create_issue|mcp-gateway_gitea_create_pr)",
        "--output", "json",
    ]
    if max_turns > 0:
        vibe_args.extend(["--max-turns", str(max_turns)])

    vibe_cmd = "vibe " + " ".join(f"'{a}'" for a in vibe_args)
    return ["/bin/bash", "-c", _vibe_config_script() + vibe_cmd]


def _build_kimi_cmd(task: str, max_turns: int = 0) -> list[str]:
    cmd = [
        "kimi",
        "-p", task,
        "--print",
        "--work-dir", "/workspace",
    ]
    if max_turns > 0:
        cmd.extend(["--max-steps-per-turn", str(max_turns)])
    return cmd


# =============================================================================
# ENVIRONMENT BUILDERS — one per agent family
# =============================================================================

def _build_goose_env() -> dict:
    model = _goose_model()
    host = cfg("GOOSE_LLM_URL", config.GOOSE_LLM_URL).rstrip("/v1").rstrip("/")
    return {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": model,
        "OPENAI_HOST": host,
        "OPENAI_BASE_PATH": "/v1/chat/completions",
        "OPENAI_API_KEY": _goose_api_key(),
        # Prevent goose from calling gpt-4o-mini for planning/titling
        "GOOSE_PLANNER_PROVIDER": "openai",
        "GOOSE_PLANNER_MODEL": model,
    }


def _build_goose_devstral_env() -> dict:
    host = cfg("DEVSTRAL_HOST", "http://host.docker.internal:8083")
    model = cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf")
    return {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": model,
        "OPENAI_HOST": host,
        "OPENAI_BASE_PATH": "/v1/chat/completions",
        "OPENAI_API_KEY": "not-needed",
        "GOOSE_PLANNER_PROVIDER": "openai",
        "GOOSE_PLANNER_MODEL": model,
    }


def _build_qwen_env() -> dict:
    host = cfg("GOOSE_LLM_URL", config.GOOSE_LLM_URL).rstrip("/")
    model = cfg("QWEN_MODEL", cfg("GOOSE_MODEL", ""))
    env = {
        "OPENAI_API_KEY": "not-needed",
        "OPENAI_BASE_URL": host if host.endswith("/v1") else host + "/v1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    if model:
        env["OPENAI_MODEL"] = model
    return env


def _build_qwen_cloud_env() -> dict:
    """Cloud mode — needs DASHSCOPE_API_KEY for headless, or tries OAuth."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    key = cfg("DASHSCOPE_API_KEY", "")
    if key:
        env["DASHSCOPE_API_KEY"] = key
    return env


def _build_qwen_devstral_env() -> dict:
    host = cfg("DEVSTRAL_HOST", "http://host.docker.internal:8083")
    model = cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf")
    return {
        "OPENAI_API_KEY": "not-needed",
        "OPENAI_BASE_URL": host + "/v1",
        "OPENAI_MODEL": model,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _build_vibe_env() -> dict:
    """Vibe with devstral via llama.cpp."""
    host = cfg("DEVSTRAL_HOST", "http://host.docker.internal:8083")
    model = cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf")
    return {
        "VIBE_API_BASE": host,
        "VIBE_MODEL_NAME": model,
        "VIBE_KEY_VAR": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _build_vibe_local_env() -> dict:
    """Vibe with local LM Studio (same backend as goose/qwen)."""
    host = cfg("GOOSE_LLM_URL", "http://bluefin:1234/v1").rstrip("/").rstrip("/v1")
    model = cfg("GOOSE_MODEL", "openai/gpt-oss-20b")
    return {
        "VIBE_API_BASE": host,
        "VIBE_MODEL_NAME": model,
        "VIBE_KEY_VAR": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _build_kimi_env() -> dict:
    """Cloud mode — needs KIMI_API_KEY for headless."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    key = cfg("KIMI_API_KEY", "")
    if key:
        env["KIMI_API_KEY"] = key
    return env


# =============================================================================
# AGENT PROFILES
# =============================================================================

AGENT_PROFILES = {
    "goose": {
        "label": "Goose Developer",
        "description": "Full coding agent — local file tools + MCP gateway. Best for coding tasks.",
        "image": lambda: cfg("GOOSE_IMAGE", config.GOOSE_IMAGE),
        "build_cmd": _build_goose_cmd,
        "build_env": _build_goose_env,
    },
    "goose-reviewer": {
        "label": "Goose Reviewer",
        "description": "Review-only — MCP gateway tools only (gitea, search, fetch). No local files.",
        "image": lambda: cfg("GOOSE_IMAGE", config.GOOSE_IMAGE),
        "build_cmd": _build_goose_reviewer_cmd,
        "build_env": _build_goose_env,
    },
    "goose-devstral": {
        "label": "Goose + Devstral",
        "description": "Goose with devstral via llama.cpp. 128k context, tool-call support.",
        "image": lambda: cfg("GOOSE_IMAGE", config.GOOSE_IMAGE),
        "build_cmd": _build_goose_devstral_cmd,
        "build_env": _build_goose_devstral_env,
    },
    "qwen": {
        "label": "Qwen Code",
        "description": "Qwen Code CLI — local LM Studio. Has file tools + shell.",
        "image": lambda: cfg("QWEN_IMAGE", "mcp-qwen:latest"),
        "build_cmd": _build_qwen_cmd,
        "build_env": _build_qwen_env,
    },
    "qwen-cloud": {
        "label": "Qwen Code (Cloud)",
        "description": "Qwen Code CLI — qwen.ai cloud API. Free tier. Needs DASHSCOPE_API_KEY.",
        "image": lambda: cfg("QWEN_IMAGE", "mcp-qwen:latest"),
        "build_cmd": _build_qwen_cloud_cmd,
        "build_env": _build_qwen_cloud_env,
    },
    "qwen-devstral": {
        "label": "Qwen + Devstral",
        "description": "Qwen CLI with devstral via llama.cpp. 128k context.",
        "image": lambda: cfg("QWEN_IMAGE", "mcp-qwen:latest"),
        "build_cmd": _build_qwen_devstral_cmd,
        "build_env": _build_qwen_devstral_env,
    },
    "vibe": {
        "label": "Mistral Vibe",
        "description": "Mistral Vibe CLI — devstral via local llama.cpp. Mistral's native agent.",
        "image": lambda: cfg("VIBE_IMAGE", "mcp-vibe:latest"),
        "build_cmd": _build_vibe_cmd,
        "build_env": _build_vibe_env,
    },
    "vibe-local": {
        "label": "Vibe + Local LLM",
        "description": "Mistral Vibe CLI with local LM Studio (same model as goose/qwen).",
        "image": lambda: cfg("VIBE_IMAGE", "mcp-vibe:latest"),
        "build_cmd": _build_vibe_cmd,
        "build_env": _build_vibe_local_env,
    },
    "kimi": {
        "label": "Kimi Code",
        "description": "Kimi Code CLI — kimi.com cloud subscription. Has file tools + shell.",
        "image": lambda: cfg("KIMI_IMAGE", "mcp-kimi:latest"),
        "build_cmd": _build_kimi_cmd,
        "build_env": _build_kimi_env,
    },
}


# =============================================================================
# JOB TRACKING — persisted to disk so jobs survive gateway restarts
# =============================================================================

_jobs: dict[str, dict] = {}
_STATE_DIR = config.CACHE_DIR / "agent-jobs"


def _save_job(job_id: str):
    """Persist a single job's state to disk."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    (_STATE_DIR / f"{job_id}.json").write_text(json.dumps(_jobs[job_id], default=str))


def _load_jobs():
    """Load surviving job state from disk on startup."""
    if not _STATE_DIR.exists():
        return
    for f in _STATE_DIR.glob("*.json"):
        try:
            job = json.loads(f.read_text())
            if job["status"] in ("starting", "running"):
                job["status"] = "crashed"
                job["output"] = job.get("output") or "(gateway restarted while job was running)"
                job["finished_at"] = job.get("finished_at") or time.time()
                f.write_text(json.dumps(job, default=str))
            _jobs[f.stem] = job
        except Exception:
            pass
    if _jobs:
        log.info("Loaded %d jobs from disk", len(_jobs))


_load_jobs()


# =============================================================================
# DOCKER HELPERS
# =============================================================================

def _get_client():
    """Create Docker client."""
    return docker.from_env()


def _get_host_workspace_path() -> str | None:
    """Get the host path that maps to /app/workspace.

    When running inside Docker, volume mounts for sibling containers must use
    host paths. We inspect our own mounts to find the host path.
    """
    if not _DOCKER_AVAILABLE:
        return None
    try:
        import socket
        client = docker.from_env()
        container = client.containers.get(socket.gethostname())
        for mount in container.attrs.get("Mounts", []):
            if mount.get("Destination") == "/app/workspace":
                return mount["Source"]
    except Exception:
        pass
    return None


async def is_available() -> bool:
    """Check if Docker is accessible."""
    if not _DOCKER_AVAILABLE:
        return False
    try:
        client = await asyncio.to_thread(_get_client)
        await asyncio.to_thread(client.ping)
        return True
    except Exception:
        return False


# =============================================================================
# GIT HELPERS — clone before agent, push after
# =============================================================================

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


async def _run_git_container(client, image: str, host_workspace: str, script: str) -> str:
    """Run a short-lived container with git for clone/push operations."""
    output = await asyncio.to_thread(
        client.containers.run,
        image=image,
        entrypoint=["/bin/bash", "-c"],
        command=[script],
        volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network_mode="host",
        extra_hosts={"host.docker.internal": "host-gateway"},
        remove=True, stdout=True, stderr=True, detach=False,
    )
    return output.decode("utf-8", errors="replace").strip()


async def _git_pre_task(client, image: str, clone_url: str, branch: str,
                        host_workspace: str, workspace_path: Path, project: str):
    """Clone or pull the project repo before running the agent."""
    git_dir = workspace_path / ".git"
    safe_dir = "git config --global --add safe.directory /workspace && "

    if git_dir.exists():
        log.info("Pulling latest for '%s' on branch '%s'", project, branch)
        script = safe_dir + f"cd /workspace && git pull origin {branch} 2>&1 || true"
    else:
        log.info("Cloning '%s' (branch '%s')", project, branch)
        script = (
            f"git clone --depth 50 {clone_url} /tmp/_repo 2>&1 && "
            "cp -a /tmp/_repo/.git /workspace/.git && "
            + safe_dir
            + "cd /workspace && git checkout -- . 2>/dev/null || true"
        )
        # Checkout non-default branch if needed
        if branch not in ("main", "master"):
            script += (
                f" && git fetch origin +refs/heads/{branch}:refs/remotes/origin/{branch} 2>/dev/null"
                f" && (git checkout -b {branch} origin/{branch} 2>/dev/null || git checkout -b {branch})"
            )

    try:
        await _run_git_container(client, image, host_workspace, script)
    except Exception as e:
        log.warning("Git pre-task failed for '%s': %s", project, e)


async def _git_post_task(client, image: str, host_workspace: str,
                         branch: str, task: str, agent_label: str) -> str:
    """Commit and push changes after agent completes."""
    safe_task = task[:200].replace('"', "'").replace("\\", "\\\\").replace("$", "")
    commit_msg = f"{agent_label.lower()}: {safe_task}"

    script = (
        'git config --global --add safe.directory /workspace && '
        'cd /workspace && '
        f'git config user.name "{agent_label}" && '
        f'git config user.email "{agent_label.lower().replace(" ", "-")}@mcp-gateway" && '
        'git add -A && '
        'if git diff --cached --quiet; then '
        '  echo "NO_CHANGES"; '
        'else '
        f'  git commit -m "{commit_msg}" && '
        f'  git push -u origin {branch} 2>&1 && '
        '  echo "COMMITTED"; '
        'fi'
    )

    try:
        result = await _run_git_container(client, image, host_workspace, script)
        if "COMMITTED" in result:
            return "[Changes committed and pushed to Gitea]"
        return "[No changes to commit]"
    except Exception as e:
        log.warning("Git post-task failed: %s", e)
        return f"[Warning: git commit/push failed: {e}]"


# =============================================================================
# WORKSPACE SETUP
# =============================================================================

async def _setup_workspace(project: str | None, workspace: str | None,
                           owner: str | None, repo: str | None,
                           branch: str) -> tuple[Path, str | None, str | None]:
    """Set up workspace directory and optional Gitea repo.

    Returns (workspace_path, clone_url, error_message).
    """
    workspace_path = Path(workspace) if workspace else config.GOOSE_WORKSPACE

    clone_url = None
    if project:
        err = _validate_project_name(project)
        if err:
            return workspace_path, None, err

        workspace_path = config.GOOSE_WORKSPACE / project

        try:
            from . import gitea
            clone_url = await gitea.ensure_repo(project)
            if not clone_url:
                return workspace_path, None, f"Could not create/access Gitea repo for project '{project}'"
        except Exception as e:
            return workspace_path, None, f"Gitea repo setup failed: {e}"

    elif owner and repo:
        # Direct repo mode (owner/repo from delegate call)
        workspace_path = config.GOOSE_WORKSPACE / f"{owner}-{repo}"
        try:
            from . import gitea
            clone_url = await gitea.ensure_repo(repo)
            if not clone_url:
                return workspace_path, None, f"Could not access Gitea repo '{owner}/{repo}'"
        except Exception as e:
            return workspace_path, None, f"Gitea repo setup failed: {e}"

    workspace_path.mkdir(parents=True, exist_ok=True)
    os.chmod(workspace_path, 0o777)
    return workspace_path, clone_url, None


def _resolve_host_workspace(workspace_path: Path, project: str | None) -> str:
    """Resolve workspace path for Docker volume mount (handles Docker-in-Docker)."""
    host_workspace = _get_host_workspace_path()
    if host_workspace:
        subdir = project or workspace_path.name
        return f"{host_workspace}/{subdir}"
    return str(workspace_path.resolve())


# =============================================================================
# MAIN TASK RUNNER (synchronous — blocks until done)
# =============================================================================

async def run_task(task: str, workspace: str | None = None,
                   project: str | None = None) -> str:
    """Run a coding task synchronously. Blocks until completion."""
    if not _DOCKER_AVAILABLE:
        return "Error: docker Python package not installed. Run: pip install docker"

    profile = AGENT_PROFILES["goose"]
    image = profile["image"]()
    branch = "main"

    workspace_path, clone_url, err = await _setup_workspace(project, workspace, None, None, branch)
    if err:
        return f"Error: {err}"

    start = time.monotonic()
    try:
        client = await asyncio.to_thread(_get_client)
        host_workspace = _resolve_host_workspace(workspace_path, project)

        if project and clone_url:
            await _git_pre_task(client, image, clone_url, branch, host_workspace, workspace_path, project)

        output = await asyncio.to_thread(
            client.containers.run,
            image=image,
            command=profile["build_cmd"](task),
            volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode="host",
            environment=profile["build_env"](),
            extra_hosts={"host.docker.internal": "host-gateway"},
            mem_limit=config.GOOSE_MEMORY_LIMIT,
            remove=True, stdout=True, stderr=True, detach=False,
        )

        elapsed = time.monotonic() - start
        result = output.decode("utf-8", errors="replace")
        if len(result) > 100_000:
            result = result[:100_000] + "\n\n... (truncated at 100k chars)"

        git_info = ""
        if project and clone_url:
            git_info = "\n" + await _git_post_task(
                client, image, host_workspace, branch, task, profile["label"])

        project_info = f" | project: {project}" if project else ""
        return f"{result}\n\n[{profile['label']} completed in {elapsed:.0f}s | workspace: {workspace_path}{project_info}]{git_info}"

    except ContainerError as e:
        elapsed = time.monotonic() - start
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "No error output"
        return f"Agent error (exit {e.exit_status}):\n{stderr}\n\n[Failed after {elapsed:.0f}s]"
    except ImageNotFound:
        return f"Error: Image '{image}' not found. Run: docker pull {image}"
    except APIError as e:
        return f"Error: Docker API error: {e.explanation or str(e)}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# =============================================================================
# ASYNC JOB RUNNER — fire-and-forget with polling
# =============================================================================

async def _run_job_background(job_id: str, task: str, agent_name: str,
                              workspace: str | None, project: str | None,
                              owner: str | None, repo: str | None,
                              branch: str, max_turns: int,
                              system_prompt: str | None):
    """Background coroutine that runs an agent job."""
    job = _jobs[job_id]
    profile = AGENT_PROFILES.get(agent_name, AGENT_PROFILES["goose"])
    image = profile["image"]()

    try:
        job["status"] = "running"
        _save_job(job_id)

        if not _DOCKER_AVAILABLE:
            raise RuntimeError("docker Python package not installed")

        workspace_path, clone_url, err = await _setup_workspace(
            project, workspace, owner, repo, branch)
        if err:
            raise RuntimeError(err)

        client = await asyncio.to_thread(_get_client)
        host_workspace = _resolve_host_workspace(workspace_path, project or (f"{owner}-{repo}" if owner else None))

        # Pre-task: clone/pull if Gitea-backed
        project_label = project or (f"{owner}/{repo}" if owner else None)
        if clone_url:
            await _git_pre_task(client, image, clone_url, branch,
                                host_workspace, workspace_path, project_label or "")

        # Build task prompt with repo context
        full_task = task
        if clone_url:
            repo_name = project_label or "unknown"
            full_task = (
                f"Your working directory is a git clone of {repo_name} on branch '{branch}'.\n"
                f"Edit files directly — changes will be committed and pushed automatically.\n"
            )
            if system_prompt:
                full_task += f"\n{system_prompt}\n"
            full_task += f"\nTask: {task}"
        elif system_prompt:
            full_task = f"{system_prompt}\n\nTask: {task}"

        # Run container (detached so we can wait non-blocking)
        container = await asyncio.to_thread(
            client.containers.run,
            image=image,
            command=profile["build_cmd"](full_task, max_turns),
            volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode="host",
            environment=profile["build_env"](),
            extra_hosts={"host.docker.internal": "host-gateway"},
            mem_limit=config.GOOSE_MEMORY_LIMIT,
            remove=False, stdout=True, stderr=True, detach=True,
        )

        log.info("Job %s: container %s started (%s)", job_id[:8], container.short_id, agent_name)

        # Wait for completion
        exit_info = await asyncio.to_thread(container.wait)
        exit_code = exit_info.get("StatusCode", -1)

        output = await asyncio.to_thread(container.logs, stdout=True, stderr=True)
        result = output.decode("utf-8", errors="replace")

        try:
            await asyncio.to_thread(container.remove)
        except Exception:
            pass

        if len(result) > 100_000:
            result = result[:100_000] + "\n\n... (truncated at 100k chars)"

        # Post-task: commit and push
        git_info = ""
        if clone_url:
            git_info = "\n" + await _git_post_task(
                client, image, host_workspace, branch, task, profile["label"])

        elapsed = time.monotonic() - job["started_at"]
        log.info("Job %s: done in %.0fs (exit=%d)", job_id[:8], elapsed, exit_code)

        project_info = f" | project: {project_label}" if project_label else ""
        job["status"] = "completed" if exit_code == 0 else "failed"
        job["exit_code"] = exit_code
        job["output"] = (
            f"{result}\n\n[{profile['label']} completed in {elapsed:.0f}s"
            f" | workspace: {workspace_path}{project_info}]{git_info}"
        )
        job["finished_at"] = time.monotonic()
        _save_job(job_id)

    except Exception as e:
        log.error("Job %s: error — %s", job_id[:8], e)
        job["status"] = "failed"
        job["output"] = f"{type(e).__name__}: {e}"
        job["finished_at"] = time.monotonic()
        _save_job(job_id)


async def run_task_async(task: str, workspace: str | None = None,
                         project: str | None = None,
                         agent: str = "goose",
                         owner: str | None = None,
                         repo: str | None = None,
                         branch: str = "main",
                         max_turns: int = 0,
                         system_prompt: str | None = None) -> str:
    """Start a coding task asynchronously. Returns immediately with a job ID.

    Args:
        task: Natural language description of the coding task
        workspace: Optional workspace directory path
        project: Optional Gitea project name (auto-creates repo)
        agent: Agent profile (see AGENT_PROFILES)
        owner: Gitea repo owner (alternative to project)
        repo: Gitea repo name (alternative to project)
        branch: Git branch to work on (default: main)
        max_turns: Max reasoning steps (0 = agent default)
        system_prompt: Extra instructions prepended to the task
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
        "project": project or (f"{owner}/{repo}" if owner and repo else None),
        "branch": branch,
        "started_at": time.monotonic(),
        "finished_at": None,
        "exit_code": None,
        "output": None,
    }
    _save_job(job_id)

    asyncio.create_task(_run_job_background(
        job_id, task, agent, workspace, project, owner, repo,
        branch, max_turns, system_prompt))

    project_info = project or (f"{owner}/{repo}" if owner and repo else "(none)")
    return (
        f"Job started: {job_id}\n"
        f"Agent: {profile['label']}\n"
        f"Project: {project_info}\n"
        f"Branch: {branch}\n"
        f"\nUse check_coding_job('{job_id}') to poll for results."
    )


def check_job(job_id: str) -> str:
    """Check the status of an async coding job."""
    if job_id not in _jobs:
        if _jobs:
            recent = sorted(_jobs.items(), key=lambda x: x[1].get("started_at", 0), reverse=True)[:5]
            lines = [f"Job '{job_id}' not found. Recent jobs:"]
            for jid, j in recent:
                lines.append(f"  {jid}: {j['status']} — {j.get('agent_label', j.get('agent', '?'))}")
            return "\n".join(lines)
        return f"Job '{job_id}' not found. No jobs have been started."

    job = _jobs[job_id]
    elapsed = (job.get("finished_at") or time.monotonic()) - job.get("started_at", time.monotonic())

    lines = [
        f"Job: {job_id}",
        f"Status: {job['status']}",
        f"Agent: {job.get('agent_label', job.get('agent', '?'))}",
        f"Task: {job.get('task', '')}",
        f"Elapsed: {elapsed:.0f}s",
    ]

    if job.get("project"):
        lines.append(f"Project: {job['project']}")
    if job.get("branch"):
        lines.append(f"Branch: {job['branch']}")

    if job["status"] in ("completed", "failed", "crashed"):
        if job.get("exit_code") is not None:
            lines.append(f"Exit code: {job['exit_code']}")
        lines.append("")
        lines.append("--- Agent Output ---")
        lines.append(job.get("output", "(no output)"))
    else:
        lines.append("\nStill working... check again in a few seconds.")

    return "\n".join(lines)


def list_agent_profiles() -> str:
    """List available agent profiles."""
    lines = ["Available agent profiles:\n"]
    for name, profile in AGENT_PROFILES.items():
        lines.append(f"  {name}: {profile['description']}")
    return "\n".join(lines)
