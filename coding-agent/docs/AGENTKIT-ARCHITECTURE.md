# AgentKit Architecture

> A hook-driven, plugin-based AI agent runtime framework for Python.

## 1. Overview

AgentKit is the foundational framework that powers the coding agent. It provides:

- **Plugin-based extensibility** — All behavior is injected via plugins implementing hook contracts
- **Protocol-driven design** — Core abstractions use Python `Protocol` types (structural subtyping, no inheritance required)
- **Type-safe pipeline** — A 7-stage linear pipeline with typed context threading
- **Append-only conversation history during normal forward execution** — Thread-safe `Tape` with windowing and JSONL persistence
- **Provider abstraction** — LLM providers are pluggable via async streaming protocol

### Design Philosophy

AgentKit separates **mechanism** from **policy**. The framework provides the execution pipeline, hook dispatch, and conversation management. All domain-specific behavior (which LLM to use, which tools to expose, how to approve tool calls) is delegated to plugins.

That separation also applies to checkpoint restore semantics. AgentKit exposes tape and checkpoint primitives, but product layers decide whether restore means rollback, branching, or another policy on top of those primitives. In `coding_agent`, the accepted current policy is controlled rollback on one active stable timeline per session; the framework-level append-only description applies to normal forward execution, not to every product-layer restore policy.

```
┌─────────────────────────────────────────┐
│            Application Code             │
│  (coding_agent, custom agents, etc.)    │
├─────────────────────────────────────────┤
│              AgentKit API               │
│  Pipeline · Plugins · Tape · Tools      │
├─────────────────────────────────────────┤
│           Hook Runtime Layer            │
│  HookRuntime · HookSpec · Registry      │
├─────────────────────────────────────────┤
│         Provider / Storage Layer        │
│  LLMProvider · TapeStore · SessionStore │
└─────────────────────────────────────────┘
```

---

## 2. Module Structure

```
src/agentkit/
├── __init__.py              # Public API re-exports
├── _types.py                # StageName, EntryKind type aliases
├── errors.py                # Error hierarchy
├── tracing.py               # Observability utilities
├── py.typed                 # PEP 561 marker
│
├── channel/                 # Bidirectional communication
│   ├── protocol.py          #   Channel protocol
│   └── local.py             #   In-memory LocalChannel
│
├── checkpoint/              # Checkpoint/restore primitives
│   ├── models.py            #   Checkpoint dataclasses
│   ├── serialize.py         #   Serialization helpers
│   └── service.py           #   CheckpointService
│
├── config/                  # Configuration
│   └── loader.py            #   TOML loading, AgentConfig dataclass
│
├── context/                 # LLM message assembly
│   └── builder.py           #   ContextBuilder
│
├── directive/               # Control flow effects
│   ├── types.py             #   Directive dataclasses
│   └── executor.py          #   DirectiveExecutor
│
├── instruction/             # Input normalization
│   └── normalize.py         #   normalize_instruction()
│
├── plugin/                  # Plugin system
│   ├── protocol.py          #   Plugin protocol
│   └── registry.py          #   PluginRegistry
│
├── providers/               # LLM provider abstraction
│   ├── protocol.py          #   LLMProvider protocol
│   └── models.py            #   StreamEvent types
│
├── runtime/                 # Execution engine
│   ├── hookspecs.py         #   17 hook specifications
│   ├── hook_runtime.py      #   HookRuntime dispatcher
│   └── pipeline.py          #   7-stage Pipeline
│
├── storage/                 # Persistence protocols
│   ├── protocols.py         #   TapeStore, DocIndex, SessionStore
│   ├── session.py           #   SessionStore implementation
│   ├── checkpoint_fs.py     #   Filesystem checkpoint store
│   └── pg.py                #   PostgreSQL checkpoint store
│
├── tape/                    # Conversation history
│   ├── models.py            #   Entry dataclass
│   ├── store.py             #   TapeStore, ForkTapeStore
│   └── tape.py              #   Tape class
│
└── tools/                   # Tool system
    ├── decorator.py          #   @tool decorator
    ├── registry.py           #   ToolRegistry
    └── schema.py             #   ToolSchema dataclass
```

---

## 3. Core Abstractions

