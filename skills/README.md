# Agent Skills for MCP Gateway

## Architecture Overview

**Skills** and **MCP Tools** are complementary layers:
- **MCP Tools** = atomic capabilities (search, fetch, process) -- the "ingredients"
- **Skills** = workflow recipes that orchestrate tools -- the "recipe cards"
- **Agents** = the cook that picks the right recipe and ingredients

Skills use **Progressive Disclosure** to stay context-efficient:
1. **Level 1**: name + description (~100 tokens, loaded at startup)
2. **Level 2**: full SKILL.md (loaded when skill triggers)
3. **Level 3**: bundled files in `references/`, `scripts/`, `assets/` (loaded on demand)

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `search(query)` | Web search via SearXNG |
| `fetch(url)` | Fetch any URL (web, PDF, image) |
| `fetch_section(url, section)` | Get specific section from large docs |
| `add_to_knowledge_base(url)` | Save document to OpenSearch KB |
| `kb_search(query)` | Search saved documents |
| `kb_list()` | List KB contents |
| `kb_remove(url)` | Remove from KB |
| `process(content, task, prompt)` | Text processing via LLM (summarize/extract/translate/analyze) |

## Infrastructure

- **master** (Windows): Docker host running gateway, SearXNG, OpenSearch, Docling
- **bluefin** (Linux): LM Studio serving two models via Tailscale
  - `qwen/qwen3-vl-4b` on 3060 12GB (vision: screenshots, images, OCR)
  - `openai/gpt-oss-20b` on 5060ti 16GB (text: summarize, analyze, generate)

## Skill Patterns

From research on Anthropic's Agent Skills architecture and multi-agent orchestration:

| Pattern | Use Case | Example |
|---------|----------|---------|
| Script Automation | Complex operations offloaded to code | CI/CD pipelines |
| Read-Process-Write | File transformation | Doc ingestion |
| Search-Analyze-Report | Codebase or data analysis | Fact checking |
| Command Chain | Sequential dependent steps | Build + test + deploy |
| Wizard Multi-Step | User input at each step | Project setup |
| Template-Based Generation | Structured output from templates | Report generation |
| Iterative Refinement | Broad scan -> deep analysis -> recommendations | Code review |
| Context Aggregation | Combine multiple sources | Project summary |

## Recommended Skills to Build

### P0 - Build First

| Skill | Description | Pattern | MCP Tools |
|-------|-------------|---------|-----------|
| `deep-research` | Multi-source research: search, fetch, summarize, save | Pipeline | search, fetch, fetch_section, process, add_to_knowledge_base |
| `fact-check` | Cross-reference claims against KB + web with confidence score | Search-Analyze-Report | kb_search, search, process |
| `doc-ingest` | Fetch any URL, extract key data, save to KB | Read-Process-Write | fetch, fetch_section, process, add_to_knowledge_base |

### P1 - High Value

| Skill | Description | Pattern | MCP Tools |
|-------|-------------|---------|-----------|
| `visual-debug` | Screenshot -> qwen3-vl extracts code/error -> gpt-oss suggests patch | Script Automation (dual-LLM) | fetch (image), process |
| `knowledge-linker` | Scan KB for related entries, cross-link, gap analysis | Context Aggregation | kb_search, kb_list, process |
| `competitive-intel` | Monitor URLs for changes, diff against KB, alert on updates | Iterative Refinement | search, fetch, kb_search, process |

### P2 - Nice to Have

| Skill | Description | Pattern | MCP Tools |
|-------|-------------|---------|-----------|
| `report-generator` | Template-based report from KB data | Template-Based Generation | kb_search, process |
| `recursive-refiner` | Iteratively improve an incomplete answer | Iterative Refinement | process, search, kb_search |

## Dual-LLM Unique Value

These skills leverage both models in a pipeline (vision -> text) that neither could do alone:

1. **Scene-aware debugging**: qwen3-vl reads screenshot layout (OCR + visual context) -> gpt-oss-20b reasons about the bug and generates a patch
2. **Multimodal data review**: qwen3-vl extracts tables/charts from images -> gpt-oss-20b produces analytical reports
3. **UI sketch to code**: qwen3-vl interprets wireframe sketches -> gpt-oss-20b generates implementation code

## Orchestration Strategy

**Supervisor + Pipeline hybrid**: The calling agent (Claude Code, Goose) acts as supervisor, decomposing tasks and delegating to the gateway's tool chain. Within the gateway, tools execute as a pipeline (search -> fetch -> process -> save). For heavy parallel research, use a swarm pattern with multiple fetch+process chains, then aggregate.

## Existing Skills

- `web-research.md` - Workflow for all 8 tools with content type handling
- `text-processing.md` - Process tool patterns and infrastructure specs
- `infrastructure.md` - Full Tailscale network map, Docker services, LM Studio models

## Sources

- [Anthropic: Equipping Agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Han Lee: Claude Agent Skills Deep Dive](https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/)
- [Anthropic: Agent Skills Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Multi-Agent Orchestration Patterns](https://fast.io/resources/multi-agent-orchestration-patterns/)
