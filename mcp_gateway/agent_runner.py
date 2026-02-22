"""
Agent Runner - Host-side MCP server for delegating tasks to CLI agents.

Uses threaded jobs — delegate_agent returns immediately with a job ID,
then check_job polls for results. Jobs run in threads (not asyncio tasks)
so they don't depend on the MCP stdio event loop being free.

Exposed tools:
- delegate_agent(task, owner, repo, branch, agent, max_turns)
- check_job(job_id)
- list_agents()
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-runner")

# =============================================================================
# CONFIGURATION  — dynamic: edit .env and changes take effect on next job
# =============================================================================

_ENV_FILE = Path(__file__).parent.parent / ".env"


def cfg(key: str, default: str = "") -> str:
    """Read a config value from .env (re-read each call, no restart needed)."""
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ENV_FILE).get(key, os.getenv(key, default))
    except ImportError:
        return os.getenv(key, default)


# WORKDIR_BASE is the one value read once at startup (it defines state directory paths)
WORKDIR_BASE = Path(cfg("AGENT_WORKDIR", tempfile.gettempdir())) / "agent-workspaces"

# Agent definitions — all config read via cfg() so .env changes take effect without restart
AGENTS = {
    # --- Goose agents ---
    "goose-dev": {
        "description": "Goose developer — has local file tools + MCP gateway. Best for coding tasks.",
        "model": lambda: cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct"),
        "host": lambda: cfg("OPENAI_HOST", "http://localhost:1234"),
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("GOOSE_PATH", "goose"), "run",
            "--text", task,
            "--no-session",
            "--no-profile",
            "--provider", cfg("GOOSE_PROVIDER", "openai"),
            "--model", cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct"),
            "--with-builtin", "developer",
            "--with-streamable-http-extension", cfg("MCP_GATEWAY_URL", "http://localhost:8000/mcp"),
            "--max-turns", str(max_turns),
        ],
    },
    "goose-reviewer": {
        "description": "Goose reviewer — MCP gateway only (gitea tools). No local file access.",
        "model": lambda: cfg("GOOSE_MODEL_REVIEWER", cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct")),
        "host": lambda: cfg("OPENAI_HOST_REVIEWER", cfg("OPENAI_HOST", "http://localhost:1234")),
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("GOOSE_PATH", "goose"), "run",
            "--text", task,
            "--no-session",
            "--no-profile",
            "--provider", cfg("GOOSE_PROVIDER", "openai"),
            "--model", cfg("GOOSE_MODEL_REVIEWER", cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct")),
            "--with-streamable-http-extension", cfg("MCP_GATEWAY_URL", "http://localhost:8000/mcp"),
            "--quiet",
            "--max-turns", str(max_turns),
        ],
    },
    "goose": {"description": "Alias for goose-dev", "alias": "goose-dev"},

    # --- Goose + Devstral via llama.cpp (128k ctx, Q8 KV cache, flash-attn) ---
    "goose-devstral": {
        "description": "Goose with devstral model via llama.cpp on local 3090. 128k context, tool-call support.",
        "model": lambda: cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
        "host": lambda: cfg("DEVSTRAL_HOST", "http://localhost:8083"),
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("GOOSE_PATH", "goose"), "run",
            "--text", task,
            "--no-session",
            "--no-profile",
            "--provider", "openai",
            "--model", cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
            "--with-builtin", "developer",
            "--with-streamable-http-extension", cfg("MCP_GATEWAY_URL", "http://localhost:8000/mcp"),
            "--max-turns", str(max_turns),
        ],
    },

    # --- Qwen Code (local LM Studio) ---
    # IMPORTANT: --auth-type openai is REQUIRED for qwen to use custom endpoints.
    # Without it, qwen defaults to qwen-oauth and silently ignores --openai-base-url.
    # NOTE: Prompt is passed via stdin (use_stdin=True), NOT -p flag.
    # Windows .cmd wrappers mangle multi-line -p arguments, breaking --auth-type parsing.
    "qwen": {
        "description": "Qwen Code CLI — local LM Studio. Has file tools + shell.",
        "model": lambda: cfg("QWEN_MODEL", cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct")),
        "host": lambda: cfg("OPENAI_HOST", "http://localhost:1234"),
        "use_stdin": True,
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("QWEN_PATH", "qwen"),
            "-y",
            "--auth-type", "openai",
            "--openai-base-url", cfg("OPENAI_HOST", "http://localhost:1234") + "/v1",
            "--openai-api-key", "not-needed",
            "-m", cfg("QWEN_MODEL", cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct")),
        ],
    },

    # --- Qwen Code (qwen-oauth cloud, no local model) ---
    "qwen-cloud": {
        "description": "Qwen Code CLI — uses qwen.ai cloud API (qwen-oauth). Free tier.",
        "model": lambda: "qwen3-coder-plus",
        "host": lambda: "api.qwen.ai",
        "use_stdin": True,
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("QWEN_PATH", "qwen"),
            "-y",
            "--auth-type", "qwen-oauth",
        ],
    },

    # --- Qwen CLI + Devstral via llama.cpp ---
    "qwen-devstral": {
        "description": "Qwen CLI with devstral model via llama.cpp on local 3090. 128k context.",
        "model": lambda: cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
        "host": lambda: cfg("DEVSTRAL_HOST", "http://localhost:8083"),
        "use_stdin": True,
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("QWEN_PATH", "qwen"),
            "-y",
            "--auth-type", "openai",
            "--openai-base-url", cfg("DEVSTRAL_HOST", "http://localhost:8083") + "/v1",
            "--openai-api-key", "not-needed",
            "-m", cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
        ],
    },

    # --- Vibe (Mistral's official CLI for Devstral) ---
    # Vibe is designed specifically for devstral. Uses ~/.vibe/config.toml for
    # provider/model config (active_model = "devstral-llamacpp" points to llama.cpp:8083).
    # MCP gateway configured in config.toml as [[mcp_servers]].
    # NOTE: --enabled-tools limits tools to built-ins + issue/PR creation only.
    # Agents now get a pre-cloned repo in workdir, so gitea file tools are unnecessary.
    # --output json captures structured output for debugging.
    "vibe": {
        "description": "Mistral Vibe CLI — devstral via local llama.cpp. Mistral's native agent for devstral.",
        "model": lambda: cfg("DEVSTRAL_MODEL", "Devstral-Small-2-24B-Instruct-2512-IQ4_XS-4.04bpw.gguf"),
        "host": lambda: cfg("DEVSTRAL_HOST", "http://localhost:8083"),
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("VIBE_PATH", "vibe"),
            "-p", task,
            "--max-turns", str(max_turns),
            "--workdir", str(cwd),
            "--enabled-tools", "re:^(bash|grep|read_file|search_replace|write_file|task|todo|mcp-gateway_gitea_create_issue|mcp-gateway_gitea_create_pr)",
            "--output", "json",
        ],
    },

    # --- Kimi Code ---
    "kimi": {
        "description": "Kimi Code CLI — kimi-code cloud subscription. Has file tools + shell + MCP (Gitea).",
        "model": lambda: "kimi-for-coding",
        "host": lambda: "api.kimi.com",
        "build_cmd": lambda task, max_turns, cwd: [
            cfg("KIMI_PATH", "kimi"),
            "-p", task,
            "--print",
            "--max-steps-per-turn", str(max_turns),
            "--work-dir", str(cwd),
        ],
    },
}


# =============================================================================
# JOB TRACKING  (persisted to disk as JSON so jobs survive MCP restarts)
# =============================================================================

_jobs: dict[str, dict] = {}
_STATE_DIR = WORKDIR_BASE / ".state"


def _save_job(job_id: str):
    """Persist a single job's state to disk."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = _STATE_DIR / f"{job_id}.json"
    state_file.write_text(json.dumps(_jobs[job_id], default=str))


