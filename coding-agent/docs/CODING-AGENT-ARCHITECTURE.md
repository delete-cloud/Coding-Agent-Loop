# Coding Agent Architecture

> A layered CLI application built on AgentKit for interactive AI-assisted coding.

## 1. Overview

`coding_agent` is the application layer that consumes `agentkit` as a framework. It provides:

- **Multi-mode CLI** — Interactive REPL, batch, and HTTP server modes via Click
- **Adapter pattern** — `PipelineAdapter` translates agentkit pipeline events into a typed wire protocol
- **Wire protocol** — Typed dataclasses (`StreamDelta`, `ToolCallDelta`, `TurnEnd`, etc.) decouple agent logic from presentation
- **Pluggable providers** — Anthropic, OpenAI-compatible, GitHub Copilot, and Kimi-family backends
- **Rich TUI** — Scrollback-based streaming renderer using `prompt_toolkit` + `rich`
- **14 plugins** — All domain behavior injected via agentkit's hook system, including skills and MCP integration

### Relationship to AgentKit

```
┌─────────────────────────────────────────────────────┐
│                  coding_agent                        │
│  CLI · Adapter · Wire · Providers · Plugins · UI    │
├─────────────────────────────────────────────────────┤
│                    agentkit                          │
│  Pipeline · Hooks · Tape · Tools · Directives       │
└─────────────────────────────────────────────────────┘
```

AgentKit provides the **mechanism** (pipeline execution, hook dispatch, conversation tape). Coding Agent provides the **policy** (which LLM, which tools, how to render, when to approve).

---

## 2. Layered Architecture

The application follows a strict layered dependency graph. Each layer depends only on layers below it.

```
┌─────────────────────────────────────────────────┐
│                CLI Layer                         │
│  __main__.py · app.py · repl.py                 │
│  input_handler.py · commands.py                 │
│  bash_executor.py                               │
├─────────────────────────────────────────────────┤
│                UI Layer                          │
│  stream_renderer.py · rich_consumer.py          │
│  rich_tui.py · collapse.py · status_footer.py   │
│  approval_prompt.py · headless.py               │
│  http_server.py · components.py · theme.py      │
├─────────────────────────────────────────────────┤
│              Adapter Layer                       │
│  adapter.py · adapter_types.py                  │
├─────────────────────────────────────────────────┤
│              Wire Protocol                       │
│  wire/protocol.py · wire/local.py               │
├─────────────────────────────────────────────────┤
│             Plugin Layer                         │
│  14 plugins implementing agentkit hooks          │
├─────────────────────────────────────────────────┤
│            Provider Layer                        │
│  base.py · anthropic.py · openai_compat.py      │
│  copilot.py                                     │
├─────────────────────────────────────────────────┤
│              Core Layer                          │
│  config.py · session.py · planner.py            │
│  agents/ · skills/ · subagents/                 │
│  evaluation/ · verification/                    │
│  kb.py · metrics.py · tokens.py · redaction.py  │
├─────────────────────────────────────────────────┤
│             Tools Layer                          │
│  file_ops.py · shell.py · planner.py            │
│  file_patch_tool.py · web_search.py · subagent.py │
│  subagent_stub.py                               │
├─────────────────────────────────────────────────┤
│              agentkit (framework)                │
│  Pipeline · HookRuntime · Tape · ToolRegistry   │
└─────────────────────────────────────────────────┘
```

---

## 3. Module Structure

