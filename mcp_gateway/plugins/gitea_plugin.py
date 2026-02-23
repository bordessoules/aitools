"""Gitea plugin -- MCP tools for browsing repos, managing PRs, and code review.

Provides tools:
- git_list_repos() -- list all repositories
- git_list_branches() -- list branches in a repo
- git_get_contents() -- read file or directory from a repo
- git_list_pulls() -- list pull requests
- git_create_pull() -- create a pull request
- git_get_pull_diff() -- get diff for a pull request
- git_create_review() -- review a pull request (approve/reject/comment)
- git_merge_pull() -- merge a pull request

These tools complement the agent plugin's _git_pre_task/_git_post_task
(which handle automatic clone/commit/push during agent dispatch).
This plugin gives Chat UI and agents direct access to git operations.
"""

from .. import gitea
from ..logger import get_logger

log = get_logger("plugin.gitea")


def register(mcp):
    """Register Gitea tools with FastMCP."""

    @mcp.tool()
    async def git_list_repos() -> str:
        """
        List all git repositories.

        Returns repository names, descriptions, and browse URLs.
        """
        if not await gitea.is_available():
            return "Error: Gitea is not available."

        repos = await gitea.list_repos()
        if not repos:
            return "No repositories found."

        lines = [f"Found {len(repos)} repo(s):\n"]
        for r in repos:
            lines.append(f"  - {r['name']}")
            if r.get("description"):
                lines.append(f"    {r['description']}")
            lines.append(f"    Updated: {r.get('updated_at', '')[:10]} | {r.get('html_url', '')}")
        return "\n".join(lines)

    @mcp.tool()
    async def git_list_branches(repo: str) -> str:
        """
        List branches in a repository.

        Args:
            repo: Repository name (e.g., "my-project")

        Returns:
            Branch names and latest commit SHAs.
        """
        branches = await gitea.list_branches(repo)
        if not branches:
            return f"No branches found for '{repo}' (repo may not exist)."

        lines = [f"Branches in '{repo}':\n"]
        for b in branches:
            sha = b.get("commit_sha", "")[:8]
            lines.append(f"  - {b['name']} ({sha})")
        return "\n".join(lines)

    @mcp.tool()
    async def git_get_contents(repo: str, path: str = "", ref: str = "") -> str:
        """
        Read a file or list a directory from a repository.

        Args:
            repo: Repository name
            path: File or directory path (empty = repo root)
            ref: Branch name, tag, or commit SHA (empty = default branch)

        Returns:
            File content (for files) or directory listing (for directories).
        """
        result = await gitea.get_contents(repo, path, ref)
        if result is None:
            return f"Not found: '{path}' in '{repo}'" + (f" at ref '{ref}'" if ref else "")

        if result["type"] == "dir":
            entries = result["entries"]
            lines = [f"Directory '{path or '/'}' in '{repo}' ({len(entries)} entries):\n"]
            for e in entries:
                icon = "/" if e["type"] == "dir" else ""
                size = f" ({e['size']}B)" if e["type"] == "file" else ""
                lines.append(f"  {e['name']}{icon}{size}")
            return "\n".join(lines)

        # File
        return result["content"]

    @mcp.tool()
    async def git_list_pulls(repo: str, state: str = "open") -> str:
        """
        List pull requests for a repository.

        Args:
            repo: Repository name
            state: Filter by state: "open", "closed", or "all"

        Returns:
            Pull request numbers, titles, branches, and authors.
        """
        pulls = await gitea.list_pulls(repo, state)
        if not pulls:
            return f"No {state} pull requests in '{repo}'."

        lines = [f"{len(pulls)} {state} PR(s) in '{repo}':\n"]
        for p in pulls:
            lines.append(f"  #{p['number']}: {p['title']}")
            lines.append(f"    {p['head']} -> {p['base']} | by {p['user']} | {p.get('created_at', '')[:10]}")
        return "\n".join(lines)

    @mcp.tool()
    async def git_create_pull(
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> str:
        """
        Create a pull request.

        Args:
            repo: Repository name
            title: PR title
            head: Source branch name
            base: Target branch name (default: "main")
            body: PR description (optional)

        Returns:
            PR number and URL, or error message.
        """
        result = await gitea.create_pull(repo, title, head, base, body)
        if result is None:
            return f"Failed to create PR in '{repo}'. Check that branches '{head}' and '{base}' exist."
        return f"Created PR #{result['number']}: {result.get('html_url', '')}"

    @mcp.tool()
    async def git_get_pull_diff(repo: str, pull_number: int) -> str:
        """
        Get the diff for a pull request.

        Args:
            repo: Repository name
            pull_number: PR number

        Returns:
            Unified diff text showing all changes in the PR.
        """
        diff = await gitea.get_pull_diff(repo, pull_number)
        if diff is None:
            return f"Could not get diff for PR #{pull_number} in '{repo}'."
        if not diff.strip():
            return f"PR #{pull_number} has no changes (empty diff)."
        return diff

    @mcp.tool()
    async def git_create_review(
        repo: str,
        pull_number: int,
        body: str,
        event: str = "COMMENT",
    ) -> str:
        """
        Review a pull request.

        Args:
            repo: Repository name
            pull_number: PR number
            body: Review comment text
            event: Review action: "COMMENT", "APPROVE", or "REQUEST_CHANGES"

        Returns:
            Confirmation of the review action.
        """
        result = await gitea.create_review(repo, pull_number, body, event)
        if result is None:
            return f"Failed to create review on PR #{pull_number} in '{repo}'."
        return f"Review submitted on PR #{pull_number}: {event} (review id: {result.get('id', '')})"

    @mcp.tool()
    async def git_merge_pull(
        repo: str,
        pull_number: int,
        method: str = "merge",
    ) -> str:
        """
        Merge a pull request.

        Args:
            repo: Repository name
            pull_number: PR number
            method: Merge method: "merge", "rebase", or "squash"

        Returns:
            Success or failure message.
        """
        ok = await gitea.merge_pull(repo, pull_number, method)
        if not ok:
            return f"Failed to merge PR #{pull_number} in '{repo}'. It may have conflicts or already be merged."
        return f"PR #{pull_number} merged successfully ({method})."


async def health_checks() -> list[tuple[str, bool]]:
    """Check Gitea availability for git tools."""
    checks = []
    try:
        if await gitea.is_available():
            ok = await gitea.ensure_setup()
            if ok:
                checks.append(("[OK] Gitea Git Tools", True))
            else:
                checks.append(("[WARN] Gitea reachable but setup failed", False))
        else:
            checks.append(("[INFO] Gitea not reachable - git tools disabled", False))
    except Exception:
        checks.append(("[INFO] Gitea not available", False))
    return checks


PLUGIN = {
    "name": "gitea",
    "env_var": "ENABLE_CODING_AGENT",
    "default_enabled": False,
    "register": register,
    "health_checks": health_checks,
}
