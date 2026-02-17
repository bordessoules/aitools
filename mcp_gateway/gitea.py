"""
Gitea integration for persistent coding projects.

Handles:
- Admin user provisioning (first-boot setup via install page)
- API token management
- Repository creation and existence checks
- Clone URL generation for Goose containers

Gitea runs as a Docker service on mcp-network. The gateway accesses it
via Docker DNS (http://gitea:3000). Goose containers (network_mode=host)
access it via localhost:{GITEA_HOST_PORT}.

First-boot flow:
  Gitea starts with INSTALL_LOCK=false → shows install page → gateway POSTs
  the install form → Gitea restarts internally → API becomes available →
  gateway creates API token. Subsequent boots skip install (already done).
"""

import asyncio

import httpx

from . import config
from .logger import get_logger

log = get_logger("gitea")

# Cached API token (created on first use, survives for gateway lifetime)
_api_token: str | None = None


def _base_url() -> str:
    """Internal Gitea URL (Docker DNS, used by gateway)."""
    return config.GITEA_URL.rstrip("/")


def _clone_base_url() -> str:
    """Gitea URL for git clone (used by Goose via localhost).

    Embeds credentials in URL since Goose containers are ephemeral
    and can't use persistent gitconfig or credential helpers.
    """
    password = _api_token or config.GITEA_ADMIN_PASSWORD
    return f"http://{config.GITEA_ADMIN_USER}:{password}@localhost:{config.GITEA_HOST_PORT}"


async def is_available() -> bool:
    """Check if Gitea is reachable (works both pre- and post-install)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Try version API first (works after install)
            resp = await client.get(f"{_base_url()}/api/v1/version")
            if resp.status_code == 200:
                return True
            # Fall back to root page (works on install page too)
            resp = await client.get(f"{_base_url()}/")
            return resp.status_code == 200
    except Exception:
        return False


async def _api_ready() -> bool:
    """Check if Gitea API is available (post-install only)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_base_url()}/api/v1/version")
            return resp.status_code == 200
    except Exception:
        return False


async def ensure_setup() -> bool:
    """Ensure Gitea is provisioned with admin user and API token.

    Handles two scenarios:
    1. Fresh install: POST install form, wait for restart, create token
    2. Already installed: authenticate with existing credentials, create token

    Idempotent — safe to call multiple times.
    Returns True if setup succeeded.
    """
    global _api_token

    if _api_token:
        return True

    base = _base_url()
    user = config.GITEA_ADMIN_USER
    password = config.GITEA_ADMIN_PASSWORD
    email = config.GITEA_ADMIN_EMAIL

    async with httpx.AsyncClient(timeout=15) as client:
        # Step 1: Check if API is available (already installed?)
        try:
            resp = await client.get(f"{base}/api/v1/version")
            api_available = resp.status_code == 200
        except Exception:
            api_available = False

        if not api_available:
            # API not available — likely fresh install, run install flow
            log.info("Gitea API not available, running install flow...")
            ok = await _install_flow(client, base, user, password, email)
            if not ok:
                return False

            # Wait for Gitea to restart after install (internal restart)
            log.info("Waiting for Gitea API to become available...")
            for i in range(15):  # Up to 30 seconds
                await asyncio.sleep(2)
                if await _api_ready():
                    log.info("Gitea API ready after %ds", (i + 1) * 2)
                    break
            else:
                log.error("Gitea API not available after install (timed out)")
                return False

        # Step 2: Verify admin user exists
        try:
            resp = await client.get(
                f"{base}/api/v1/user",
                headers={"Authorization": f"Basic {_basic_auth(user, password)}"},
            )
            if resp.status_code == 200:
                log.info("Gitea admin user '%s' authenticated", user)
            else:
                log.error("Gitea admin auth failed: %s", resp.status_code)
                return False
        except Exception as e:
            log.error("Error authenticating Gitea admin: %s", e)
            return False

        # Step 3: Create API token
        try:
            return await _ensure_token(client, base, user, password)
        except Exception as e:
            log.error("Error creating Gitea token: %s", e)
            return False