```
src/coding_agent/
├── __main__.py              # Click CLI: main, repl, run, stats, serve
├── adapter.py               # PipelineAdapter: agentkit events → wire protocol
├── adapter_types.py         # StopReason, TurnOutcome dataclasses
├── agent.toml               # Default agent configuration
├── app.py                   # Application factory with create_agent()
│
├── agents/                  # Agent definitions
│   └── __init__.py
│
├── approval/                # Approval policy framework
│   ├── coordinator.py       #   Approval coordination logic
│   ├── policy.py            #   ApprovalPolicy enum, PolicyEngine
│   └── store.py             #   Approval state persistence
│
├── cli/                     # CLI input/output layer
│   ├── repl.py              #   InteractiveSession, REPL loop
│   ├── input_handler.py     #   prompt_toolkit key bindings, shell mode
│   ├── commands.py          #   Slash command registry (/help, /model, etc.)
│   ├── bash_executor.py     #   Direct bash execution for shell mode
│   └── terminal_output.py   #   Output abstraction for prompt_toolkit
│
├── config/                  # Configuration constants
│   └── constants.py         #   Hardcoded defaults
│
├── core/                    # Core domain logic
│   ├── config.py            #   Pydantic Config model, layered loading
│   ├── planner.py           #   Task planner (todo_write/todo_read)
│   └── session.py           #   Session management
│
├── evaluation/              # Evaluation framework
│   ├── __init__.py
│   ├── adapter.py
│   └── metrics.py
│
├── kb.py                    # Knowledge base chunk injection
│
├── metrics.py               # Application metrics
│
├── plugins/                 # AgentKit hook implementations (14 plugins)
│   ├── approval.py          #   approve_tool_call → Approve/Reject/AskUser
│   ├── core_tools.py        #   get_tools + execute_tool → ToolRegistry
│   ├── doom_detector.py     #   on_checkpoint → doom loop detection
│   ├── llm_provider.py      #   provide_llm → LLMProvider factory
│   ├── memory.py            #   build_context + on_turn_end → memory
│   ├── metrics.py           #   on_checkpoint → performance metrics
│   ├── mcp.py               #   mount/get_tools/execute_tool → MCP server tools
│   ├── kb.py                #   build_context → KB chunk injection
│   ├── parallel_executor.py #   execute_tools_batch → parallel execution
│   ├── shell_session.py     #   mount + on_checkpoint → shell state
│   ├── skills.py            #   build_context + execute_tool → skill discovery/activation
│   ├── storage.py           #   provide_storage → JSONL TapeStore
│   ├── summarizer.py        #   resolve_context_window → compression
│   └── topic.py             #   on_checkpoint → topic boundary detection
│
├── providers/               # LLM provider implementations
│   ├── base.py              #   ChatProvider protocol, StreamEvent, ToolCall
│   ├── anthropic.py         #   Anthropic Messages API
│   ├── openai_compat.py     #   OpenAI-compatible Chat Completions
│   └── copilot.py           #   GitHub Copilot (extends OpenAI-compat)
│
├── redaction.py             # Output redaction
│
├── skills/                  # Skill definitions
│   └── __init__.py
│
├── subagents/               # Sub-agent coordination
│   ├── __init__.py
│   └── coordinator.py
│
├── summarizer/              # Context summarization strategies
│   ├── base.py              #   Summarizer protocol
│   ├── llm_summarizer.py    #   LLM-based summarization (future)
│   └── rule_summarizer.py   #   Rule-based truncation
│
├── tokens.py                # Token utilities
│
├── tools/                   # Tool implementations
│   ├── file_ops.py          #   file_read, file_write, file_replace, glob, grep
│   ├── file_patch_tool.py   #   Structured file patching
│   ├── shell.py             #   bash_run with safety controls
│   ├── cache.py             #   Tool result caching
│   ├── planner.py           #   todo_write, todo_read
│   ├── web_search.py        #   web_search tool and backend plumbing
│   ├── subagent.py          #   Sub-agent delegation tool
│   ├── subagent_stub.py     #   Sub-agent stub tool
│   └── sandbox.py           #   sandbox helpers
│
├── ui/                      # Presentation layer
│   ├── stream_renderer.py   #   StreamingRenderer: raw text → Rich panels
│   ├── rich_consumer.py     #   RichConsumer: WireMessage → Renderer calls
│   ├── rich_tui.py          #   Rich TUI components
│   ├── approval_prompt.py   #   Interactive approval UI with previews
│   ├── headless.py          #   HeadlessConsumer: logging-based output
│   ├── http_server.py       #   FastAPI HTTP server mode
│   ├── components.py        #   Shared Rich components
│   ├── collapse.py          #   Collapsible output regions
│   ├── theme.py             #   Color/style definitions
│   ├── schemas.py           #   HTTP API schemas
│   ├── session_manager.py   #   HTTP session management
│   ├── status_footer.py     #   Status footer widget
│   ├── auth.py              #   HTTP API authentication
│   └── rate_limit.py        #   HTTP rate limiting
│
├── wire/                    # Wire protocol
│   ├── protocol.py          #   Message types: StreamDelta, ToolCallDelta, etc.
│   └── local.py             #   LocalWire: async queue-based in-process wire
│
└── utils/                   # Shared utilities
    └── retry.py             #   Retry logic
```

---

## 4. Bootstrap & Startup Flow

### Entry Point: `__main__.py`

The CLI uses Click with `invoke_without_command=True` — running without subcommand drops into REPL mode.

```
python -m coding_agent
       │
       ▼
  main() ─── no subcommand ──→ _run_repl_command()
       │                            │
       ├── run  ──→ _run_headless() or _run_with_tui()
       ├── repl ──→ _run_repl_command()
       ├── stats ─→ collector.get_session()
       └── serve ─→ uvicorn.run(http_server.app)
```

### Agent Construction: `app.py` → `create_agent()`

The `create_agent()` factory in `app.py` is the wiring center. It constructs the full agentkit pipeline:

```python
def create_agent(...) -> tuple[Pipeline, PipelineContext]:
    # 1. Load config (TOML + overrides)
    cfg = load_config(config_path)

    # 2. Create PluginRegistry with agentkit HOOK_SPECS
    registry = PluginRegistry(specs=HOOK_SPECS)

    # 3. Register all 14 plugins via factory lambdas
    plugin_factories = {
        "llm_provider": lambda: LLMProviderPlugin(...),
        "storage":      lambda: StoragePlugin(...),
        "core_tools":   lambda: CoreToolsPlugin(...),
        "approval":     lambda: ApprovalPlugin(...),
        "summarizer":   lambda: SummarizerPlugin(...),
        "memory":       lambda: MemoryPlugin(),
        "shell_session": lambda: shell_session,
        "doom_detector": lambda: DoomDetectorPlugin(...),
        "parallel_executor": lambda: ParallelExecutorPlugin(...),
        "topic":        lambda: TopicPlugin(...),
        "session_metrics": lambda: SessionMetricsPlugin(),
        "skills":       lambda: SkillsPlugin(...),
        "mcp":          lambda: MCPPlugin(...),
        "kb":           lambda: KBPlugin(...),
    }

    # 4. Selective plugin loading from config
    enabled_plugins = cfg.plugins or list(plugin_factories.keys())
    for name in enabled_plugins:
        registry.register(plugin_factories[name]())

    # 5. Build runtime, pipeline, context
    runtime  = HookRuntime(registry, specs=HOOK_SPECS)
    pipeline = Pipeline(runtime=runtime, registry=registry, ...)
    ctx      = PipelineContext(tape=Tape(), session_id=..., config={...})

    return pipeline, ctx
```

