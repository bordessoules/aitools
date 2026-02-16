"""
Code execution sandbox using Docker containers.

Runs untrusted code in isolated containers with:
- No network access
- Memory and CPU limits
- Automatic cleanup (auto-remove)
- Configurable timeout

Supported languages: Python, JavaScript (Node.js)
"""

import asyncio
import time

from . import config
from .logger import get_logger

log = get_logger("sandbox")

try:
    import docker
    from docker.errors import ContainerError, ImageNotFound, APIError
    _DOCKER_AVAILABLE = True
except ImportError:
    _DOCKER_AVAILABLE = False

# Language configurations: canonical name -> (image config attr, command prefix, aliases)
_LANGUAGES = {
    "python": {
        "image_attr": "CODE_SANDBOX_PYTHON_IMAGE",
        "command": ["python3", "-u", "-c"],
        "aliases": {"python", "py", "python3"},
    },
    "javascript": {
        "image_attr": "CODE_SANDBOX_NODE_IMAGE",
        "command": ["node", "-e"],
        "aliases": {"javascript", "js", "node", "nodejs"},
    },
}

# Build reverse lookup: alias -> canonical name
_ALIAS_MAP = {}
for _name, _cfg in _LANGUAGES.items():
    for _alias in _cfg["aliases"]:
        _ALIAS_MAP[_alias] = _name


def _resolve_language(language: str) -> tuple[str, dict] | None:
    """Resolve language string to canonical name and config."""
    canonical = _ALIAS_MAP.get(language.lower().strip())
    if canonical is None:
        return None
    return canonical, _LANGUAGES[canonical]


def _get_client():
    """Create Docker client. Handles Windows named pipe and Unix socket automatically."""
    return docker.from_env()


async def is_available() -> bool:
    """Check if Docker is accessible for sandbox execution."""
    if not _DOCKER_AVAILABLE:
        return False
    try:
        client = await asyncio.to_thread(_get_client)
        await asyncio.to_thread(client.ping)
        return True
    except Exception:
        return False


async def run_code(language: str, code: str) -> str:
    """Execute code in an isolated Docker container.

    Args:
        language: Programming language ("python", "py", "javascript", "js", etc.)
        code: Source code to execute

    Returns:
        Combined stdout + stderr output with execution metadata, or error message
    """
    if not _DOCKER_AVAILABLE:
        return "Error: docker Python package not installed. Run: pip install docker"

    # Resolve language
    resolved = _resolve_language(language)
    if resolved is None:
        supported = ", ".join(sorted(_ALIAS_MAP.keys()))
        return f"Error: Unknown language '{language}'. Supported: {supported}"

    canonical, lang_cfg = resolved
    image = getattr(config, lang_cfg["image_attr"])
    command = lang_cfg["command"] + [code]

    # Convert CPU limit to nano_cpus (1.0 CPU = 1_000_000_000 nano_cpus)
    nano_cpus = int(config.CODE_SANDBOX_CPU_LIMIT * 1_000_000_000)

    log.info("Running %s code (%d chars) in %s", canonical, len(code), image)
    start = time.monotonic()

    try:
        client = await asyncio.to_thread(_get_client)

        # Run code in isolated container
        output = await asyncio.to_thread(
            client.containers.run,
            image=image,
            command=command,
            network_mode="none",
            mem_limit=config.CODE_SANDBOX_MEMORY_LIMIT,
            nano_cpus=nano_cpus,
            remove=True,
            stdout=True,
            stderr=True,
            detach=False,
        )

        elapsed = time.monotonic() - start
        result = output.decode("utf-8", errors="replace")

        # Truncate very long output
        if len(result) > 50_000:
            result = result[:50_000] + f"\n\n... (output truncated at 50,000 chars)"

        log.info("Code executed successfully in %.1fs (%d chars output)", elapsed, len(result))
        return f"{result}\n\n[Executed {canonical} in {elapsed:.1f}s]"

    except ContainerError as e:
        elapsed = time.monotonic() - start
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "No error output"
        log.warning("Code execution failed (exit %s) in %.1fs", e.exit_status, elapsed)
        return f"Execution error (exit code {e.exit_status}):\n{stderr}\n\n[Failed after {elapsed:.1f}s]"

    except ImageNotFound:
        return f"Error: Sandbox image '{image}' not found. Run: docker pull {image}"

    except APIError as e:
        if "timeout" in str(e).lower() or "deadline" in str(e).lower():
            return f"Error: Execution timed out after {config.CODE_SANDBOX_TIMEOUT}s"
        log.error("Docker API error: %s", e)
        return f"Error: Docker API error: {e.explanation or str(e)}"

    except Exception as e:
        log.error("Unexpected sandbox error: %s", e)
        return f"Error: {type(e).__name__}: {e}"
