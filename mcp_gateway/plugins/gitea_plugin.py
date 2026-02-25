"""Gitea plugin -- MCP tools for browsing repos and managing PRs.

Two combined tools replace the original eight, cutting ~700 tokens of
schema overhead while keeping full functionality:

- git_browse(repo, action, ...) -- list repos, branches, read files
- git_pr(repo, action, ...)     -- list/create/diff/review/merge PRs
"""

from .. import gitea
from ..logger import get_logger

log = get_logger("plugin.gitea")


def register(mcp):
    """Register Gitea tools with FastMCP."""

    @mcp.tool()
    async def git_browse(
        action: str = "list_repos",
        repo: str = "",
        path: str = "",
        ref: str = "",
    ) -> str:
        """
        Browse git repositories — list repos, branches, or read files.

        Actions:
        - "list_repos": List all repositories (repo not required)
        - "list_branches": List branches in a repo
        - "read": Read a file or directory from a repo

        Args:
            action: "list_repos", "list_branches", or "read"
            repo: Repository name (required for list_branches and read)
            path: File or directory path (for read, empty = repo root)
            ref: Branch/tag/SHA (for read, empty = default branch)
        """
        if action == "list_repos":
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

        if not repo:
            return "Error: 'repo' is required for this action."

        if action == "list_branches":
            branches = await gitea.list_branches(repo)
            if not branches:
                return f"No branches found for '{repo}' (repo may not exist)."
            lines = [f"Branches in '{repo}':\n"]
            for b in branches:
                sha = b.get("commit_sha", "")[:8]
                lines.append(f"  - {b['name']} ({sha})")
            return "\n".join(lines)

        if action == "read":
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
            return result["content"]

        return f"Unknown action '{action}'. Use: list_repos, list_branches, read"

    @mcp.tool()
    async def git_pr(
        repo: str,
        action: str = "list",
        pull_number: int = 0,
        state: str = "open",
        title: str = "",
        head: str = "",
        base: str = "main",
        body: str = "",
        event: str = "COMMENT",
        method: str = "merge",
    ) -> str:
        """
        Manage pull requests — list, create, diff, review, or merge.

        Actions and their required args:
        - "list": List PRs (state: "open"/"closed"/"all")
        - "create": Create PR (title, head required; base, body optional)
        - "diff": Get PR diff (pull_number required)
        - "review": Review PR (pull_number, body required; event: COMMENT/APPROVE/REQUEST_CHANGES)
        - "merge": Merge PR (pull_number required; method: merge/rebase/squash)

        Args:
            repo: Repository name
            action: "list", "create", "diff", "review", or "merge"
            pull_number: PR number (for diff, review, merge)
            state: PR state filter (for list)
            title: PR title (for create)
            head: Source branch (for create)
            base: Target branch (for create, default: "main")
            body: PR description (create) or review comment (review)
            event: Review action (for review)
            method: Merge method (for merge)
        """
        if action == "list":
            pulls = await gitea.list_pulls(repo, state)
            if not pulls:
                return f"No {state} pull requests in '{repo}'."
            lines = [f"{len(pulls)} {state} PR(s) in '{repo}':\n"]
            for p in pulls:
                lines.append(f"  #{p['number']}: {p['title']}")
                lines.append(f"    {p['head']} -> {p['base']} | by {p['user']} | {p.get('created_at', '')[:10]}")
            return "\n".join(lines)

        if action == "create":
            if not title or not head:
                return "Error: 'title' and 'head' are required to create a PR."
            result = await gitea.create_pull(repo, title, head, base, body)
            if result is None:
                return f"Failed to create PR in '{repo}'. Check that branches '{head}' and '{base}' exist."
            return f"Created PR #{result['number']}: {result.get('html_url', '')}"

        if action == "diff":
            if not pull_number:
                return "Error: 'pull_number' is required for diff."
            diff = await gitea.get_pull_diff(repo, pull_number)
            if diff is None:
                return f"Could not get diff for PR #{pull_number} in '{repo}'."
            if not diff.strip():
                return f"PR #{pull_number} has no changes (empty diff)."
            return diff

        if action == "review":
            if not pull_number or not body:
                return "Error: 'pull_number' and 'body' are required for review."
            result = await gitea.create_review(repo, pull_number, body, event)
            if result is None:
                return f"Failed to create review on PR #{pull_number} in '{repo}'."
            return f"Review submitted on PR #{pull_number}: {event} (review id: {result.get('id', '')})"

        if action == "merge":
            if not pull_number:
                return "Error: 'pull_number' is required for merge."
            ok = await gitea.merge_pull(repo, pull_number, method)
            if not ok:
                return f"Failed to merge PR #{pull_number} in '{repo}'. It may have conflicts or already be merged."
            return f"PR #{pull_number} merged successfully ({method})."

        return f"Unknown action '{action}'. Use: list, create, diff, review, merge"


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
