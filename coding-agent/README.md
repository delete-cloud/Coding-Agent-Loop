# Coding Agent

An AI-powered coding agent with interactive TUI, task planning, and safe repository tooling.

## Features

- 🤖 **Interactive TUI** - Rich terminal interface with streaming output
- 📋 **Task Planning** - Built-in todo management with `todo_write`/`todo_read`
- 🎨 **Multiple Providers** - OpenAI and Anthropic support
- 🔧 **File Operations** - Read, write, and replace files safely
- 🔍 **Code Search** - Grep and glob for exploring codebases
- ⚡ **Shell Execution** - Run commands with safety controls

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd coding-agent

# Install dependencies
uv sync --all-extras
```

## Usage

### Interactive Mode (Default)

Start an interactive coding session:

```bash
# Just run coding-agent - starts interactive REPL (default)
uv run python -m coding_agent

# Or explicitly
uv run python -m coding_agent repl

# With custom settings
uv run python -m coding_agent repl \
  --model gpt-4 \
  --api-key $OPENAI_API_KEY \
  --repo /path/to/project
```

### Inside REPL

```
🤖 Coding Agent | Model: gpt-4 | Steps: 0
─────────────────────────────────────────

[0] > fix the bug in utils.py
[Agent thinks and streams response...]

[1] > /plan
Current Plan:
[>] 1. Read utils.py
[ ] 2. Identify the bug
[ ] 3. Fix and test

[2] > /model claude-sonnet
Model changed to: claude-sonnet

[3] > /exit
Goodbye!
```

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/exit` or `/quit` | Exit the agent |
| `/clear` | Clear the screen |
| `/plan` | Show current plan |
| `/model [name]` | Show or change model |
| `/tools` | List available tools |

## Parallel Tool Execution

The agent automatically executes independent tool calls in parallel to reduce latency:

```bash
# 3 file reads that would take 300ms sequentially → 100ms in parallel
uv run python -m coding_agent run --goal "Read file1.py, file2.py, and file3.py"
```

### Configuration

```bash
# Disable parallel execution
uv run python -m coding_agent run --goal "..." --no-parallel

# Configure max parallelism (default: 5)
uv run python -m coding_agent run --goal "..." --max-parallel 10
```

### Safety

The agent detects dependencies and only parallelizes safe operations:
- ✅ **Parallel:** `file_read(a) + file_read(b)` - different files
- ✅ **Parallel:** `file_read(a) + grep(pattern)` - no dependency
- ❌ **Sequential:** `file_read(a) + file_write(a)` - same file
- ❌ **Sequential:** `file_write(a) + file_write(b)` - multiple writes (conservative)

### Batch Mode

Run a single task (for scripts/CI):

```bash
# Headless mode
uv run python -m coding_agent run \
  --goal "fix the bug in utils.py" \
  --api-key $OPENAI_API_KEY

# With TUI display
uv run python -m coding_agent run \
  --goal "refactor main.py" \
  --api-key $OPENAI_API_KEY \
  --tui
```

### Environment Variables

```bash
export AGENT_API_KEY=sk-...
export AGENT_MODEL=gpt-4
export AGENT_PROVIDER=openai  # or anthropic

# Then run without --api-key
uv run python -m coding_agent
```

## Architecture

```
coding-agent/
├── src/coding_agent/
│   ├── cli/              # CLI and REPL
│   ├── core/             # Agent loop, tape, context
│   ├── providers/        # LLM providers (OpenAI, Anthropic)
│   ├── tools/            # Tool implementations
│   ├── ui/               # TUI components
│   └── wire.py           # Wire protocol
└── tests/                # Test suite
```

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run specific test
uv run pytest tests/core/test_loop.py -v

# Format code
uv run ruff format src/
```

## License

MIT
