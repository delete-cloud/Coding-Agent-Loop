# Coding Agent Loop

This is a **monorepo** housing the agent runtime, application layers, and supporting documentation for the coding-agent ecosystem.

```
.
├── coding-agent/      ← core monorepo package (agentkit runtime + coding_agent app)
├── docs/              ← cross-cutting documentation, lessons, and specs
└── README.md          ← you are here
```

## Packages

| Path | Description | Entrypoint |
|------|-------------|------------|
| [`coding-agent/`](./coding-agent/README.md) | `agentkit` runtime + `coding_agent` CLI / TUI / HTTP server | `python -m coding_agent` |

## Architecture

The repository splits cleanly into two layers:

- **`agentkit`** — a generic, hook-based agent runtime. It defines the turn pipeline, tape model, tool schemas, plugin registry, and directive execution. It is framework code, not tied to any specific use-case.
- **`coding_agent`** — the concrete application built on top of `agentkit`. It provides the interactive REPL, Rich TUI, HTTP server, file/shell tools, web search, subagents, skills, and MCP integration.

Both layers live under [`coding-agent/src/`](./coding-agent/src/):

```
coding-agent/src/
├── agentkit/        # generic runtime
└── coding_agent/    # concrete coding assistant
```

See [`coding-agent/README.md`](./coding-agent/README.md) for full usage, development, and architecture details.

## Local Reference Directories

The following directories are tracked in `.gitignore` and kept locally for reference or development convenience. They are **not** part of the committed monorepo:

- `agent-coding-loop/` — earlier iteration of the loop evaluation harness
- `bub/` — build-system experiments
- `eino-examples/`, `eino-skills/` — eino framework reference
- `opencode/` — opencode agent reference
- `simplebank/` — Go backend reference project (symlink)

## Quick Start

```bash
cd coding-agent
uv sync --all-extras
uv run python -m coding_agent
```

## License

MIT