### 3.1 Pipeline & PipelineContext

The `Pipeline` is the central execution engine. It runs one agent **turn** through 7 sequential stages, threading a mutable `PipelineContext` through each.

```python
@dataclass
class PipelineContext:
    tape: Tape                              # Conversation history
    session_id: str                         # Current session
    config: dict[str, Any]                  # Runtime configuration
    plugin_states: dict[str, Any]           # Per-plugin mutable state
    messages: list[dict[str, Any]]          # Assembled LLM messages
    llm_provider: Any                       # Active LLM provider
    storage: Any                            # Active tape store
    tool_schemas: list[Any]                 # Available tool definitions
    response_entries: list[Any]             # Entries from current turn
    output: Any                             # Stage outputs (directives)
    on_event: Callable | None               # Streaming event callback
```

```python
class Pipeline:
    STAGES = [
        "resolve_session", "load_state", "build_context",
        "run_model", "save_state", "render", "dispatch"
    ]

    async def run_turn(self, ctx: PipelineContext) -> PipelineContext:
        # Executes each stage sequentially
        # Wraps storage in ForkTapeStore for transactional safety
        # On error: rollback tape, notify on_error observers
```

### 3.2 Entry & Tape

`Entry` is the atomic unit of conversation history. `Tape` is the thread-safe ordered collection.

```python
@dataclass(frozen=True)
class Entry:
    kind: EntryKind       # "message" | "tool_call" | "tool_result" | "summary" | ...
    payload: dict         # Role-specific content
    id: str               # UUID
    timestamp: float      # Unix timestamp
    meta: dict            # Extensible metadata (anchor_type, is_handoff, etc.)
```

```python
class Tape:
    # Thread-safe (Lock-protected) append-only conversation log
    def append(entry: Entry)              # Add entry
    def windowed_entries() -> list[Entry]  # Entries from window_start onwards
    def handoff(summary_anchor, ...)      # Advance window after summarization
    def fork() -> Tape                    # Transactional fork
    def save_jsonl(path) / load_jsonl(path)  # Persistence
```

**Windowing Model:**

```
Full tape:    [e0] [e1] [e2] [summary] [e4] [e5] [e6]
                                ↑
                          window_start=3
Visible:                  [summary] [e4] [e5] [e6]
```

Old entries are preserved but excluded from context building. The `handoff()` method inserts a summary anchor and advances the window.

### 3.3 Plugin Protocol

```python
@runtime_checkable
class Plugin(Protocol):
    state_key: str                                    # Unique namespace ID
    def hooks(self) -> dict[str, Callable[..., Any]]  # hook_name → callable
```

Any object satisfying this structural protocol is a valid plugin. No inheritance required.

### 3.4 Directives

Directives are immutable value objects that describe **effects** to be executed by the `DirectiveExecutor`:

```python
class Directive:          # Abstract base
class Approve(Directive)  # Tool call approved
class Reject(Directive)   # Tool call rejected (reason: str)
class AskUser(Directive)  # Pause for user input (question: str)
class Checkpoint(Directive)  # Persist plugin state
class MemoryRecord(Directive)  # Store memory (summary, tags, importance)
```

### 3.5 ToolSchema & ToolRegistry

```python
@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict          # JSON Schema
    def to_openai_format()    # Convert to OpenAI function calling format

class ToolRegistry:
    def register(schema, handler)  # Register a tool
    def get(name) -> handler       # Look up handler
    def schemas() -> list          # All registered schemas
```

The `@tool` decorator generates `ToolSchema` from function signatures and docstrings.

---

## 4. Plugin System

### 4.1 Hook Specifications

AgentKit defines **17 hooks** in the `HOOK_SPECS` registry. Each `HookSpec` declares:

| Field | Purpose |
|---|---|
| `name` | Hook identifier |
| `firstresult` | Stop at first non-None result (call_first) |
| `is_observer` | Fire-and-forget, errors swallowed (notify) |
| `returns_directive` | Return value is a Directive |
| `return_type` | Expected return type for validation |

### 4.2 The 17 Hooks