def _load_jobs():
    """Load surviving job state from disk on startup."""
    if not _STATE_DIR.exists():
        return
    for f in _STATE_DIR.glob("*.json"):
        job_id = f.stem
        try:
            job = json.loads(f.read_text())
            # Mark jobs that were running when we crashed
            if job["status"] in ("starting", "running"):
                job["status"] = "crashed"
                job["output"] = job.get("output") or "(MCP server restarted while job was running)"
                job["finished_at"] = job.get("finished_at") or time.time()
                f.write_text(json.dumps(job, default=str))
            _jobs[job_id] = job
        except Exception:
            pass
    if _jobs:
        print(f"[agent-runner] Loaded {len(_jobs)} jobs from disk", file=sys.stderr)


# Load persisted jobs on startup
_load_jobs()


# =============================================================================
# GIT HELPERS  (clone before agent, push after)
# =============================================================================

def _clone_repo(owner: str, repo: str, branch: str, workdir: Path) -> str:
    """Clone a Gitea repo into the workdir and checkout the target branch.

    Uses token-authenticated HTTP URL so no SSH key needed.
    Returns a status message. Raises on failure.
    """
    gitea_url = cfg("GITEA_URL", "http://localhost:3001")
    gitea_token = cfg("GITEA_TOKEN", "")

    # Build authenticated clone URL: http://agent:token@host:port/owner/repo.git
    # Gitea accepts any username when the token is the password.
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(gitea_url)
    auth_netloc = f"agent:{gitea_token}@{parsed.hostname}"
    if parsed.port:
        auth_netloc += f":{parsed.port}"
    auth_url = urlunparse(parsed._replace(netloc=auth_netloc))
    clone_url = f"{auth_url}/{owner}/{repo}.git"

    def _git(*args, check=True, **kwargs):
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(workdir), capture_output=True, text=True,
            check=check, **kwargs,
        )

    # Clone (shallow — enough history for diffs)
    subprocess.run(
        ["git", "clone", "--depth", "50", clone_url, "."],
        cwd=str(workdir), capture_output=True, text=True, check=True,
    )

    # Configure git identity for commits
    _git("config", "user.name", "agent-runner")
    _git("config", "user.email", "agent@local")

    # Checkout or create branch
    if branch != "main" and branch != "master":
        # Check if branch exists on remote
        result = _git("ls-remote", "--heads", "origin", branch, check=False)
        if result.stdout.strip():
            # Branch exists — fetch it (shallow clone only has default branch)
            # Fetch both the local branch AND the remote-tracking ref
            _git("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", check=False)
            _git("checkout", "-b", branch, f"origin/{branch}")
        else:
            # New branch — create from current HEAD
            _git("checkout", "-b", branch)

    current = _git("branch", "--show-current").stdout.strip()
    return f"Cloned {owner}/{repo} on branch '{current}'"


