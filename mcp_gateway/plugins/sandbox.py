"""Code execution sandbox plugin.

Provides run_code() tool for executing Python/JavaScript in isolated Docker containers.
"""

from .. import code_sandbox
from ..logger import get_logger

log = get_logger("plugin.sandbox")


def register(mcp):
    """Register code execution tools with FastMCP."""

    @mcp.tool()
    async def run_code(language: str, code: str) -> str:
        """
        Execute code in a secure, isolated Docker sandbox.

        Runs code in a fresh container with NO network access, memory/CPU limits,
        and automatic cleanup. Use this for:
        - Running Python or JavaScript code snippets
        - Testing algorithms or data processing
        - Verifying calculations or transformations

        Supported languages: "python" (or "py"), "javascript" (or "js", "node")

        Security: Each execution runs in a fresh container with:
        - No network access (completely isolated)
        - 256MB memory limit (configurable)
        - 30-second timeout (configurable)
        - Container auto-removed after execution

        Args:
            language: Programming language ("python" or "javascript")
            code: Source code to execute

        Returns:
            Execution output (stdout + stderr) with timing info
        """
        return await code_sandbox.run_code(language, code)


async def health_checks() -> list[tuple[str, bool]]:
    """Check Docker availability for code sandbox."""
    checks = []
    try:
        if await code_sandbox.is_available():
            checks.append(("[OK] Code Sandbox (Docker)", True))
        else:
            checks.append(("[WARN] Code Sandbox: Docker not accessible", False))
    except Exception:
        checks.append(("[WARN] Code Sandbox: docker package not installed", False))
    return checks


PLUGIN = {
    "name": "sandbox",
    "env_var": "ENABLE_CODE_EXECUTION",
    "default_enabled": False,
    "register": register,
    "health_checks": health_checks,
}