| # | Hook | Mode | Stage | Purpose |
|---|---|---|---|---|
| 1 | `provide_storage` | first_result | load_state | Return TapeStore instance |
| 2 | `get_tools` | collect_all | load_state | Return directly exposed ToolSchema list |
| 3 | `get_proxy_tools` | collect_all | load_state | Return dynamic ToolSchema list hidden behind proxy |
| 4 | `provide_llm` | first_result | load_state | Return LLMProvider instance |
| 5 | `approve_tool_call` | first_result | run_model | Return Approve/Reject/AskUser |
| 6 | `summarize_context` | first_result | build_context | Legacy: compress tape entries |
| 7 | `resolve_context_window` | first_result | build_context | Return (window_start, summary_anchor) |
| 8 | `on_error` | observer | any | Notified on pipeline errors |
| 9 | `mount` | collect_all | init | Plugin initialization, return state |
| 10 | `on_shutdown` | observer | any | Notified when pipeline is shutting down |
| 11 | `on_checkpoint` | observer | save_state | Persist state at turn boundaries |
| 12 | `build_context` | collect_all | build_context | Inject grounding context (memories, KB) |
| 13 | `on_turn_end` | collect_all | render | Produce MemoryRecord directives |
| 14 | `execute_tool` | first_result | run_model | Execute directly exposed single tool call |
| 15 | `execute_proxy_tool` | first_result | run_model | Execute dynamic tool called through `call_tool` |
| 16 | `on_session_event` | observer | any | Session-level events (topic, handoff) |
| 17 | `execute_tools_batch` | first_result | run_model | Execute directly exposed tool batch in parallel |

### 4.3 Hook Dispatch Modes

```
call_first(hook, **kwargs)   → Returns first non-None result (short-circuits)
call_many(hook, **kwargs)    → Collects all results into a list
notify(hook, **kwargs)       → Fire-and-forget, swallows exceptions
```

### 4.4 Registration Flow

```
1. Create PluginRegistry(specs=HOOK_SPECS)
2. registry.register(plugin)
   → Validates state_key uniqueness
   → Indexes plugin.hooks() against known specs
   → Warns on unknown hook names
3. Create HookRuntime(registry, specs)
   → Ready for call_first/call_many/notify
```

### 4.5 Toolset Governance

`Pipeline.mount()` initializes one `agentkit.tools.Toolset` for the run context.
`load_state` uses that instance to collect schemas, and `run_model` reuses it for
validation, approval, and execution.

Tool execution options are read from `PipelineContext.config`:

| Config Key | Type | Default | Meaning |
|---|---|---|---|
| `tool_timeout_seconds` | `float | None` | `None` | Per hook-call timeout in seconds for tool execution hooks |
| `tool_max_retries` | `int` | `0` | Number of retries after the first failed tool execution hook attempt |

Single-tool retries are scoped to the provider hook that raised. Earlier
providers that already returned `UNHANDLED_TOOL_RESULT` are not called again for
that retry cycle. Returning `UNHANDLED_TOOL_RESULT` is therefore a no-ownership
decision: providers must make it before performing I/O or mutating state.

Schema validation runs before approval and execution. This intentionally exposes
stale fixtures or callers whose arguments drift from the registered schema; for
example, `grep_search` accepts `directory`, so legacy fixture arguments using
`path` are rejected when `additionalProperties: false` is present.

Approval is fail-closed. A non-`Directive` return, approval hook lookup failure,
approval hook exception, async approval await failure, or directive executor
exception produces a rejected `ToolApprovalResult` instead of aborting the turn.
Executor failures keep the directive on the result and use the directive reason
when available; other approval failures use `reason="policy"`.

Dynamic providers can implement `get_proxy_tools` and `execute_proxy_tool`.
Those schemas are not exposed to the model directly. When any proxy schemas are
available, `Toolset.collect_schemas()` adds stable `search_tools` and
`call_tool` affordances alongside direct tools. `search_tools` returns matching
dynamic tool descriptors, or every dynamic descriptor when `query=""`.
`call_tool` is treated as a proxy affordance rather than a real target tool, so
the pipeline skips outer approval for `search_tools` and `call_tool`. Governance
is applied to the nested target: `call_tool` validates the target arguments
against the target schema, runs the target through `approve_tool_call`, and then
executes it through `execute_proxy_tool` with the same timeout/retry envelope.
Calls containing `search_tools` or `call_tool` currently bypass
`execute_tools_batch` as a batch-level tradeoff, so mixed direct/proxy batches
execute sequentially. Direct/proxy tool name overlap is treated as product
wiring ambiguity and logged as a warning. `search_tools` currently returns full
target `parameters`; schema-size trimming or a later `describe_tool` affordance
is intentionally deferred.