async def _install_flow(client: httpx.AsyncClient, base: str, user: str, password: str, email: str) -> bool:
    """Handle Gitea's first-run install flow by POSTing the install form.

    Gitea's rootless image has specific default paths that must match.
    All required form fields must be included or the form silently fails.
    """
    host_port = config.GITEA_HOST_PORT

    try:
        resp = await client.post(
            f"{base}/",
            data={
                # Database
                "db_type": "sqlite3",
                "db_path": "/var/lib/gitea/data/gitea.db",
                # General
                "app_name": "MCP Goose Projects",
                "repo_root_path": "/var/lib/gitea/git/repositories",
                "lfs_root_path": "/var/lib/gitea/git/lfs",
                "log_root_path": "/var/lib/gitea/data/log",
                "run_user": "git",
                "domain": "localhost",
                "http_port": "3000",
                "app_url": f"http://localhost:{host_port}/",
                # Admin account
                "admin_name": user,
                "admin_passwd": password,
                "admin_confirm_passwd": password,
                "admin_email": email,
                # Disable optional features
                "disable_registration": "on",
                "offline_mode": "on",
                "password_algorithm": "pbkdf2_hi",
            },
            follow_redirects=True,
        )
        if resp.status_code in (200, 302):
            log.info("Gitea install flow completed")
            return True
        log.error("Gitea install flow failed: %s %s", resp.status_code, resp.text[:200])
        return False
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError):
        # Gitea closes connection during internal restart — this is expected
        log.info("Gitea install flow completed (connection reset during restart)")
        return True
    except Exception as e:
        log.error("Gitea install flow error: %s", e)
        return False


async def _ensure_token(client: httpx.AsyncClient, base: str, user: str, password: str) -> bool:
    """Create or refresh the mcp-gateway API token."""
    global _api_token
    auth_header = {"Authorization": f"Basic {_basic_auth(user, password)}"}

    # Delete existing mcp-gateway tokens (can't retrieve their values)
    resp = await client.get(f"{base}/api/v1/users/{user}/tokens", headers=auth_header)
    if resp.status_code == 200:
        for t in resp.json():
            if t.get("name") == "mcp-gateway":
                await client.delete(
                    f"{base}/api/v1/users/{user}/tokens/{t['id']}",
                    headers=auth_header,
                )

    # Create fresh token
    resp = await client.post(
        f"{base}/api/v1/users/{user}/tokens",
        headers=auth_header,
        json={"name": "mcp-gateway", "scopes": ["all"]},
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        _api_token = data.get("sha1") or data.get("token", "")
        log.info("Gitea API token created")
        return True

    log.error("Failed to create Gitea token: %s %s", resp.status_code, resp.text[:200])
    return False


def _basic_auth(user: str, password: str) -> str:
    """Encode Basic auth header value."""
    import base64
    return base64.b64encode(f"{user}:{password}".encode()).decode()


async def ensure_repo(name: str) -> str | None:
    """Ensure a repository exists in Gitea. Creates it if missing.

    Args:
        name: Repository name (e.g., "weather-app")

    Returns:
        Clone URL with embedded credentials, or None on failure.
    """
    if not _api_token:
        if not await ensure_setup():
            return None

    base = _base_url()
    user = config.GITEA_ADMIN_USER
    headers = {"Authorization": f"token {_api_token}"}

    async with httpx.AsyncClient(timeout=10) as client:
        # Check if repo exists
        resp = await client.get(f"{base}/api/v1/repos/{user}/{name}", headers=headers)

        if resp.status_code == 200:
            log.info("Repo '%s' exists", name)
        elif resp.status_code == 404:
            log.info("Creating repo '%s'...", name)
            resp = await client.post(
                f"{base}/api/v1/user/repos",
                headers=headers,
                json={
                    "name": name,
                    "auto_init": True,
                    "default_branch": "main",
                    "description": f"Project: {name} (managed by MCP Goose)",
                    "private": False,
                },
            )
            if resp.status_code not in (200, 201):
                log.error("Failed to create repo '%s': %s", name, resp.text[:200])
                return None
        else:
            log.error("Error checking repo '%s': %s %s", name, resp.status_code, resp.text[:200])
            return None

    return f"{_clone_base_url()}/{user}/{name}.git"


async def list_repos() -> list[dict]:
    """List all repositories in Gitea.

    Returns:
        List of dicts with name, description, updated_at, html_url.
    """
    if not _api_token:
        if not await ensure_setup():
            return []

    base = _base_url()
    headers = {"Authorization": f"token {_api_token}"}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base}/api/v1/user/repos",
            headers=headers,
            params={"limit": 50},
        )
        if resp.status_code != 200:
            return []

        # Rewrite html_url to use localhost port (for the user's browser)
        return [
            {
                "name": r["name"],
                "description": r.get("description", ""),
                "updated_at": r.get("updated_at", ""),
                "html_url": r.get("html_url", "").replace(
                    _base_url(), f"http://localhost:{config.GITEA_HOST_PORT}"
                ),
            }
            for r in resp.json()
        ]
