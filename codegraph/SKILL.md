---
name: codegraph
description: Initialize and use CodeGraph — pre-indexed knowledge graph for 92% faster code exploration. Auto-init when .codegraph/ missing. Use when exploring unfamiliar codebases, tracing call flows, or before editing.
---

## codegraph

Initialize CodeGraph when a project lacks `.codegraph/`, and use it as the PRIMARY code exploration method.

### Auto-Init (when `.codegraph/` is missing)

1. Check installation:
```bash
codegraph --version
```
If not found → `npm install -g @colbymchenry/codegraph`

2. Initialize and index the project:
```bash
codegraph init -i
```

3. Verify:
```bash
codegraph status
```
Expected: `Files: N, Nodes: N, Backend: native`

4. If `Backend: wasm` (slow) → `npm rebuild better-sqlite3`

### Using CodeGraph for Exploration

Once `.codegraph/` exists, ALWAYS prefer CodeGraph MCP tools:

| Tool | What for |
|------|----------|
| `codegraph_explore` | **Primary exploration** — returns full source from all relevant files in one call |
| `codegraph_search` | Find symbols by name |
| `codegraph_callers` / `codegraph_callees` | Trace call flow |
| `codegraph_impact` | Check what's affected before editing |
| `codegraph_node` | Get single symbol details |

### Rules

- Explore agents: pass this in prompt → "This project has CodeGraph. Use codegraph_explore as PRIMARY tool."
- NEVER re-read files codegraph already returned source for
- CodeGraph > grep/glob/Read/Find for any code understanding task