### 4.6 Runtime Message Bus

`agentkit.runtime.messages` defines inbound runtime controls separately from
outbound UI/event streams. The framework message kinds are:

- `interrupt`
- `user_steer`
- `approval_decision`
- `subagent_message`
- `system_notice`

`RuntimeMessageBus.consume_after(cursor, kinds=...)` is cursor-based and
idempotent. Calling it repeatedly with the same `RuntimeMessageCursor` returns
the same batch for that consumer and kind filter; each consumer owns and persists
its own cursor. The caller advances its cursor only after applying the batch.
The in-memory implementation is process-local, keeps all messages and message
IDs in memory, and is intended for tests and single-runtime wiring rather than
long-running durable processes. HTTP routing, owner fencing, and durable message
storage remain `coding_agent` responsibilities.

`Pipeline` consumes only `interrupt`, `user_steer`, `system_notice`, and
`subagent_message` at stage boundaries, before each model round, and before tool
execution. `interrupt` fails the turn at the safe point with `PipelineError`
before the pipeline cursor is advanced. `user_steer`, `system_notice`, and
`subagent_message` messages consumed in the current turn are injected into the
runtime context system message. `approval_decision` is typed by the bus but not
consumed or interpreted by agentkit; product-specific approval stores consume it
with their own cursor. The `coding_agent` HTTP session manager uses a
session-local runtime bus for approval decisions with payload shape
`{"session_id": str, "request_id": str, "approved": bool, "feedback": str | None,
"scope": "once" | "session" | "always"}`. Its approval cursor is independent of
`PipelineContext.runtime_message_cursor`.

The runtime context system message includes known run identity (`session_id`,
`run_id`, `agent_id`, `parent_run_id`), environment kind and workspace root,
elapsed turn time, context budget, optional active approval summaries, and
current-turn runtime messages. Active approval summaries live on
`PipelineContext.active_approvals` as non-empty strings or mappings with
`request_id` and optional `tool`.

---

## 5. Pipeline Stages

### 5.1 Stage Flow Diagram

```
User Input
    │
    ▼
┌─────────────────┐
│ resolve_session  │  Session setup (currently no-op)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   load_state    │  provide_storage → provide_llm → get_tools/get_proxy_tools
│                 │  Collects: storage, llm_provider, tool_schemas
│                 │  Begins ForkTapeStore transaction
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  build_context  │  resolve_context_window → build_context hooks
│                 │  ContextBuilder.build(tape, grounding)
│                 │  → ctx.messages (ready for LLM)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   run_model     │  LLM streaming loop (up to max_tool_rounds):
│                 │    stream() → TextEvent/ToolCallEvent/DoneEvent
│                 │    For each tool_call:
│                 │      approve_tool_call → execute_tool(s)
│                 │      Append tool_result to tape
│                 │      Re-run build_context, continue loop
│                 │    Text-only response → break
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   save_state    │  on_checkpoint (observer) → persist state
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│     render      │  on_turn_end → collect Directive list
│                 │  Execute directives (MemoryRecord, etc.)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    dispatch     │  Final dispatch (currently no-op)
└─────────────────┘
```

### 5.2 run_model Tool Loop Detail

```
for round in range(max_tool_rounds):
    ┌─────────────────────────────┐
    │  stream(messages, tools)    │  Async iterator of StreamEvents
    │  ├─ TextEvent → buffer     │
    │  ├─ ThinkingEvent → buffer │
    │  ├─ ToolCallEvent → queue  │
    │  └─ DoneEvent → break      │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │  text only? → append Entry  │──→ BREAK (turn complete)
    │           (kind="message")  │
    └──────────┬──────────────────┘
               │ has tool_calls
               ▼
    ┌─────────────────────────────┐
    │ For each tool_call:         │
    │   approve_tool_call(...)    │
    │   ├─ Approve → execute      │
    │   └─ Reject → skip          │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │ Batch or sequential execute │
    │ execute_tools_batch (direct ≥2) │
    │ execute_tool (direct single)    │
    │ call_tool → execute_proxy_tool  │
    │ → Append tool_result Entry  │
    │ → Fire ToolResultEvent      │
    └──────────┬──────────────────┘
               │
    ┌──────────▼──────────────────┐
    │  Re-run build_context       │
    │  → Update ctx.messages      │
    │  → Continue loop            │
    └─────────────────────────────┘
```

