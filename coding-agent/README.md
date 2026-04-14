# Coding Agent

`coding_agent` is the application layer in this repository. It is built on top of
`agentkit`, a reusable hook-based agent runtime that provides the pipeline,
plugin registry, tape model, tool schemas, and directive execution.

Today the repo contains both:

- `src/agentkit/` - framework/runtime code
- `src/coding_agent/` - the concrete coding assistant CLI, TUI, HTTP, tools, and plugins

## Current Capabilities

- Interactive REPL with Rich streaming output and shell mode
- Batch execution via `python -m coding_agent run --goal ...`
- HTTP server mode via `python -m coding_agent serve`
- Hook-driven runtime built from `agentkit` pipeline stages and plugins
- Tooling for file reads/writes, grep/glob, shell commands, file patching, planning, web search, and subagents
- Provider support for `openai`, `anthropic`, `copilot`, `kimi`, `kimi-code`, and `kimi-code-anthropic`
- Approval policies for risky tools and external requests
- Dependency-aware parallel tool execution
- Topic detection, context summarization, memory grounding, session metrics, skills, and MCP server integration
- JSONL-backed tape persistence under `./data/tapes/` by default

## Installation

```bash
git clone <repo-url>
cd coding-agent
uv sync --all-extras
```

## Usage

### REPL Mode

```bash
# Default entrypoint: starts the interactive REPL
uv run python -m coding_agent

# Explicit REPL command
uv run python -m coding_agent repl

# With overrides
uv run python -m coding_agent repl \
  --repo /path/to/project \
  --provider openai \
  --model gpt-4o
```

Inside the REPL:

- Type normal text to talk to the agent
- Type `!` on an empty prompt to enter shell mode
- Use slash commands for session controls

Shell behavior differs by execution path:

- REPL `!` shell mode uses a real shell, so chaining syntax like `&&`, `||`, pipes, and redirects works there.
- The `bash_run` tool is a single-command executor with safety restrictions, so it rejects `&&`, `||`, pipes, redirects, `;`, and backgrounding. Split those into separate tool calls.

Supported slash commands today:

| Command | Description |
| --- | --- |
| `/help` | Show available commands |
| `/exit`, `/quit` | Exit the agent |
| `/clear` | Clear the screen |
| `/plan` | Show the current planner state |
| `/model [name]` | Show or change the model for the next turn |
| `/tools` | List registered tools |
| `/skill [name|off]` | List or activate skills |
| `/thinking ...` | Toggle thinking mode and effort |
| `/mcp [reload]` | Inspect or reload MCP servers |

### Batch Mode

```bash
# Headless
uv run python -m coding_agent run \
  --goal "fix the bug in utils.py" \
  --repo .

# Rich streaming TUI in batch mode
uv run python -m coding_agent run \
  --goal "refactor main.py" \
  --repo . \
  --tui
```

Parallel execution is enabled by default for safe independent tool calls:

```bash
uv run python -m coding_agent run --goal "read a.py, b.py, and c.py"
uv run python -m coding_agent run --goal "..." --no-parallel
uv run python -m coding_agent run --goal "..." --max-parallel 10
```

### HTTP Server

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

The HTTP server uses the same pipeline stack as REPL and batch mode through the
`coding_agent.app.create_agent()` factory and `PipelineAdapter`.

## Providers And Environment

Common environment variables:

```bash
export AGENT_API_KEY=...
export AGENT_PROVIDER=openai
export AGENT_MODEL=gpt-4o
export AGENT_BASE_URL=https://...
```

Provider-specific fallback keys currently supported by the implementation:

- `GITHUB_TOKEN` for `copilot`
- `MOONSHOT_API_KEY` for `kimi`
- `KIMI_CODE_API_KEY` for `kimi-code` and `kimi-code-anthropic`

## Repository Layout

```text
src/
├── agentkit/        # generic runtime: pipeline, hooks, tape, tools, directives
└── coding_agent/    # application layer: CLI, plugins, providers, UI, wire, tools

docs/
├── AGENTKIT-ARCHITECTURE.md
├── AGENTKIT-ARCHITECTURE-zh.md
├── CODING-AGENT-ARCHITECTURE.md
└── CODING-AGENT-ARCHITECTURE-zh.md
```

## Architecture Summary

- `agentkit` defines the 7-stage turn pipeline and 14 hook specs
- `coding_agent` wires that runtime into a concrete plugin set
- `coding_agent.app.create_agent()` is the main composition root
- `PipelineAdapter` bridges runtime events to wire messages for Rich, headless, and HTTP consumers
- `CoreToolsPlugin` is the main tool surface; `SkillsPlugin` and `MCPPlugin` extend the agent beyond local file/shell tools

## Recommended Workflow Entry

For work in this repository, use the workflow layers like this:

- `AGENTS.md` sets the repository defaults and ADR rules.
- `.agents/skills/adr-first-workflow/SKILL.md` helps decide whether the task needs an ADR, how to shape scope, and which target tests should gate the work.
- `.opencode/prompts/README.md` and the local prompt set run the bounded engineer/reviewer/verifier loop once the task is already scoped.

Quick decision rule:

1. If you are not sure whether the task needs an ADR, or you are not yet sure about scope or target tests, start with `adr-first-workflow`.
2. If the goal, scope, relevant ADRs, and target tests are already known, go straight to the task packet and the `.opencode/prompts` bounded loop.
3. If the task changes persistence, protocol, data model, or cross-module boundaries, record that decision through the ADR workflow before implementation.

Short version: use the skill for workflow judgment, and use the prompt set for disciplined execution.

For more detail, see:

- [docs/AGENTKIT-ARCHITECTURE.md](docs/AGENTKIT-ARCHITECTURE.md)
- [docs/AGENTKIT-ARCHITECTURE-zh.md](docs/AGENTKIT-ARCHITECTURE-zh.md)
- [docs/CODING-AGENT-ARCHITECTURE.md](docs/CODING-AGENT-ARCHITECTURE.md)
- [docs/CODING-AGENT-ARCHITECTURE-zh.md](docs/CODING-AGENT-ARCHITECTURE-zh.md)

## Development

```bash
uv run pytest tests/ -v
uv run ruff format src/
```

Useful targeted checks while changing runtime or product-layer behavior:

```bash
uv run pytest tests/agentkit/ -v
uv run pytest tests/coding_agent/ -v
uv run pytest tests/cli/ -v
```

## License

MIT