### REPL Startup: `InteractiveSession`

```
run_repl(config)
    │
    ▼
InteractiveSession(config)
    ├── InputHandler()        # prompt_toolkit sessions
    ├── BashExecutor()        # for shell mode
    ├── StreamingRenderer()   # Rich console output
    ├── RichConsumer()        # wire → renderer bridge
    └── _setup_agent()        # creates Pipeline + PipelineAdapter
           │
           ▼
        session.run()         # REPL loop: read → dispatch → render
```

---

## 5. Adapter Layer

### PipelineAdapter (`adapter.py`)

The adapter bridges agentkit's event-based pipeline with the wire protocol. It is the **only** point of contact between the agentkit runtime and the UI layer.

```
┌──────────────┐     Pipeline Events      ┌──────────────────┐
│   agentkit   │  ──────────────────────→  │ PipelineAdapter  │
│   Pipeline   │  TextEvent, ToolCallEvent │                  │
│              │  ToolResultEvent, Done    │  _handle_event() │
└──────────────┘                          └────────┬─────────┘
                                                   │
                                          Wire Messages
                                          StreamDelta, ToolCallDelta
                                          ToolResultDelta, TurnEnd
                                                   │
                                                   ▼
                                          ┌──────────────────┐
                                          │  WireConsumer     │
                                          │  (Rich / Headless │
                                          │   / HTTP)         │
                                          └──────────────────┘
```

**Key responsibilities:**

| Method | Purpose |
|---|---|
| `run_turn(user_input)` | Appends user entry to tape, runs pipeline, returns `TurnOutcome` |
| `_handle_event(event)` | Translates agentkit events → wire messages, calls `consumer.emit()` |
| `_determine_stop_reason()` | Inspects tape + plugin states to classify termination |
| `_finish(stop_reason)` | Emits `TurnEnd`, assembles `TurnOutcome` |

### TurnOutcome (`adapter_types.py`)

```python
class StopReason(Enum):
    NO_TOOL_CALLS    = "no_tool_calls"       # Agent responded without tools
    MAX_STEPS_REACHED = "max_steps_reached"  # Hit turn limit
    DOOM_LOOP        = "doom_loop"           # Repetitive tool calls detected
    ERROR            = "error"               # Exception during execution
    INTERRUPTED      = "interrupted"         # KeyboardInterrupt

@dataclass
class TurnOutcome:
    stop_reason: StopReason
    final_message: str | None    # Last assistant message
    steps_taken: int             # Tool call count this turn
    error: str | None            # Error details if applicable
```

---

## 6. Wire Protocol

### Message Types (`wire/protocol.py`)

All messages inherit from `WireMessage` (with `session_id` and `timestamp`):

| Type | Direction | Purpose |
|---|---|---|
| `StreamDelta` | Agent → UI | Streaming text chunk from LLM |
| `ToolCallDelta` | Agent → UI | Tool invocation with name + arguments |
| `ToolResultDelta` | Agent → UI | Tool execution result (success/error) |
| `ApprovalRequest` | Agent → UI | Request user permission for tool |
| `ApprovalResponse` | UI → Agent | User's approval decision |
| `TurnEnd` | Agent → UI | Turn completed with status |

### CompletionStatus

```python
class CompletionStatus(str, Enum):
    COMPLETED = "completed"   # Normal completion (no more tool calls)
    BLOCKED   = "blocked"     # Hit max steps or doom loop
    ERROR     = "error"       # Exception occurred
```

### Legacy Compatibility

`ApprovalRequest` and `ApprovalResponse` support dual-format fields with `__post_init__` synchronization — enabling both the new protocol (`request_id`, `tool_call`, `approved`) and legacy format (`call_id`, `tool`, `args`, `decision`) simultaneously.

### LocalWire (`wire/local.py`)

In-process async queue-based wire for CLI sessions:

```python
class LocalWire:
    _outgoing: asyncio.Queue[WireMessage]   # Agent → UI
    _incoming: asyncio.Queue[WireMessage]   # UI → Agent

    async def send(message) → None          # Agent sends to UI
    async def receive() → WireMessage       # Agent reads from UI
    async def request_approval(tool_call, timeout) → ApprovalResponse
```

The `request_approval` flow:
1. Agent sends `ApprovalRequest` to `_outgoing`
2. UI consumes from `_outgoing`, displays prompt
3. UI puts `ApprovalResponse` into `_incoming`
4. Agent reads `ApprovalResponse` from `_incoming`
5. Timeout → auto-deny with feedback message

---

## 7. Plugin System

### Plugin Registration

All 14 plugins implement the agentkit `Plugin` protocol: a `hooks()` method returning `dict[str, Callable]`, and a `state_key` class attribute.

```python
class SomePlugin:
    state_key = "some_plugin"

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"hook_name": self.handler_method}
```

Plugins are registered in `create_agent()` via factory lambdas, supporting deferred construction and config-driven selection.

### Plugin Catalog

