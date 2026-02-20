"""
Agent Runner - Host-side MCP server for delegating tasks to CLI agents.

Uses async jobs — delegate_agent returns immediately with a job ID,
then check_job polls for results. No timeouts, no blocking.

Both agent types work through the MCP gateway's Gitea tools (no local git).

Exposed tools:
- delegate_agent(task, owner, repo, branch, agent, max_turns)
- check_job(job_id)
- list_agents()
"""

import asyncio
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# Load .env from project root
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


mcp = FastMCP("agent-runner")

# =============================================================================
# CONFIGURATION
# =============================================================================

GOOSE_PATH = os.getenv("GOOSE_PATH", "goose")
GOOSE_MODEL = os.getenv("GOOSE_MODEL", "openai/gpt-oss-20b")
GOOSE_PROVIDER = os.getenv("GOOSE_PROVIDER", "openai")
OPENAI_HOST = os.getenv("OPENAI_HOST", "http://bluefin:1234")
MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "http://localhost:8000/mcp")
WORKDIR_BASE = Path(os.getenv("AGENT_WORKDIR", tempfile.gettempdir())) / "agent-workspaces"

# Agent definitions — all use Gitea API via MCP gateway, no local git
AGENTS = {
    "goose-dev": {
        "description": "Goose developer — has local file tools (write_file, shell) + MCP gateway. Best for coding tasks.",
        "build_cmd": lambda task, max_turns, cwd: [
            GOOSE_PATH, "run",
            "--text", task,
            "--no-session",
            "--no-profile",
            "--provider", GOOSE_PROVIDER,
            "--model", GOOSE_MODEL,
            "--with-builtin", "developer",
            "--with-streamable-http-extension", MCP_GATEWAY_URL,
            "--max-turns", str(max_turns),
        ],
    },
    "goose-reviewer": {
        "description": "Goose reviewer — MCP gateway only (gitea tools). Reviews PRs, reads code, adds comments. No local file access.",
        "build_cmd": lambda task, max_turns, cwd: [
            GOOSE_PATH, "run",
            "--text", task,
            "--no-session",
            "--no-profile",
            "--provider", GOOSE_PROVIDER,
            "--model", GOOSE_MODEL,
            "--with-streamable-http-extension", MCP_GATEWAY_URL,
            "--quiet",
            "--max-turns", str(max_turns),
        ],
    },
    "goose": {
        "description": "Alias for goose-dev",
        "alias": "goose-dev",
    },
}


# =============================================================================
# JOB TRACKING
# =============================================================================

_jobs: dict[str, dict] = {}


# =============================================================================
# ASYNC JOB RUNNER
# =============================================================================

async def _run_job(job_id: str, task: str, owner: str, repo: str,
                   branch: str, agent: str, agent_config: dict,
                   max_turns: int, system_prompt: Optional[str]):
    """Background coroutine that runs an agent job."""
    job = _jobs[job_id]
    workdir = WORKDIR_BASE / f"{agent}-{job_id[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # Build the task prompt
        full_task = task
        if system_prompt:
            full_task = f"{system_prompt}\n\nTask: {task}"

        # Build command
        cmd = agent_config["build_cmd"](full_task, max_turns, workdir)

        # Environment — only pass what Goose needs
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = "not-needed"
        env["OPENAI_HOST"] = OPENAI_HOST

        print(f"[agent-runner] Job {job_id[:8]}: starting {agent}", file=sys.stderr)
        print(f"[agent-runner]   model={GOOSE_MODEL} host={OPENAI_HOST}", file=sys.stderr)

        # Run agent
        job["status"] = "running"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        stdout, _ = await proc.communicate()
        agent_output = stdout.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode

        # Store result
        job["status"] = "completed" if exit_code == 0 else "failed"
        job["exit_code"] = exit_code
        job["output"] = agent_output[-5000:] if len(agent_output) > 5000 else agent_output
        job["finished_at"] = time.time()

        elapsed = job["finished_at"] - job["started_at"]
        print(f"[agent-runner] Job {job_id[:8]}: done in {elapsed:.0f}s (exit={exit_code})", file=sys.stderr)

    except Exception as e:
        job["status"] = "failed"
        job["output"] = f"{type(e).__name__}: {e}"
        job["finished_at"] = time.time()
        print(f"[agent-runner] Job {job_id[:8]}: error — {e}", file=sys.stderr)


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
        lines.append(f"  {name}: {agent['description']}")
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

    # Fire and forget
    asyncio.create_task(_run_job(
        job_id, task, owner, repo, branch, agent, agent_config,
        max_turns, system_prompt,
    ))

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

    if job["status"] in ("completed", "failed"):
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
