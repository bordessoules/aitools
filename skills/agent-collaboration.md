---
name: agent-collaboration
description: Multi-agent collaboration via Gitea — delegate tasks to CLI agents, manage PRs and reviews
---

# Agent Collaboration Workflow

Delegate coding tasks to AI agents (Goose, Kimi, Qwen Code) that work on Gitea repos.
Each agent gets an isolated branch, works autonomously, then submits changes for review.

## Architecture

- **MCP Gateway** (Docker, :8000): search, fetch, process, kb_*, gitea_* tools
- **Agent Runner** (host, stdio): delegate_agent, list_agents
- **Gitea** (host, :3001): Git repos, branches, PRs, issues
- **LM Studio** (bluefin:1234): gpt-oss-20b (agent brain)

## Quick Start

```
1. gitea_create_repo("my-project", "Agent collaboration test")
2. gitea_create_issue("owner", "my-project", "Add hello.py", "Create a Python hello world script")
3. delegate_agent(task="Create hello.py with a greeting function", owner="owner", repo="my-project", branch="feature/hello")
4. gitea_create_pr("owner", "my-project", "Add hello.py", "Created by Goose agent", "feature/hello")
5. delegate_agent(task="Review PR #1 and check code quality", owner="owner", repo="my-project", branch="main", system_prompt="You are a code reviewer. Read the PR changes and provide feedback.")
6. gitea_add_pr_review("owner", "my-project", 1, "LGTM - clean code", "APPROVE")
```

## Available Tools

### Gitea Tools (in MCP Gateway)

| Tool | Purpose |
|------|---------|
| `gitea_list_repos()` | List all repos |
| `gitea_create_repo(name, description, private)` | Create repo |
| `gitea_list_branches(owner, repo)` | List branches |
| `gitea_create_branch(owner, repo, branch, from)` | Create branch |
| `gitea_get_file(owner, repo, filepath, branch)` | Read file |
| `gitea_put_file(owner, repo, filepath, content, message, branch)` | Write file (commit) |
| `gitea_create_pr(owner, repo, title, body, head, base)` | Create PR |
| `gitea_list_prs(owner, repo, state)` | List PRs |
| `gitea_add_pr_review(owner, repo, pr_index, body, event)` | Review PR |
| `gitea_create_issue(owner, repo, title, body)` | Create issue |
| `gitea_list_issues(owner, repo, state)` | List issues |

### Agent Runner Tools (host-side)

| Tool | Purpose |
|------|---------|
| `list_agents()` | Show available agents |
| `delegate_agent(task, owner, repo, branch, agent, max_turns)` | Run agent on a repo |

## Workflow Patterns

### Pattern 1: Developer + Reviewer

```
Claude Code (orchestrator)
  → delegate_agent(task="implement feature X", branch="feature/x", agent="goose")
  → gitea_create_pr(head="feature/x")
  → delegate_agent(task="review PR #N", branch="main", system_prompt="You are a reviewer")
  → gitea_add_pr_review(event="APPROVE" or "REQUEST_CHANGES")
```

### Pattern 2: Issue-Driven

```
gitea_create_issue("Add authentication")
  → delegate_agent(task="Implement issue #1: Add authentication", branch="feature/auth")
  → gitea_create_pr(body="Closes #1")
```

### Pattern 3: Parallel Agents

```
delegate_agent(task="Build frontend", branch="feature/frontend", agent="goose")
delegate_agent(task="Build backend API", branch="feature/backend", agent="goose")
  → Two branches, two PRs, merge sequentially
```

## Agent Capabilities

| Agent | Model | Strengths |
|-------|-------|-----------|
| **Goose** | gpt-oss-20b (LM Studio) | General coding, MCP tools, file ops |
| **Kimi** (future) | Kimi K2.5 | --work-dir flag, subagent system |
| **Qwen Code** (future) | Qwen3-Coder | Code generation, large context |

## Notes

- Agents work in temp directories (cloned from Gitea), not your local workspace
- Each delegation is a fresh clone — no state between delegations
- Agents can use MCP gateway tools (search, fetch, process) during their work
- max_turns controls agent autonomy (default: 10, increase for complex tasks)
- Agent timeout is 10 minutes per delegation