### 5.3 Transactional Safety

The pipeline wraps tape mutations in a `ForkTapeStore` transaction:

```
load_state:
    fork = storage.begin(tape)   # Fork tape
    ctx.tape = fork              # All mutations go to fork

On success:
    storage.commit(fork)         # Persist to backing store

On error:
    storage.rollback(fork)       # Discard fork
    ctx.tape = original_tape     # Restore original
```

---

## 6. Provider Abstraction

### 6.1 LLMProvider Protocol

```python
@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def max_context_size(self) -> int: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]: ...
```

Any object with these three members is a valid provider. The `stream()` method is an async generator yielding `StreamEvent` subtypes.

### 6.2 StreamEvent Types

```python
@dataclass(frozen=True)
class TextEvent:           # text: str — incremental content
class ThinkingEvent:       # text: str — reasoning/chain-of-thought
class ToolCallEvent:       # tool_call_id, name, arguments
class ToolResultEvent:     # tool_call_id, name, result, is_error
class DoneEvent:           # stop_reason: str, usage: dict
```

```
LLM Stream:  [Think] [Think] [Text] [Text] [ToolCall] [ToolCall] [Done]
                │       │      │      │        │          │         │
                ▼       ▼      ▼      ▼        ▼          ▼         ▼
            on_event callback → forwarded to PipelineAdapter → Wire → UI
```

---

## 7. Storage & Persistence

### 7.1 Storage Protocols

```python
class TapeStore(Protocol):
    def save(tape: Tape) -> None
    def load(tape_id: str) -> Tape | None
    def list_ids() -> list[str]

class SessionStore(Protocol):
    def save_session(session_id, metadata) -> None
    def load_session(session_id) -> dict | None

class DocIndex(Protocol):
    def search(query: str, limit: int) -> list[dict]
```

### 7.2 ForkTapeStore

Provides transactional semantics for pipeline execution:

```python
class ForkTapeStore:
    def begin(tape: Tape) -> Tape      # Create fork
    def commit(fork: Tape) -> None     # Persist to backing store
    def rollback(fork: Tape) -> None   # Discard
```

### 7.3 JSONL Format

Tapes are persisted as newline-delimited JSON:

```jsonl
{"id":"abc","kind":"message","payload":{"role":"user","content":"Hello"},"timestamp":1712000000}
{"id":"def","kind":"message","payload":{"role":"assistant","content":"Hi!"},"timestamp":1712000001}
{"id":"ghi","kind":"tool_call","payload":{"id":"tc1","name":"file_read","arguments":{"path":"x.py"},"role":"assistant"},"timestamp":1712000002}
{"id":"jkl","kind":"tool_result","payload":{"tool_call_id":"tc1","content":"...file content..."},"timestamp":1712000003}
```

Incremental append is supported — only new entries since last save are written.

---

## 8. Channel System

The `Channel` protocol supports bidirectional communication between components:

```python
class Channel(Protocol):
    async def send(message: Any) -> None
    async def receive() -> Any
    def subscribe(callback: Callable) -> str     # Returns subscription ID
    def unsubscribe(sub_id: str) -> None
```

`LocalChannel` provides an in-memory implementation using `asyncio.Queue` and callback lists. This is used for in-process communication (e.g., between pipeline and UI).

---

## 9. Directive System

Directives implement the **Command pattern** for pipeline side effects:

```
Pipeline Stage           Directive Produced       DirectiveExecutor Action
─────────────────────────────────────────────────────────────────────────
run_model                Approve                  Allow tool execution
run_model                Reject(reason)           Block tool, record reason
run_model                AskUser(question)        Pause for user input
save_state               Checkpoint(state)        Persist plugin state
render (on_turn_end)     MemoryRecord(summary)    Store in knowledge base
```