| Plugin | state_key | Hooks | Purpose |
|---|---|---|---|
| **LLMProviderPlugin** | `llm_provider` | `provide_llm` | Factory for provider instances (Anthropic, OpenAI, Copilot, Kimi) |
| **CoreToolsPlugin** | `core_tools` | `get_tools`, `execute_tool` | Registers + executes all file/shell/planner tools |
| **ApprovalPlugin** | `approval` | `approve_tool_call` | Returns `Approve`/`Reject`/`AskUser` directives |
| **StoragePlugin** | `storage` | `provide_storage`, `mount` | JSONL-backed `ForkTapeStore` + `FileSessionStore` |
| **SummarizerPlugin** | `summarizer` | `resolve_context_window` | Context compression via topic boundaries or entry-count truncation |
| **MemoryPlugin** | `memory` | `build_context`, `on_turn_end`, `on_checkpoint`, `mount` | Grounding injection + memory extraction |
| **SkillsPlugin** | `skills` | `build_context`, `get_tools`, `execute_tool`, `on_checkpoint`, `on_session_event`, `mount` | Discovers `.agents/skills`, injects skill summaries, and activates skills |
| **MCPPlugin** | `mcp` | `mount`, `get_tools`, `execute_tool`, `on_checkpoint` | Starts MCP servers and re-exposes their tools |
| **DoomDetectorPlugin** | `doom_detector` | `on_checkpoint` | Detects N consecutive identical tool calls |
| **ParallelExecutorPlugin** | `parallel_executor` | `execute_tools_batch` | Dependency-aware parallel tool execution |
| **TopicPlugin** | `topic` | `on_checkpoint`, `on_session_event`, `mount` | File-overlap-based topic boundary detection |
| **SessionMetricsPlugin** | `session_metrics` | `on_checkpoint`, `on_session_event` | Per-turn and per-topic performance metrics |
| **ShellSessionPlugin** | `shell_session` | `mount`, `on_checkpoint` | Persistent CWD + env tracking across tool calls |
| **KBPlugin** | `kb` | `build_context` | KB chunk injection |

### Plugin Interaction Diagram

```
User Input
    │
    ▼
Pipeline.run_turn()
    │
    ├── provide_llm ────────→ LLMProviderPlugin
    ├── get_tools ──────────→ CoreToolsPlugin
    ├── build_context ──────→ MemoryPlugin (injects memories)
    ├── resolve_context_window → SummarizerPlugin (compresses tape)
    │
    ├── [LLM Streaming Phase]
    │       │
    │       ├── Tool calls detected
    │       │       │
    │       │       ├── approve_tool_call → ApprovalPlugin
    │       │       │       ├── Approve → execute
    │       │       │       ├── AskUser → wire → UI → response
    │       │       │       └── Reject → skip
    │       │       │
    │       │       ├── execute_tool ────→ CoreToolsPlugin
    │       │       │       └── bash_run → ShellSessionPlugin (sync CWD)
    │       │       │
    │       │       └── execute_tools_batch → ParallelExecutorPlugin
    │       │
    │       └── on_checkpoint ──→ DoomDetectorPlugin
    │                           → TopicPlugin
    │                           → SessionMetricsPlugin
    │                           → MemoryPlugin
    │                           → ShellSessionPlugin
    │
    └── on_turn_end ────────→ MemoryPlugin (extract memory)
```

---

## 8. Provider Layer

### Architecture

```
┌───────────────────────────────────┐
│     agentkit LLMProvider          │  Protocol (structural subtyping)
│     protocol.py                   │  stream(), model_name, models()
└──────────────┬────────────────────┘
               │ implements
    ┌──────────┼──────────────────┐
    │          │                  │
    ▼          ▼                  ▼
┌────────┐ ┌──────────────┐ ┌──────────┐
│Anthropic│ │OpenAI-Compat │ │  Copilot │
│Provider │ │  Provider    │ │ Provider │
└────────┘ └──────────────┘ └──┬───────┘
                                │ extends
                          OpenAI-Compat
```

### Provider Details

| Provider | Module | Backend | Base URL |
|---|---|---|---|
| **AnthropicProvider** | `anthropic.py` | Anthropic Messages API | `https://api.anthropic.com` |
| **OpenAICompatProvider** | `openai_compat.py` | OpenAI Chat Completions | Configurable |
| **CopilotProvider** | `copilot.py` | GitHub Models API | `https://models.github.ai/inference` |

### Dual-Protocol Bridge

The codebase has two streaming event type systems:

1. **Legacy** (`providers/base.py`): `StreamEvent` with `type: Literal["delta", "tool_call", "done", "error"]`
2. **AgentKit** (`agentkit/providers/models.py`): `TextEvent`, `ToolCallEvent`, `DoneEvent`, `ToolResultEvent`

The `adapt_stream_events()` function in `plugins/llm_provider.py` bridges them:

```python
async def adapt_stream_events(old_stream) -> AsyncIterator[NewStreamEvent]:
    async for event in old_stream:
        if event.type == "delta":     yield TextEvent(text=event.text)
        elif event.type == "tool_call": yield ToolCallEvent(...)
        elif event.type == "done":    yield DoneEvent()
        elif event.type == "error":   yield DoneEvent()  # degraded
```

### Provider Factory (LLMProviderPlugin)

The `LLMProviderPlugin.provide_llm()` method is a lazy factory:

```python
def provide_llm(self, **kwargs) -> LLMProvider:
    match self._provider_name:
        case "anthropic":           → AnthropicProvider
        case "openai"|"openai_compat": → OpenAICompatProvider
        case "copilot":             → CopilotProvider
        case "kimi":                → OpenAICompatProvider(base_url=moonshot)
        case "kimi-code":           → OpenAICompatProvider(base_url=kimi, UA=claude-code)
        case "kimi-code-anthropic": → AnthropicProvider(base_url=kimi, UA=claude-code)
```

The provider is instantiated once on first call and cached via `self._instance`.

---

## 9. CLI Layer

### Click CLI (`__main__.py`)

```
coding_agent CLI
├── (default)  → REPL mode
├── repl       → REPL mode (explicit)
├── run        → Batch mode (--goal required)
│   ├── --tui  → Rich TUI display
│   └── (default) → Headless
├── stats      → Session statistics
└── serve      → HTTP API server (uvicorn)
```

### REPL Loop (`cli/repl.py`)

`InteractiveSession` manages the main read-eval-print loop:

```
while not should_exit:
    │
    ├── user_input = await input_handler.get_input()
    │
    ├── if shell_mode:
    │       bash_executor.execute(user_input)
    │
    ├── if starts_with "/":
    │       handle_command(user_input, context)
    │
    └── else:
            renderer.user_message(input)
            adapter.run_turn(input)
```

### Input Handler (`cli/input_handler.py`)

Two `PromptSession` instances with shared key bindings:

| Mode | Session | Multiline | Prompt |
|---|---|---|---|
| **Chat** | `chat_session` | Yes (Shift+Enter for newlines) | `[0] > ` |
| **Shell** | `shell_session` | No | `bash dir $ ` |

**Key bindings:**

| Key | Chat Mode | Shell Mode |
|---|---|---|
| `Enter` | Submit message | Submit command |
| `Escape + Ctrl-J` (Shift+Enter) | Insert newline | — |
| `!` on empty buffer | Switch to shell | — |
| `Escape` on empty buffer | — | Switch to chat |
| `Backspace` on empty buffer | — | Switch to chat |
| `Ctrl-C` (×1) | Clear + hint | Clear + hint |
| `Ctrl-C` (×2 within 2s) | Exit | Exit |
| `Ctrl-D` | Exit | Exit |

Custom ANSI escape sequences are registered for Shift+Enter compatibility:
```python
ANSI_SEQUENCES["\x1b[27;2;13~"] = (Keys.Escape, Keys.ControlJ)
ANSI_SEQUENCES["\x1b[13;2u"]    = (Keys.Escape, Keys.ControlJ)
```

### Slash Commands (`cli/commands.py`)

Decorator-based command registry:

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/exit`, `/quit` | Exit the agent |
| `/clear` | Clear the screen |
| `/plan` | Show current plan (todo list) |
| `/model [name]` | Show or change model |
| `/tools` | List available tools |
| `/skill [name|off]` | List or activate skills |
| `/thinking ...` | Toggle thinking mode and effort |
| `/mcp [reload]` | Inspect or reload MCP servers |

Commands are registered via `@command(name, description)` decorator and dispatched by `handle_command()`.

---

## 10. UI Layer

### Design: Scrollback-Based Architecture

The UI deliberately avoids full-screen TUI frameworks (like Textual). Instead, it uses `prompt_toolkit` for input and `rich` for output in a scrollback-based design — similar to Claude Code / aider.

```
┌─────────────────────────────────────────┐
│           WireConsumer Protocol          │  async emit(msg) + request_approval()
├──────────┬──────────────┬───────────────┤
│  Rich    │  Headless    │  HTTP Server  │
│ Consumer │  Consumer    │  Consumer     │
├──────────┤              │               │
│ Streaming│  logging +   │  SSE/WebSocket│
│ Renderer │  stdout      │  + REST       │
│ (Rich)   │              │               │
└──────────┴──────────────┴───────────────┘
```

### WireConsumer Protocol

```python
class WireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...
```

All UI backends implement this protocol. The adapter doesn't know which backend is active.

### StreamingRenderer (`ui/stream_renderer.py`)

The core rendering engine. Handles:

- **Streaming text**: Writes raw characters to terminal in real-time, then re-renders as Rich Markdown when stream ends (if markdown syntax detected)
- **Tool call panels**: Rich `Panel` with tool icon, arguments preview
- **Tool result panels**: Timing, truncation (1000 chars), error highlighting
- **Clear-and-rerender**: Uses ANSI cursor control to erase streamed text and replace with formatted Markdown

```python
class StreamingRenderer:
    stream_start()                    # Begin text accumulation
    stream_text(text)                 # Raw character output + buffer
    stream_end()                      # Re-render as Markdown if needed
    tool_call(call_id, name, args)    # Render tool invocation panel
    tool_result(call_id, name, result, is_error)  # Render result panel
    turn_end(status)                  # Turn completion indicator
```

**Tool icons:**

| Pattern | Icon |
|---|---|
| `file` | 📄 |
| `grep`, `search` | 🔍 |
| `bash` | ⚡ |
| `glob` | 📂 |
| `todo` | 📋 |
| (other) | 🔧 |

### RichConsumer (`ui/rich_consumer.py`)

Dispatches wire messages to `StreamingRenderer` via pattern matching:

```python
match msg:
    case StreamDelta(content=text):     renderer.stream_text(text)
    case ToolCallDelta(tool_name=...):  renderer.tool_call(...)
    case ToolResultDelta(...):          renderer.tool_result(...)
    case TurnEnd(completion_status=s):  renderer.turn_end(s.value)
