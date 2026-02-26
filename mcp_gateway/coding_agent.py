"""
Coding agent — runs CLI agents in isolated Docker containers.

All agents run in Docker with:
- Workspace mounted as volume
- network_mode="host" (access local LLM APIs + MCP gateway)
- Optional Gitea-backed project mode (clone before, push after)
- Async job mode with polling (avoids MCP timeout issues)

Agents (1 profile per CLI, model selected at dispatch time):
- goose:          Goose by Block — local file tools + MCP gateway
- goose-reviewer: Goose review-only — MCP gateway tools, no local files
- vibe:           Mistral Vibe CLI + MCP gateway
- qwen:           Qwen Code CLI + MCP gateway (local LLM)
- qwen-cloud:     Qwen Code CLI via qwen.ai cloud API
- kimi:           Kimi Code CLI via kimi.com cloud API + MCP gateway

Models (auto-swapped by llamaswap):
- devstral:       Devstral Small 24B
- qwen-coder:     Qwen3 Coder 30B A3B
- qwen35-code:    Qwen3.5 35B A3B — Thinking · Code (temp 0.6)
- qwen35:         Qwen3.5 35B A3B — Thinking · General (temp 1.0)
- qwen35-fast:    Qwen3.5 35B A3B — No-Think · General (temp 0.7)
- qwen35-reason:  Qwen3.5 35B A3B — No-Think · Reasoning (temp 1.0)
- qwen3-next:     Qwen3 Coder Next 80B MoE
"""

import asyncio
import json
import os
import re
import time
import uuid
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
# MODEL + ENDPOINT HELPERS
# =============================================================================

def _llm_host() -> str:
    """Single LLM endpoint — llamaswap auto-swaps models by name."""
    return cfg("LLM_HOST", "http://host.docker.internal:8085")


