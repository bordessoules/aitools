"""
Gitea REST API client for MCP Gateway.

Thin async wrapper over Gitea's /api/v1 endpoints.
Used for agent collaboration: repos, branches, PRs, issues, files.
"""

import base64
from typing import Optional

import httpx

import config


def _headers() -> dict:
    """Auth headers for Gitea API."""
    return {"Authorization": f"token {config.GITEA_TOKEN}"}


def _api(path: str) -> str:
    """Build full API URL."""
    return f"{config.GITEA_URL}/api/v1{path}"


async def _request(method: str, path: str, json: dict | None = None) -> dict | list | str:
    """Make an authenticated Gitea API request."""
    if not config.GITEA_TOKEN:
        return {"error": "GITEA_TOKEN not configured. Set it in .env"}

    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_GITEA) as client:
            resp = await client.request(method, _api(path), headers=_headers(), json=json)
            if resp.status_code >= 400:
                return {"error": f"Gitea API {resp.status_code}: {resp.text[:200]}"}
            if resp.status_code == 204:
                return {"ok": True}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": f"Gitea request timed out after {config.TIMEOUT_GITEA}s"}
    except Exception as e:
        return {"error": f"Gitea request failed: {e}"}


# =============================================================================
# REPOSITORIES
# =============================================================================

async def list_repos() -> str:
    """List repositories for the authenticated user."""
    data = await _request("GET", "/user/repos?limit=50")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if not data:
        return "No repositories found."

    lines = [f"Repositories ({len(data)}):\n"]
    for repo in data:
        private = " [private]" if repo.get("private") else ""
        lines.append(f"  {repo['full_name']}{private}")
        if repo.get("description"):
            lines.append(f"    {repo['description'][:80]}")
    return "\n".join(lines)


async def create_repo(name: str, description: str = "", private: bool = False) -> str:
    """Create a new repository."""
    data = await _request("POST", "/user/repos", json={
        "name": name,
        "description": description,
        "private": private,
        "auto_init": True,
    })
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Created repository: {data['full_name']} ({data.get('html_url', '')})"


# =============================================================================
# BRANCHES
# =============================================================================

async def list_branches(owner: str, repo: str) -> str:
    """List branches for a repository."""
    data = await _request("GET", f"/repos/{owner}/{repo}/branches")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if not data:
        return f"No branches found in {owner}/{repo}."

    lines = [f"Branches in {owner}/{repo}:\n"]
    for branch in data:
        lines.append(f"  {branch['name']}")
    return "\n".join(lines)


async def create_branch(owner: str, repo: str, branch_name: str, from_branch: str = "main") -> str:
    """Create a new branch."""
    data = await _request("POST", f"/repos/{owner}/{repo}/branches", json={
        "new_branch_name": branch_name,
        "old_branch_name": from_branch,
    })
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Created branch: {branch_name} (from {from_branch}) in {owner}/{repo}"


# =============================================================================
# FILES
# =============================================================================

async def get_file(owner: str, repo: str, filepath: str, branch: str = "main") -> str:
    """Read a file from a repository."""
    data = await _request("GET", f"/repos/{owner}/{repo}/contents/{filepath}?ref={branch}")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if isinstance(data, dict) and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return f"File: {filepath} (branch: {branch})\n\n{content}"

    return f"Error: Could not read {filepath}"


async def put_file(
    owner: str, repo: str, filepath: str, content: str, message: str, branch: str = "main"
) -> str:
    """Create or update a file in a repository (creates a commit)."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    # Check if file exists to get its SHA (needed for updates)
    existing = await _request("GET", f"/repos/{owner}/{repo}/contents/{filepath}?ref={branch}")
    sha = None
    if isinstance(existing, dict) and existing.get("sha"):
        sha = existing["sha"]

    body: dict = {
        "content": encoded,
        "message": message,
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    method = "PUT" if sha else "POST"
    data = await _request(method, f"/repos/{owner}/{repo}/contents/{filepath}", json=body)
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    action = "Updated" if sha else "Created"
    return f"{action} file: {filepath} on branch {branch} ({message})"


# =============================================================================
# PULL REQUESTS
# =============================================================================

async def create_pull_request(
    owner: str, repo: str, title: str, body: str, head: str, base: str = "main"
) -> str:
    """Create a pull request."""
    data = await _request("POST", f"/repos/{owner}/{repo}/pulls", json={
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    })
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Created PR #{data['number']}: {title} ({head} -> {base})\n  URL: {data.get('html_url', '')}"


async def list_pull_requests(owner: str, repo: str, state: str = "open") -> str:
    """List pull requests."""
    data = await _request("GET", f"/repos/{owner}/{repo}/pulls?state={state}&limit=20")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if not data:
        return f"No {state} pull requests in {owner}/{repo}."

    lines = [f"Pull requests in {owner}/{repo} ({state}):\n"]
    for pr in data:
        lines.append(f"  #{pr['number']}: {pr['title']}")
        lines.append(f"    {pr['head']['label']} -> {pr['base']['label']}  [{pr['state']}]")
        if pr.get("body"):
            lines.append(f"    {pr['body'][:100]}")
    return "\n".join(lines)


async def get_pull_request(owner: str, repo: str, index: int) -> str:
    """Get a specific pull request with details."""
    data = await _request("GET", f"/repos/{owner}/{repo}/pulls/{index}")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    lines = [
        f"PR #{data['number']}: {data['title']}",
        f"  State: {data['state']}",
        f"  Branch: {data['head']['label']} -> {data['base']['label']}",
        f"  URL: {data.get('html_url', '')}",
    ]
    if data.get("body"):
        lines.append(f"  Description:\n    {data['body'][:500]}")
    return "\n".join(lines)


async def add_pr_comment(owner: str, repo: str, index: int, body: str) -> str:
    """Add a comment to a pull request (PRs share issue index in Gitea)."""
    data = await _request("POST", f"/repos/{owner}/{repo}/issues/{index}/comments", json={
        "body": body,
    })
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Added comment to PR #{index}"


async def create_pr_review(
    owner: str, repo: str, index: int, body: str, event: str = "COMMENT"
) -> str:
    """Create a review on a pull request.

    event: APPROVE, REQUEST_CHANGES, or COMMENT
    """
    valid_events = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
    if event.upper() not in valid_events:
        return f"Error: event must be one of {valid_events}"

    data = await _request("POST", f"/repos/{owner}/{repo}/pulls/{index}/reviews", json={
        "body": body,
        "event": event.upper(),
    })
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Submitted {event} review on PR #{index}"


# =============================================================================
# ISSUES
# =============================================================================

async def create_issue(
    owner: str, repo: str, title: str, body: str = "", labels: Optional[list[str]] = None
) -> str:
    """Create an issue."""
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    data = await _request("POST", f"/repos/{owner}/{repo}/issues", json=payload)
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return f"Created issue #{data['number']}: {title}\n  URL: {data.get('html_url', '')}"


async def list_issues(owner: str, repo: str, state: str = "open") -> str:
    """List issues (excludes pull requests)."""
    data = await _request("GET", f"/repos/{owner}/{repo}/issues?state={state}&type=issues&limit=20")
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if not data:
        return f"No {state} issues in {owner}/{repo}."

    lines = [f"Issues in {owner}/{repo} ({state}):\n"]
    for issue in data:
        labels = ", ".join(l["name"] for l in issue.get("labels", []))
        label_str = f" [{labels}]" if labels else ""
        lines.append(f"  #{issue['number']}: {issue['title']}{label_str}")
        if issue.get("body"):
            lines.append(f"    {issue['body'][:100]}")
    return "\n".join(lines)