def _push_results(workdir: Path, branch: str) -> str:
    """Stage, commit, and push any changes the agent left behind.

    Non-fatal — returns a summary string, never raises.
    """
    def _git(*args, check=True):
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(workdir), capture_output=True, text=True,
            check=check,
        )

    try:
        # Check for uncommitted changes
        status = _git("status", "--porcelain", check=False)
        if status.stdout.strip():
            _git("add", "-A")
            _git("commit", "-m", "agent: auto-commit workspace changes", check=False)

        # Check for unpushed commits
        # For new branches, origin/{branch} won't exist — check remote tracking
        has_remote = _git(
            "rev-parse", "--verify", f"origin/{branch}", check=False
        ).returncode == 0

        if has_remote:
            log = _git("log", f"origin/{branch}..HEAD", "--oneline", check=False)
            unpushed = log.stdout.strip()
            if not unpushed:
                return "Nothing to push"
        # else: new branch — always push

        # Push (set upstream for new branches)
        _git("push", "-u", "origin", branch)

        if has_remote and unpushed:
            commit_count = len(unpushed.splitlines())
            return f"Pushed {commit_count} commit(s) to {branch}"
        return f"Pushed new branch '{branch}' to origin"

    except Exception as e:
        return f"Push failed (non-fatal): {e}"


# =============================================================================
# THREADED JOB RUNNER  (runs in real OS threads, not asyncio tasks)
# =============================================================================