The `DirectiveExecutor` dispatches each directive type to the appropriate handler:

```python
class DirectiveExecutor:
    async def execute(directive: Directive) -> bool:
        match directive:
            case Approve():     return True
            case Reject():      return False
            case AskUser():     # prompt user
            case Checkpoint():  # persist state
            case MemoryRecord(): # store memory
```

---

## 10. Extension Points

| Extension Point | Mechanism | Example |
|---|---|---|
| **Add tools** | Plugin implementing `get_tools` hook | File ops, shell, search |
| **Custom LLM** | Plugin implementing `provide_llm` hook | Anthropic, OpenAI, local |
| **Storage backend** | Plugin implementing `provide_storage` hook | SQLite, S3, local FS |
| **Tool approval** | Plugin implementing `approve_tool_call` hook | Policy engine, user prompt |
| **Context injection** | Plugin implementing `build_context` hook | RAG, memory, KB |
| **Context windowing** | Plugin implementing `resolve_context_window` hook | Topic-based, token-based |
| **Turn-end effects** | Plugin implementing `on_turn_end` hook | Memory recording, metrics |
| **Parallel execution** | Plugin implementing `execute_tools_batch` hook | Async batch runner |
| **Observability** | Plugins implementing observer hooks | Metrics, error tracking |
| **Custom directives** | Subclass `Directive` + executor handler | Domain-specific effects |

---

## 11. Dependency Graph

```
                    ┌──────────┐
                    │ __init__ │  (re-exports)
                    └────┬─────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────▼────┐    ┌─────▼─────┐   ┌─────▼─────┐
    │ runtime │    │  plugin   │   │   tape    │
    │         │    │           │   │           │
    │pipeline │◄───│ registry  │   │  tape.py  │
    │hookspecs│    │ protocol  │   │ models.py │
    │hook_rt  │    └───────────┘   │ store.py  │
    └────┬────┘                    └─────┬─────┘
         │                               │
    ┌────▼────────┐              ┌───────▼───────┐
    │  providers  │              │   storage     │
    │  protocol   │              │  protocols    │
    │  models     │              │  session      │
    └─────────────┘              │ checkpoint_fs │
                                 │     pg        │
                                 └───────┬───────┘
                                         │
                                    ┌────▼─────┐
                                    │checkpoint│
                                    │ models   │
                                    │serialize │
                                    │ service  │
                                    └──────────┘
         │
    ┌────▼────┐    ┌───────────┐    ┌───────────┐
    │ context │    │ directive │    │   tools   │
    │ builder │    │  types    │    │ decorator │
    └─────────┘    │ executor  │    │ registry  │
                   └───────────┘    │ schema    │
                                    └───────────┘
    ┌───────────┐    ┌───────────┐    ┌───────────┐
    │  channel  │    │  config   │    │instruction│
    │ protocol  │    │  loader   │    │ normalize │
    │  local    │    └───────────┘    └───────────┘
    └───────────┘

    ┌───────────┐    ┌───────────┐
    │  _types   │    │  errors   │  ← Used by ALL modules
    └───────────┘    └───────────┘
```

### Key Dependency Rules

- `runtime/pipeline.py` depends on: `plugin.registry`, `providers.models`, `tape`, `directive.types`, `context.builder` (may also depend on `checkpoint` for checkpoint/restore)
- `plugin/` has **no** dependencies on `runtime/` (clean separation)
- `tape/` depends only on `_types` (fully self-contained)
- `providers/` depends only on its own `models.py` (protocol is structural)
- `checkpoint/` depends on `tape/models` and `storage/protocols`
- `errors.py` and `_types.py` are leaf dependencies (used everywhere, depend on nothing)

---

## 12. Error Hierarchy

```
AgentKitError
├── PipelineError     # Stage execution failures
├── HookError         # Hook dispatch failures
├── PluginError       # Plugin registration/state errors
├── DirectiveError    # Directive execution failures
├── StorageError      # Persistence failures
├── ToolError         # Tool execution failures
└── ConfigError       # Configuration loading failures
```

All errors carry enough context (stage name, plugin ID, tool name) for actionable debugging.