```

Also manages **session-scoped auto-approval**: once a tool is approved with `scope="session"`, subsequent calls to the same tool skip the approval prompt.

### HeadlessConsumer (`ui/headless.py`)

For batch/CI mode:
- `StreamDelta` → `print(text, end="", flush=True)` (raw stdout)
- `ToolCallDelta` → `logger.info()`
- `TurnEnd` → `logger.info()`
- Auto-approves all tool calls (configurable)

### Approval Prompt (`ui/approval_prompt.py`)

Interactive approval with tool-specific previews:

| Tool Type | Preview |
|---|---|
| `bash` | Syntax-highlighted command panel |
| `file_write` | File path + content with syntax highlighting |
| `file_edit` | Red/green diff of old/new text |
| (other) | Generic key=value display |

User choices: `[y]` approve once, `[a]` approve all (session), `[n]` reject, `[r]` reject with reason.

---

## 11. Tools Layer

### Tool Registration

Tools are registered in `CoreToolsPlugin._register_tools()` via `ToolRegistry`:

```python
def _register_tools(self):
    file_read, file_write, file_replace, glob_files, grep_search = build_file_tools(workspace_root)
    file_patch = build_file_patch_tool(workspace_root)
    todo_write, todo_read = build_planner_tools(planner)

    for fn in (file_read, file_write, file_replace, glob_files,
               grep_search, bash_run, todo_write, todo_read, file_patch):
        self._registry.register(fn)
```

### Tool Catalog

| Tool | Module | Description |
|---|---|---|
| `file_read` | `file_ops.py` | Read file contents |
| `file_write` | `file_ops.py` | Write/create file |
| `file_replace` | `file_ops.py` | Search-and-replace in file |
| `glob_files` | `file_ops.py` | Glob pattern file search |
| `grep_search` | `file_ops.py` | Regex content search |
| `file_patch` | `file_patch_tool.py` | Structured file patching |
| `bash_run` | `shell.py` | Shell command execution |
| `todo_write` | `planner.py` | Create/update task plan |
| `todo_read` | `planner.py` | Read current task plan |

### Shell Session Sync

When `bash_run` executes, `CoreToolsPlugin._sync_shell_session()` inspects the result to track:
- **Directory changes**: `"Changed directory to /foo"` → `shell_session.update_cwd("/foo")`
- **Environment exports**: `"Exported KEY=value"` → `shell_session.update_env("KEY", "value")`

This enables persistent shell state across tool calls (the "Kapybara pattern").

`bash_run` is not the same as REPL `!` shell mode. `bash_run` executes a single command with explicit token restrictions, so shell chaining syntax such as `&&`, `||`, pipes, redirects, `;`, and backgrounding is rejected. REPL `!` mode uses `BashExecutor` with a real shell subprocess, so those operators work there.

### Parallel Execution

`ParallelExecutorPlugin` provides dependency-aware parallel tool execution:

```
Input: [file_read(a), file_read(b), file_write(a)]
                    │
    DependencyAnalyzer.can_run_in_parallel()
                    │
    Batch 1: [file_read(a), file_read(b)]  ← parallel (different files, both reads)
    Batch 2: [file_write(a)]               ← sequential (depends on file_read(a))
```

**Conflict rules:**
- Same-file read+write → sequential
- Same-file write+write → sequential
- Different-file operations → parallel
- Non-file tools → always parallel

Controlled by `asyncio.Semaphore(max_concurrency)` (default: 5).

### Tool Result Caching

`tools/cache.py` provides LRU caching for idempotent tool results (configurable via `--cache/--no-cache`, `--cache-size`).

---

## 12. Approval System

### Three-Layer Design

```
┌──────────────────────────────────┐
│  Layer 1: ApprovalPlugin         │  Plugin hook: approve_tool_call()
│  Returns Approve/Reject/AskUser  │  Based on policy + blocked tools
├──────────────────────────────────┤
│  Layer 2: DirectiveExecutor      │  AgentKit core: executes directives
│  Routes AskUser → _ask_user()    │  Pluggable callback
├──────────────────────────────────┤
│  Layer 3: UI Approval            │  RichConsumer.request_approval()
│  approval_prompt.py              │  → prompt_approval() interactive UI
│  HeadlessConsumer                │  → auto-approve or auto-deny
└──────────────────────────────────┘
```

### Policy Engine (`approval/policy.py`)

```python
class ApprovalPolicy(Enum):
    YOLO        = "yolo"         # Auto-approve everything
    INTERACTIVE = "interactive"  # Always ask
    AUTO        = "auto"         # Auto-approve safe tools only

class PolicyEngine:
    def needs_approval(self, tool_name) -> bool:
        match self.config.policy:
            case YOLO:        return False
            case INTERACTIVE: return True
            case AUTO:        return tool_name not in self.config.safe_tools