def _run_job(job_id: str, task: str, owner: str, repo: str,
             branch: str, agent: str, agent_config: dict,
             max_turns: int, system_prompt: Optional[str]):
    """Background thread that runs an agent job.

    Uses subprocess.Popen (not asyncio) so it runs completely independently
    of the MCP stdio event loop.
    """
    job = _jobs[job_id]
    workdir = WORKDIR_BASE / f"{agent}-{job_id[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # --- Step 1: Clone the repo into workdir ---
        print(f"[agent-runner] Job {job_id[:8]}: cloning {owner}/{repo}@{branch}", file=sys.stderr)
        try:
            clone_msg = _clone_repo(owner, repo, branch, workdir)
            job["clone_status"] = clone_msg
            print(f"[agent-runner] Job {job_id[:8]}: {clone_msg}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr or e.stdout or str(e)
            raise RuntimeError(f"Clone failed: {stderr_text}")

        # --- Step 2: Build the task prompt with repo context ---
        repo_context = (
            f"Your working directory is a git clone of {owner}/{repo} on branch '{branch}'.\n"
            f"Edit files directly -- your changes will be committed and pushed automatically when you finish.\n"
            f"If you want to commit with a meaningful message, use: git commit -m \"your message\"\n"
        )
        full_task = repo_context + "\n"
        if system_prompt:
            full_task += f"{system_prompt}\n\n"
        full_task += f"Task: {task}"

        # Build command
        cmd = agent_config["build_cmd"](full_task, max_turns, workdir)

        # Environment
        agent_host = agent_config.get("host", lambda: cfg("OPENAI_HOST", "http://localhost:1234"))()
        agent_model = agent_config.get("model", lambda: cfg("GOOSE_MODEL", "qwen3-coder-30b-a3b-instruct"))()
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = "not-needed"
        env["OPENAI_HOST"] = agent_host
        # Qwen also reads OPENAI_BASE_URL
        env["OPENAI_BASE_URL"] = agent_host + "/v1"
        # Fix Windows charmap encoding crashes (kimi outputs UTF-8 with replacement chars)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        print(f"[agent-runner] Job {job_id[:8]}: starting {agent}", file=sys.stderr)
        print(f"[agent-runner]   model={agent_model} host={agent_host}", file=sys.stderr)

        # --- Step 3: Run the agent ---
        job["status"] = "running"
        _save_job(job_id)

        # Qwen agents use stdin for prompt (use_stdin=True) because Windows .cmd
        # wrappers mangle multi-line -p arguments, breaking --auth-type parsing.
        use_stdin = agent_config.get("use_stdin", False)
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            input=full_task.encode("utf-8") if use_stdin else None,
            stdin=None if use_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

        agent_output = proc.stdout.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode

        # Write full output to log file (always available, not truncated)
        (workdir / "agent-output.log").write_text(agent_output, encoding="utf-8")

        # --- Step 4: Push results back to Gitea ---
        push_summary = _push_results(workdir, branch)
        job["push_summary"] = push_summary
        print(f"[agent-runner] Job {job_id[:8]}: {push_summary}", file=sys.stderr)

        # Store result
        job["status"] = "completed" if exit_code == 0 else "failed"
        job["exit_code"] = exit_code
        output_with_push = agent_output
        if push_summary != "Nothing to push":
            output_with_push += f"\n\n--- Git Push ---\n{push_summary}"
        job["output"] = output_with_push[-5000:] if len(output_with_push) > 5000 else output_with_push
        job["finished_at"] = time.time()
        _save_job(job_id)

        elapsed = job["finished_at"] - job["started_at"]
        print(f"[agent-runner] Job {job_id[:8]}: done in {elapsed:.0f}s (exit={exit_code})", file=sys.stderr)

    except Exception as e:
        job["status"] = "failed"
        job["output"] = f"{type(e).__name__}: {e}"
        job["finished_at"] = time.time()
        _save_job(job_id)
        print(f"[agent-runner] Job {job_id[:8]}: error -- {e}", file=sys.stderr)


# =============================================================================
# TOOLS
# =============================================================================

@mcp.tool()
async def list_agents() -> str:
    """List available agents that can be delegated tasks.

    Returns:
        Agent names and descriptions
    """
    lines = ["Available agents:\n"]
    for name, agent in AGENTS.items():
        desc = agent['description']
        if "alias" not in agent:
            model = agent.get("model", lambda: GOOSE_MODEL)()
            host = agent.get("host", lambda: OPENAI_HOST)()
            lines.append(f"  {name}: {desc}\n    model={model} host={host}")
        else:
            lines.append(f"  {name}: {desc}")
    return "\n".join(lines)


@mcp.tool()
async def delegate_agent(
    task: str,
    owner: str,
    repo: str,
    branch: str = "agent-work",
    agent: str = "goose",
    max_turns: int = 10,
    system_prompt: Optional[str] = None,
) -> str:
    """Delegate a task to a CLI agent working on a Gitea repository.

    Returns immediately with a job ID. Use check_job() to poll for results.

    The agent will:
    1. Clone the repo from Gitea into an isolated workspace
    2. Checkout/create the specified branch
    3. Execute the task using its own LLM
    4. Commit and push any changes back to Gitea

    After the job completes, you can create a PR with gitea_create_pr().

    Args:
        task: What the agent should do (natural language instruction)
        owner: Gitea repo owner (username)
        repo: Gitea repo name
        branch: Branch to work on (default: agent-work)
        agent: Which agent to use (default: goose). See list_agents()
        max_turns: Max reasoning steps the agent can take (default: 10)
        system_prompt: Optional extra system instructions for the agent

    Returns:
        Job ID and status. Use check_job(job_id) to get results.
    """
    if agent not in AGENTS:
        return f"Error: Unknown agent '{agent}'. Available: {', '.join(AGENTS.keys())}"

    agent_config = AGENTS[agent]

    # Resolve aliases
    if "alias" in agent_config:
        agent = agent_config["alias"]
        agent_config = AGENTS[agent]

    # Create job
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "status": "starting",
        "agent": agent,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "task": task[:200],
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "output": None,
    }

    _save_job(job_id)

    # Fire and forget — runs in a real OS thread, not an asyncio task
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, task, owner, repo, branch, agent, agent_config,
              max_turns, system_prompt),
        daemon=True,
    )
    thread.start()

    return f"Job started: {job_id}\nAgent: {agent}\nRepo: {owner}/{repo}\nBranch: {branch}\n\nUse check_job('{job_id}') to poll for results."