def _resolve_model(model: str | None) -> str:
    """Resolve a model alias (e.g. 'devstral') to the full GGUF filename."""
    if not model:
        model = cfg("DEFAULT_MODEL", "devstral")
    aliases = {
        "devstral": cfg("DEVSTRAL_MODEL",
                        "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
        "qwen-coder": cfg("QWEN_CODER_MODEL",
                          "Qwen3-Coder-30B-A3B-Instruct-IQ4_XS-4.20bpw.gguf"),
        # Qwen3.5 profiles — same GGUF, different llamaswap profiles (sampling).
        # Use profile aliases so llamaswap routes to the right config.
        "qwen35-code": "qwen35-code",
        "qwen35": cfg("QWEN35_MODEL",
                      "Qwen3.5-35B-A3B-MXFP4_MOE.gguf"),
        "qwen35-fast": "qwen35-fast",
        "qwen35-reason": "qwen35-reason",
        "qwen3-next": cfg("QWEN3_NEXT_MODEL",
                          "Qwen3-Coder-Next-REAM-MXFP4_MOE.gguf"),
    }
    return aliases.get(model, model)


MODEL_ALIASES = ("devstral", "qwen-coder",
                 "qwen35-code", "qwen35", "qwen35-fast", "qwen35-reason",
                 "qwen3-next")


def _mcp_base_url() -> str:
    """Base MCP gateway URL adjusted for network_mode=host containers.

    Docker DNS names (e.g. "gateway") don't resolve with network_mode=host,
    so we replace with localhost.
    """
    url = cfg("GOOSE_MCP_GATEWAY_URL", config.GOOSE_MCP_GATEWAY_URL)
    if not url:
        return "http://localhost:8000"
    return url.replace("://gateway:", "://localhost:")


def _mcp_urls(ports: list[int]) -> list[str]:
    """Build MCP Streamable HTTP endpoint URLs from port list."""
    base = _mcp_base_url()
    # Extract host without port: http://localhost:8000 -> http://localhost
    host = re.sub(r":\d+$", "", base)
    return [f"{host}:{port}/mcp" for port in ports]


def _mcp_sse_urls(ports: list[int]) -> list[str]:
    """Build MCP SSE endpoint URLs from port list (for clients like Qwen)."""
    return [url.replace("/mcp", "/sse") for url in _mcp_urls(ports)]


# =============================================================================
# COMMAND BUILDERS — one per CLI tool
# =============================================================================
# Each returns a CLI args list. The Docker image's entrypoint handles the rest.
# max_turns=0 means "don't pass --max-turns" (agent uses its default).
# model=None means "use DEFAULT_MODEL from .env" (resolved by _resolve_model).

def _build_goose_cmd(task: str, max_turns: int = 0, model: str | None = None,
                     mcp_ports: list[int] | None = None) -> list[str]:
    """Goose developer — full coding agent with local file tools + MCP gateway."""
    model = _resolve_model(model)
    cmd = [
        "run", "--provider", "openai", "--model", model,
        "--with-builtin", "developer",
        "--no-session", "--no-profile",
        "--text", task,
    ]
    for url in _mcp_urls(mcp_ports or [config.GATEWAY_PORT]):
        cmd.extend(["--with-streamable-http-extension", url])
    if max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


def _build_goose_reviewer_cmd(task: str, max_turns: int = 0, model: str | None = None,
                              mcp_ports: list[int] | None = None) -> list[str]:
    """Goose reviewer — MCP gateway only, no --with-builtin developer."""
    model = _resolve_model(model)
    cmd = [
        "run", "--provider", "openai", "--model", model,
        "--no-session", "--no-profile", "--quiet",
        "--text", task,
    ]
    for url in _mcp_urls(mcp_ports or [config.GATEWAY_PORT]):
        cmd.extend(["--with-streamable-http-extension", url])
    if max_turns > 0:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


def _vibe_config_script(mcp_ports: list[int] | None = None) -> str:
    """Shell snippet that writes ~/.vibe/config.toml from env vars.

    Configures both the LLM provider and the MCP gateway server(s).
    Expects VIBE_API_BASE, VIBE_MODEL_NAME and (optionally) VIBE_KEY_VAR
    to be set in the container environment.
    """
    urls = _mcp_urls(mcp_ports or [config.GATEWAY_PORT])
    mcp_block = ""
    for i, url in enumerate(urls):
        name = f"mcp-gateway-{i}" if len(urls) > 1 else "mcp-gateway"
        mcp_block += f"""
[[mcp_servers]]
name = "{name}"
transport = "streamable-http"
url = "{url}"
"""

    return f"""
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
{mcp_block}TOMLEOF
sed -i "s|__VIBE_API_BASE__|${{VIBE_API_BASE}}|" ~/.vibe/config.toml
sed -i "s|__VIBE_MODEL_NAME__|${{VIBE_MODEL_NAME}}|" ~/.vibe/config.toml
sed -i "s|__VIBE_KEY_VAR__|${{VIBE_KEY_VAR:-}}|" ~/.vibe/config.toml
"""


def _build_vibe_cmd(task: str, max_turns: int = 0, model: str | None = None,
                    mcp_ports: list[int] | None = None) -> list[str]:
    """Mistral Vibe CLI — writes config.toml (LLM + MCP) then runs vibe."""
    vibe_args = [
        "-p", task,
        "--workdir", "/workspace",
        "--enabled-tools", r"re:^(bash|grep|read_file|search_replace|write_file|task|todo|mcp-gateway.*_.*)",
        "--output", "streaming",
    ]
    if max_turns > 0:
        vibe_args.extend(["--max-turns", str(max_turns)])

    # Escape single quotes in args for shell safety (replace ' with '\'' )
    def _sq(s: str) -> str:
        return s.replace("'", "'\\''")
    vibe_cmd = "vibe " + " ".join(f"'{_sq(a)}'" for a in vibe_args)
    return ["/bin/bash", "-c", _vibe_config_script(mcp_ports) + vibe_cmd]


def _qwen_mcp_script(mcp_ports: list[int] | None = None) -> str:
    """Shell snippet that writes ~/.qwen/settings.json with MCP gateway."""
    urls = _mcp_sse_urls(mcp_ports or [config.GATEWAY_PORT])
    if not urls:
        return ""
    servers = {}
    for i, url in enumerate(urls):
        name = f"mcp-gateway-{i}" if len(urls) > 1 else "mcp-gateway"
        servers[name] = {"url": url}
    servers_json = json.dumps({"mcpServers": servers})
    return f"""mkdir -p ~/.qwen
cat > ~/.qwen/settings.json << 'JSONEOF'
{servers_json}
JSONEOF
"""


def _build_qwen_local_cmd(task: str, max_turns: int = 0, model: str | None = None,
                          mcp_ports: list[int] | None = None) -> list[str]:
    """Qwen Code CLI with local LLM + MCP gateway."""
    qwen_cmd = f"qwen -y --auth-type openai -p '{task}'"
    return ["/bin/bash", "-c", _qwen_mcp_script(mcp_ports) + qwen_cmd]


def _build_qwen_cloud_cmd(task: str, max_turns: int = 0, model: str | None = None,
                          mcp_ports: list[int] | None = None) -> list[str]:
    """Qwen Code CLI with qwen.ai cloud. Needs DASHSCOPE_API_KEY."""
    return ["qwen", "-y", "--auth-type", "qwen-oauth", "-p", task]


def _build_kimi_cmd(task: str, max_turns: int = 0, model: str | None = None,
                    mcp_ports: list[int] | None = None) -> list[str]:
    """Kimi Code CLI with MCP gateway tools."""
    urls = _mcp_urls(mcp_ports or [config.GATEWAY_PORT])
    setup = ""
    for i, url in enumerate(urls):
        name = f"mcp-gateway-{i}" if len(urls) > 1 else "mcp-gateway"
        setup += f"kimi mcp add --transport http {name} {url} 2>/dev/null || true\n"
    kimi_args = f"kimi -p '{task}' --print --work-dir /workspace"
    if max_turns > 0:
        kimi_args += f" --max-steps-per-turn {max_turns}"
    return ["/bin/bash", "-c", setup + kimi_args]


# =============================================================================
# ENVIRONMENT BUILDERS — one per CLI family
# =============================================================================
# All local builders accept model=None (resolved to DEFAULT_MODEL).
# Cloud builders ignore the model parameter.

def _build_goose_local_env(model: str | None = None) -> dict:
    """Goose with local LLM via llamaswap."""
    host = _llm_host()
    model = _resolve_model(model)
    return {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": model,
        "OPENAI_HOST": host,
        "OPENAI_BASE_PATH": "/v1/chat/completions",
        "OPENAI_API_KEY": "not-needed",
        "GOOSE_PLANNER_PROVIDER": "openai",
        "GOOSE_PLANNER_MODEL": model,
    }


def _build_vibe_local_env(model: str | None = None) -> dict:
    """Vibe with local LLM via llamaswap."""
    host = _llm_host()
    model = _resolve_model(model)
    return {
        "VIBE_API_BASE": host,
        "VIBE_MODEL_NAME": model,
        "VIBE_KEY_VAR": "",
        "VIBE_MAX_TOKENS": "65536",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _build_qwen_local_env(model: str | None = None) -> dict:
    """Qwen Code with local LLM via llamaswap."""
    host = _llm_host()
    model = _resolve_model(model)
    return {
        "OPENAI_API_KEY": "not-needed",
        "OPENAI_BASE_URL": host + "/v1",
        "OPENAI_MODEL": model,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _build_qwen_cloud_env(model: str | None = None) -> dict:
    """Cloud mode — needs DASHSCOPE_API_KEY for headless, or tries OAuth."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    key = cfg("DASHSCOPE_API_KEY", "")
    if key:
        env["DASHSCOPE_API_KEY"] = key
    return env


def _build_kimi_env(model: str | None = None) -> dict:
    """Cloud mode — needs KIMI_API_KEY for headless."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    key = cfg("KIMI_API_KEY", "")
    if key:
        env["KIMI_API_KEY"] = key
    return env


# =============================================================================
# AGENT PROFILES — 1 per CLI, model selected at dispatch time
# =============================================================================


def _apply_llm_overrides(env: dict, llm_url: str | None, api_key: str | None) -> dict:
    """Override LLM endpoint in agent env dict for runtime routing.

    Allows dispatching agents to any OpenAI-compatible endpoint at runtime,
    without changing gateway config or restarting.
    """
    if not llm_url and not api_key:
        return env

    env = dict(env)  # Don't mutate original

    if llm_url:
        host = llm_url.rstrip("/")
        if host.endswith("/v1"):
            host = host[:-3]
        # Override for each agent CLI's env var naming
        if "OPENAI_HOST" in env:
            env["OPENAI_HOST"] = host
        if "OPENAI_BASE_URL" in env:
            env["OPENAI_BASE_URL"] = host + "/v1"
        if "VIBE_API_BASE" in env:
            env["VIBE_API_BASE"] = host

    if api_key:
        if "OPENAI_API_KEY" in env:
            env["OPENAI_API_KEY"] = api_key
        if "DASHSCOPE_API_KEY" in env:
            env["DASHSCOPE_API_KEY"] = api_key
        if "KIMI_API_KEY" in env:
            env["KIMI_API_KEY"] = api_key

    return env


AGENT_PROFILES = {
    "goose": {
        "label": "Goose Developer",
        "description": "Goose coding agent — local file tools + MCP gateway.",
        "image": lambda: cfg("GOOSE_IMAGE", config.GOOSE_IMAGE),
        "build_cmd": _build_goose_cmd,
        "build_env": _build_goose_local_env,
        "mcp_ports": [config.WEB_PORT, config.KB_PORT, config.SANDBOX_PORT, config.GITEA_PLUGIN_PORT],
    },
    "goose-reviewer": {
        "label": "Goose Reviewer",
        "description": "Goose review-only — MCP gateway tools (search, fetch, gitea). No local files.",
        "image": lambda: cfg("GOOSE_IMAGE", config.GOOSE_IMAGE),
        "build_cmd": _build_goose_reviewer_cmd,
        "build_env": _build_goose_local_env,
        "mcp_ports": [config.WEB_PORT, config.KB_PORT, config.GITEA_PLUGIN_PORT],
    },
    "vibe": {
        "label": "Mistral Vibe",
        "description": "Mistral Vibe CLI — local LLM + MCP gateway tools.",
        "image": lambda: cfg("VIBE_IMAGE", "mcp-vibe:latest"),
        "build_cmd": _build_vibe_cmd,
        "build_env": _build_vibe_local_env,
        "mcp_ports": [config.WEB_PORT],
    },
    "qwen": {
        "label": "Qwen Code",
        "description": "Qwen Code CLI — local LLM + MCP gateway tools.",
        "image": lambda: cfg("QWEN_IMAGE", "mcp-qwen:latest"),
        "build_cmd": _build_qwen_local_cmd,
        "build_env": _build_qwen_local_env,
        "mcp_ports": [config.WEB_PORT],
    },
    "qwen-cloud": {
        "label": "Qwen Code (Cloud)",
        "description": "Qwen Code CLI — qwen.ai cloud API. Free tier. Needs DASHSCOPE_API_KEY.",
        "image": lambda: cfg("QWEN_IMAGE", "mcp-qwen:latest"),
        "build_cmd": _build_qwen_cloud_cmd,
        "build_env": _build_qwen_cloud_env,
        "mcp_ports": [config.WEB_PORT],
    },
    "kimi": {
        "label": "Kimi Code",
        "description": "Kimi Code CLI — kimi.com cloud + MCP gateway tools. Needs KIMI_API_KEY.",
        "image": lambda: cfg("KIMI_IMAGE", "mcp-kimi:latest"),
        "build_cmd": _build_kimi_cmd,
        "build_env": _build_kimi_env,
        "mcp_ports": [config.WEB_PORT],
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
    # Set identity locally (writes to /workspace/.git/config, persists to agent container)
    git_identity = (
        "git config user.email 'agent@mcp-gateway.local' && "
        "git config user.name 'MCP Agent'"
    )

    if git_dir.exists():
        log.info("Pulling latest for '%s' on branch '%s'", project, branch)
        script = safe_dir + f"cd /workspace && {git_identity} && git pull origin {branch} 2>&1 || true"
    else:
        log.info("Cloning '%s' (branch '%s')", project, branch)
        script = (
            f"git clone --depth 50 {clone_url} /tmp/_repo 2>&1 && "
            "cp -a /tmp/_repo/.git /workspace/.git && "
            + safe_dir
            + f"cd /workspace && {git_identity} && git checkout -- . 2>/dev/null || true"
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
    """Safety net — push any unpushed commits the agent left behind.

    The agent is told to commit and push its own changes. This is just
    a fallback in case the agent committed but forgot to push.
    """
    script = (
        'git config --global --add safe.directory /workspace && '
        'cd /workspace && '
        f'git push origin {branch} 2>&1 && '
        'echo "PUSHED" || echo "NOTHING_TO_PUSH"'
    )

    try:
        result = await _run_git_container(client, image, host_workspace, script)
        if "PUSHED" in result:
            return "[Changes pushed to Gitea]"
        log.info("Safety push output: %s", result[:300])
        return "[Nothing new to push]"
    except Exception as e:
        log.warning("Safety push failed: %s", e)
        return f"[Warning: push failed: {e}]"


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


async def _run_job_background(job_id: str, task: str, agent_name: str,
                              workspace: str | None, project: str | None,
                              owner: str | None, repo: str | None,
                              branch: str, max_turns: int,
                              system_prompt: str | None,
                              model: str | None = None,
                              llm_url: str | None = None,
                              api_key: str | None = None,
                              mcp_ports_override: list[int] | None = None):
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
                f"Commit your changes with clear messages and push to origin before finishing.\n"
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
            command=profile["build_cmd"](full_task, max_turns, model=model,
                                           mcp_ports=mcp_ports_override or profile.get("mcp_ports")),
            volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode="host",
            environment=_apply_llm_overrides(profile["build_env"](model=model), llm_url, api_key),
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
                         system_prompt: str | None = None,
                         model: str | None = None,
                         llm_url: str | None = None,
                         api_key: str | None = None) -> str:
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
        model: Model alias or GGUF filename (default: DEFAULT_MODEL env var)
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
        "await_count": 0,
    }
    _save_job(job_id)

    asyncio.create_task(_run_job_background(
        job_id, task, agent, workspace, project, owner, repo,
        branch, max_turns, system_prompt, model=model,
        llm_url=llm_url, api_key=api_key))

    project_info = project or (f"{owner}/{repo}" if owner and repo else "(none)")
    return (
        f"Job started: {job_id}\n"
        f"Agent: {profile['label']}\n"
        f"Project: {project_info}\n"
        f"Branch: {branch}\n"
        f"\nUse check_agent_job('{job_id}') to poll for results."
    )


async def run_task_and_wait(
    task: str,
    agent: str = "goose",
    model: str | None = None,
    mcp_ports_override: list[int] | None = None,
    project: str | None = None,
    branch: str = "main",
    max_turns: int = 0,
    system_prompt: str | None = None,
    timeout: int = 0,
) -> str:
    """Start a coding task and wait for completion. Returns the result directly.

    Unlike run_task_async (fire-and-forget + polling), this holds the
    connection open and returns when the agent finishes or times out.
    The caller makes one call and gets one result — no polling needed.

    Args:
        task: Natural language task description
        agent: Agent profile name (key in AGENT_PROFILES)
        model: Model alias (resolved by _resolve_model)
        mcp_ports_override: Override MCP ports (replaces profile default)
        project: Gitea project name (auto-creates repo if needed)
        branch: Git branch to work on
        max_turns: Max reasoning steps (0 = agent default)
        system_prompt: Extra instructions prepended to the task
        timeout: Max seconds to wait (0 = use AGENT_TIMEOUT from config)
    """
    if agent not in AGENT_PROFILES:
        available = ", ".join(AGENT_PROFILES.keys())
        return f"Error: Unknown agent '{agent}'. Available: {available}"

    max_wait = timeout or config.AGENT_TIMEOUT

    job_id = uuid.uuid4().hex[:12]
    profile = AGENT_PROFILES[agent]

    _jobs[job_id] = {
        "status": "starting",
        "agent": agent,
        "agent_label": profile["label"],
        "task": task[:200],
        "project": project,
        "branch": branch,
        "started_at": time.monotonic(),
        "finished_at": None,
        "exit_code": None,
        "output": None,
    }
    _save_job(job_id)

    # Start the background job
    asyncio.create_task(_run_job_background(
        job_id, task, agent, None, project, None, None,
        branch, max_turns, system_prompt, model=model,
        mcp_ports_override=mcp_ports_override))

    # Hold connection — poll internally until done or timeout
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        await asyncio.sleep(2)
        job = _jobs.get(job_id, {})
        if job.get("status") in ("completed", "failed", "crashed"):
            return job.get("output", "(no output)")

    # Timeout — job may still be running in background
    return (
        f"Agent timed out after {max_wait}s. The job may still be running.\n"
        f"Task: {task[:200]}"
    )


def _await_backoff(count: int) -> int:
    """Progressive backoff: 35s, 40s, 45s, 50s... capped at 60s."""
    return min(35 + (count * 5), 60)


async def run_task_fire(
    task: str,
    agent: str = "goose",
    model: str | None = None,
    mcp_ports_override: list[int] | None = None,
    project: str | None = None,
    branch: str = "main",
    max_turns: int = 0,
    system_prompt: str | None = None,
) -> str:
    """Start a coding task in the background. Returns job_id immediately."""
    if agent not in AGENT_PROFILES:
        raise ValueError(f"Unknown agent '{agent}'. Available: {', '.join(AGENT_PROFILES)}")

    job_id = uuid.uuid4().hex[:12]
    profile = AGENT_PROFILES[agent]

    _jobs[job_id] = {
        "status": "starting",
        "agent": agent,
        "agent_label": profile["label"],
        "task": task[:200],
        "project": project,
        "branch": branch,
        "started_at": time.monotonic(),
        "finished_at": None,
        "exit_code": None,
        "output": None,
        "await_count": 0,
    }
    _save_job(job_id)

    asyncio.create_task(_run_job_background(
        job_id, task, agent, None, project, None, None,
        branch, max_turns, system_prompt, model=model,
        mcp_ports_override=mcp_ports_override))

    return job_id


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
    if job.get("status") in ("completed", "failed", "crashed"):
        return check_job(job_id)

    # Progressive backoff
    count = job.get("await_count", 0)
    wait_secs = _await_backoff(count)
    job["await_count"] = count + 1
    _save_job(job_id)

    # Hold connection for wait_secs, checking every 2s
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        if job.get("status") in ("completed", "failed", "crashed"):
            return check_job(job_id)

    elapsed = int(time.monotonic() - job["started_at"])
    return (
        f"Agent still working ({elapsed}s elapsed). "
        f"Call await_agent('{job_id}') again to check."
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
    """List available agent profiles and models."""
    lines = ["Available agent profiles:\n"]
    for name, profile in AGENT_PROFILES.items():
        lines.append(f"  {name}: {profile['description']}")
    lines.append("\nAvailable models (for local agents):")
    for alias in MODEL_ALIASES:
        lines.append(f"  {alias}: {_resolve_model(alias)}")
    lines.append(f"\nDefault model: {cfg('DEFAULT_MODEL', 'devstral')}")
    lines.append("LLM endpoint: " + _llm_host())
    return "\n".join(lines)