```

Default safe tools: `{"file_read", "repo_list", "git_status"}`

### ApprovalPlugin (`plugins/approval.py`)

Uses agentkit's directive types:

```python
def approve_tool_call(self, tool_name, arguments, **kwargs):
    if tool_name in self._blocked_tools:
        return Reject(reason=f"tool '{tool_name}' is blocked")
    match self._policy:
        case AUTO:      return Approve()
        case MANUAL:    return AskUser(question=f"Allow '{tool_name}'?")
        case SAFE_ONLY: return Approve() if tool_name in self._safe_tools else AskUser(...)
```

### Session-Scoped Approval

`RichConsumer` tracks `_session_approved_tools: set[str]`. When a user approves with `scope="session"`, subsequent calls to the same tool are auto-approved without prompting.

---

## 13. Configuration

### Layered Precedence

```
CLI flags  >  Environment variables  >  Defaults
```

### Config Model (`core/config.py`)

```python
class Config(BaseModel):
    # Provider
    provider: Literal["openai", "anthropic", "copilot", "kimi", "kimi-code", "kimi-code-anthropic"]
    model: str = "gpt-4o"
    api_key: SecretStr | None
    base_url: str | None

    # Behavior
    max_steps: int = 30
    approval_mode: Literal["yolo", "interactive", "auto"] = "yolo"
    doom_threshold: int = 3

    # Paths
    repo: Path = Path(".")
    tape_dir: Path = ~/.coding-agent/tapes

    # Sub-agents
    max_subagent_depth: int = 3
    subagent_max_steps: int = 15

    # Execution
    enable_parallel_tools: bool = True
    max_parallel_tools: int = 5

    # Caching
    enable_cache: bool = True
    cache_size: int = 100

    # HTTP
    http_api_key: str | None
```

Notes on current implementation:

- Skill discovery is handled by `SkillsPlugin`, which scans `<workspace>/.agents/skills`, `~/.agents/skills`, and optional `skills.extra_dirs` entries from `agent.toml`.
- Tape persistence for the runtime is currently resolved through `coding_agent.app.create_agent()` and `StoragePlugin` from `data_dir` / `AGENT_DATA_DIR`, so `Config.tape_dir` is not the main runtime storage switch yet.

### Environment Variable Map

| Env Var | Config Field |
|---|---|
| `AGENT_API_KEY` | `api_key` |
| `AGENT_MODEL` | `model` |
| `AGENT_BASE_URL` | `base_url` |
| `AGENT_PROVIDER` | `provider` |
| `AGENT_MAX_STEPS` | `max_steps` |
| `AGENT_APPROVAL_MODE` | `approval_mode` |
| `AGENT_DOOM_THRESHOLD` | `doom_threshold` |
| `AGENT_REPO` | `repo` |
| `AGENT_ENABLE_PARALLEL_TOOLS` | `enable_parallel_tools` |
| `AGENT_MAX_PARALLEL_TOOLS` | `max_parallel_tools` |
| `AGENT_HTTP_API_KEY` | `http_api_key` |

### Provider-Specific Key Resolution

```python
if provider == "copilot" and no api_key:
    api_key = GITHUB_TOKEN
elif provider == "kimi" and no api_key:
    api_key = MOONSHOT_API_KEY
elif provider in ("kimi-code", "kimi-code-anthropic") and no api_key:
    api_key = KIMI_CODE_API_KEY
