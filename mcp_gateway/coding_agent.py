"""
Coding agent wrapper using Goose by Block.

Spawns Goose in a Docker container with:
- Task passed via CLI arguments
- Workspace mounted as volume
- Access to vLLM for LLM inference
- Access to MCP Gateway for tools (search, fetch, KB)
- Configurable timeout
"""

import asyncio
import json
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


def _build_goose_config() -> dict:
    """Build Goose config.yaml content for Docker mount."""
    cfg = {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": config.VISION_MODEL,
        "OPENAI_HOST": config.GOOSE_LLM_URL.rstrip("/v1").rstrip("/"),
        "OPENAI_BASE_PATH": "/v1/chat/completions",
        "OPENAI_API_KEY": config.VISION_API_KEY or "not-needed",
        "extensions": {
            "developer": {
                "enabled": True,
                "type": "builtin",
            },
        },
    }

    # Add MCP gateway as an extension if URL is configured
    if config.GOOSE_MCP_GATEWAY_URL:
        cfg["extensions"]["mcp_gateway"] = {
            "name": "mcp-gateway",
            "description": "Web search, content fetching, and knowledge base",
            "enabled": True,
            "type": "sse",
            "uri": f"{config.GOOSE_MCP_GATEWAY_URL}/sse",
            "timeout": 120,
            "bundled": False,
        }

    return cfg


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
        # Find our container by hostname (Docker sets hostname = container ID)
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
        # Check if image exists locally
        try:
            await asyncio.to_thread(client.images.get, config.GOOSE_IMAGE)
            return True
        except Exception:
            # Image not pulled yet — still "available" (Docker works, just needs pull)
            log.info("Goose image %s not found locally, will pull on first use", config.GOOSE_IMAGE)
            return True
    except Exception:
        return False


async def run_task(task: str, workspace: str | None = None) -> str:
    """Run a coding task using Goose agent in a Docker container.

    Args:
        task: Natural language description of the coding task
        workspace: Optional workspace directory path (defaults to config.GOOSE_WORKSPACE)

    Returns:
        Goose output with task results, or error message
    """
    if not _DOCKER_AVAILABLE:
        return "Error: docker Python package not installed. Run: pip install docker"

    workspace_path = Path(workspace) if workspace else config.GOOSE_WORKSPACE
    workspace_path.mkdir(parents=True, exist_ok=True)

    log.info("Running Goose task: %s (workspace: %s)", task[:100], workspace_path)
    start = time.monotonic()

    # Write Goose config to a temp file inside the workspace directory.
    # This is critical for Docker-in-Docker: the file must be on a path
    # that's shared between the gateway container and the host filesystem.
    goose_config = _build_goose_config()
    config_file = workspace_path / ".goose_config.yaml"

    try:
        client = await asyncio.to_thread(_get_client)

        # Write config inside workspace (shared volume between gateway and host)
        config_file.write_text(json.dumps(goose_config))

        # Docker-in-Docker path resolution:
        # When running inside Docker, volume mounts for sibling containers must
        # use host paths, not container paths. Auto-detect by inspecting our mounts.
        host_workspace = _get_host_workspace_path()
        if host_workspace:
            # Running in Docker: use host path for volume mounts
            host_config = f"{host_workspace}/.goose_config.yaml"
            log.debug("Docker-in-Docker mode: host workspace = %s", host_workspace)
        else:
            # Running locally: use resolved paths directly
            host_workspace = str(workspace_path.resolve())
            host_config = str(config_file.resolve())

        # Build container config
        volumes = {
            host_workspace: {"bind": "/workspace", "mode": "rw"},
            host_config: {"bind": "/root/.config/goose/config.yaml", "mode": "ro"},
        }

        # Goose needs network to reach vLLM and MCP gateway
        # We'll use the host network for simplicity on Windows/Docker Desktop
        output = await asyncio.to_thread(
            client.containers.run,
            image=config.GOOSE_IMAGE,
            command=["run", "--text", task],
            volumes=volumes,
            working_dir="/workspace",
            network_mode="host",
            environment={
                "GOOSE_PROVIDER": "openai",
                "GOOSE_MODEL": config.VISION_MODEL,
                "OPENAI_HOST": config.GOOSE_LLM_URL.rstrip("/v1").rstrip("/"),
                "OPENAI_BASE_PATH": "/v1/chat/completions",
                "OPENAI_API_KEY": config.VISION_API_KEY or "not-needed",
            },
            extra_hosts={"host.docker.internal": "host-gateway"},
            mem_limit="2g",
            remove=True,
            stdout=True,
            stderr=True,
            detach=False,
        )

        elapsed = time.monotonic() - start
        result = output.decode("utf-8", errors="replace")

        if len(result) > 100_000:
            result = result[:100_000] + "\n\n... (output truncated at 100,000 chars)"

        log.info("Goose task completed in %.1fs (%d chars output)", elapsed, len(result))
        return f"{result}\n\n[Goose completed in {elapsed:.0f}s | workspace: {workspace_path}]"

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

    finally:
        # Clean up config file from workspace
        try:
            config_file.unlink(missing_ok=True)
        except Exception:
            pass