@mcp.tool()
async def check_job(job_id: str) -> str:
    """Check the status of a delegated agent job.

    Args:
        job_id: The job ID returned by delegate_agent()

    Returns:
        Job status and output (if completed)
    """
    if job_id not in _jobs:
        if _jobs:
            recent = sorted(_jobs.items(), key=lambda x: x[1]["started_at"], reverse=True)[:5]
            lines = [f"Job '{job_id}' not found. Recent jobs:"]
            for jid, j in recent:
                lines.append(f"  {jid}: {j['status']} — {j['agent']} on {j['repo']}")
            return "\n".join(lines)
        return f"Job '{job_id}' not found. No jobs have been started."

    job = _jobs[job_id]
    elapsed = time.time() - job["started_at"]

    lines = [
        f"Job: {job_id}",
        f"Status: {job['status']}",
        f"Agent: {job['agent']}",
        f"Repo: {job['repo']}",
        f"Branch: {job['branch']}",
        f"Elapsed: {elapsed:.0f}s",
    ]

    if job["status"] in ("completed", "failed", "crashed"):
        if job["exit_code"] is not None:
            lines.append(f"Exit code: {job['exit_code']}")
        lines.append("")
        lines.append("--- Agent Output ---")
        lines.append(job.get("output", "(no output)"))
    else:
        lines.append(f"\nStill working... check again in a few seconds.")

    return "\n".join(lines)


# =============================================================================
# SERVER
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Runner - MCP server for delegating tasks to CLI agents")
    parser.add_argument("-t", "--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=8001)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        app = mcp.streamable_http_app()
        print(f"Starting Agent Runner on {args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