```

---

## 14. Context Management

### Summarizer Strategy

The `SummarizerPlugin` uses a two-tier context window management strategy:

**Strategy 1 — Topic-Boundary Folding:**
If the tape has `topic_finalized` anchors and exceeds `max_entries`, fold at the last topic boundary. The fold produces a summary anchor listing topic count and involved files.

**Strategy 2 — Entry-Count Truncation:**
Fallback when no topic boundaries exist. Keeps the last `keep_recent` entries verbatim, summarizes the rest into an anchor.

### Topic Detection

`TopicPlugin` detects topic shifts by monitoring file-path overlap between turns:

1. Extract file paths from tool call arguments
2. Compare with current topic's file set
3. If overlap ratio < `overlap_threshold` (default: 0.2) → new topic
4. Insert `topic_start` anchor + emit `on_session_event`
5. Previous topic gets `fold_boundary` anchor (used by summarizer)

### Memory Grounding

`MemoryPlugin` operates in two modes:

1. **Grounding** (`build_context` hook): Injects top-N relevant memories as system messages before each LLM call. Filters by topic file overlap when available.

2. **Extraction** (`on_turn_end` hook): Produces a `MemoryRecord` with:
   - `summary`: Last 200 chars of assistant message
   - `tags`: Tool names + file paths extracted from tape
   - `importance`: Heuristic score (0-1) based on tool call count and message count

---

## 15. Dependency Graph

```
__main__.py
├── app.py
│   └── create_agent() → core/config.py + plugins/
│
├── adapter.py
│   ├── agentkit (Pipeline, PipelineContext, Entry)
│   ├── agentkit (TextEvent, ToolCallEvent, ToolResultEvent, DoneEvent)
│   ├── adapter_types.py (StopReason, TurnOutcome)
│   └── wire/protocol.py (StreamDelta, ToolCallDelta, ToolResultDelta, TurnEnd)
│
├── cli/repl.py
│   ├── cli/input_handler.py (prompt_toolkit)
│   ├── cli/commands.py
│   ├── cli/bash_executor.py
│   ├── adapter.py (PipelineAdapter)
│   ├── ui/stream_renderer.py (rich)
│   └── ui/rich_consumer.py
│
├── core/config.py (pydantic)
│
├── plugins/ (each depends on agentkit hook protocol)
│   ├── llm_provider.py → providers/*.py
│   ├── core_tools.py → tools/*.py + agentkit (ToolRegistry)
│   ├── approval.py → agentkit (Approve, Reject, AskUser)
│   ├── storage.py → agentkit (ForkTapeStore, FileSessionStore)
│   ├── summarizer.py → agentkit (Tape, Entry)
│   ├── memory.py → agentkit (MemoryRecord, Tape, Entry)
│   ├── doom_detector.py → (no external deps)
│   ├── parallel_executor.py → (asyncio only)
│   ├── topic.py → agentkit (Tape, Entry)
│   ├── metrics.py → (stdlib only)
│   └── shell_session.py → (stdlib only)
│
├── subagents/
│   └── coordinator.py
│
├── evaluation/
│   ├── adapter.py
│   └── metrics.py
│
├── verification/
│   ├── contract.py
│   └── runner.py
│
└── ui/
    ├── rich_consumer.py → wire/protocol.py
    ├── stream_renderer.py → rich
    ├── approval_prompt.py → wire/protocol.py + rich
    ├── headless.py → wire/protocol.py + logging
    └── http_server.py → fastapi + wire/protocol.py
```

---

## 16. Data Flow Diagrams

### Interactive REPL: Full Request Lifecycle

```
User types "fix the bug in main.py"
    │
    ▼
InputHandler.get_input()
    │ (prompt_toolkit)
    ▼
InteractiveSession._process_message("fix the bug in main.py")
    │
    ├── renderer.user_message("fix the bug...")      ← UI: show user input
    │
    └── adapter.run_turn("fix the bug...")
            │
            ├── tape.append(Entry(kind="message", role="user", ...))
            │
            └── pipeline.run_turn(ctx)
                    │
                    ├── [resolve_session]
                    ├── [load_state]       ← StoragePlugin loads tape
                    ├── [build_context]    ← MemoryPlugin injects memories
                    │                      ← SummarizerPlugin compresses
                    │
                    ├── [run_model]        ← LLMProviderPlugin → LLM API
                    │       │
                    │       ├── TextEvent("I'll read the file...")
                    │       │       │
                    │       │       └── adapter._handle_event()
                    │       │               └── consumer.emit(StreamDelta(...))
                    │       │                       └── renderer.stream_text(...)
                    │       │
                    │       ├── ToolCallEvent("file_read", {path: "main.py"})
                    │       │       │
                    │       │       └── adapter._handle_event()
                    │       │               └── consumer.emit(ToolCallDelta(...))
                    │       │                       └── renderer.tool_call(...)
                    │       │
                    │       └── DoneEvent()
                    │
                    ├── [execute_tools]
                    │       ├── approve_tool_call → ApprovalPlugin → Approve
                    │       ├── execute_tool → CoreToolsPlugin → file_read("main.py")
                    │       └── ToolResultEvent → adapter → ToolResultDelta → renderer
                    │
                    ├── [on_checkpoint]
                    │       ├── DoomDetectorPlugin: check for loops
                    │       ├── TopicPlugin: detect topic shift
                    │       ├── SessionMetricsPlugin: update counters
                    │       └── MemoryPlugin: cache file tags
                    │
                    ├── [save_state]       ← StoragePlugin persists tape
                    │
                    └── [dispatch]         ← Repeat if more tool calls needed
                            │
                            └── (cycles back to run_model until no more tools)
                                    │
                                    └── Final TextEvent("Here's the fix...")
                                            │
                                            └── TurnEnd(COMPLETED)
```

### Batch Mode: Headless Flow

```
_run_headless(config, goal)
    │
    ├── create_agent() → (pipeline, ctx)
    ├── HeadlessConsumer(auto_approve=True)
    ├── PipelineAdapter(pipeline, ctx, consumer)
    │
    └── adapter.run_turn(goal)
            │
            ├── StreamDelta → print(text, end="")    ← raw stdout
            ├── ToolCallDelta → logger.info(...)
            ├── ApprovalRequest → auto-approve
            └── TurnEnd → click.echo("--- Result ---")
```

---

## 17. Error Handling

### Doom Loop Detection

`DoomDetectorPlugin` hashes tool calls (name + arguments) and tracks consecutive identical calls. When `threshold` (default: 3) consecutive identical calls are detected:

1. Sets `ctx.plugin_states["doom_detector"]["doom_detected"] = True`
2. `PipelineAdapter._determine_stop_reason()` returns `StopReason.DOOM_LOOP`
3. Turn ends with `CompletionStatus.BLOCKED`

### Turn Termination

| Condition | StopReason | CompletionStatus |
|---|---|---|
| Agent responds without tool calls | `NO_TOOL_CALLS` | `COMPLETED` |
| Tool call limit reached | `MAX_STEPS_REACHED` | `BLOCKED` |
| Doom loop detected | `DOOM_LOOP` | `BLOCKED` |
| Python exception | `ERROR` | `ERROR` |
| KeyboardInterrupt | `INTERRUPTED` | `ERROR` |

### Graceful Recovery

The REPL wraps `_process_message()` in a try/except — errors are displayed but the session continues:

```python
try:
    await self._process_message(user_input)
except Exception as e:
    print_pt(f"\nError during agent execution: {e}")
    print_pt("You can continue with a new message.\n")
```
