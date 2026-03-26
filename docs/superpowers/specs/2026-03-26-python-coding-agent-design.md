# Python Coding Agent — Design Spec

## 1. Overview

Rebuild the coding agent from scratch in Python, replacing the current Go/Eino implementation. The new agent retains proven concepts (RAG pipeline, eval framework, doom loop detection) while fixing fundamental issues (no interactivity, fragile unified-diff patching, no sandbox, no sub-agents).

### Goals

- Interactive TUI with mixed mode (yolo / interactive / auto-policy)
- Batch/headless mode for eval and CI
- Sub-agent architecture for task decomposition
- Sandbox-ready (git snapshot now, Docker/Kata later)
- ACP (Agent Client Protocol) and MCP (Model Context Protocol) integration
- Tape-based context management for long-running sessions
- Self-written thin provider layer (no heavy dependencies like LiteLLM)

### Non-Goals (for v1)

- Web UI frontend or desktop app (HTTP API is provided, but no bundled frontend)
- Multi-user / multi-tenant
- Production container sandbox (interface only, stub implementation)
- Full autonomous agent teams (s09-s12 deferred)

### Positioning

Learning project + personal tool, potentially open source later.

---

## 2. Reference Implementations Studied

| Project | Language | Key Takeaways |
|---------|----------|---------------|
| **Codex CLI** (OpenAI) | Rust | Custom patch format, platform-native sandbox (Seatbelt/Landlock), rule-based exec policy + approval caching, sub-agent spawning with depth limits |
| **OpenCode** (Anomaly) | TypeScript/Bun | Client/server architecture, git-based snapshot for undo, Vercel AI SDK multi-provider, glob-pattern permission system, custom agents via config |
| **Kimi CLI** (Moonshot) | Python | Soul architecture (KimiSoul loop), kosong provider abstraction, Wire protocol (UI decoupled from core), sub-agents with foreground/background, approval runtime, dynamic injection (plan/yolo mode) |
| **bub.build** | Python | Hook-first (pluggy) architecture, tape-based context (append-only facts + anchors), fork/merge for sub-agents, skill-as-document pattern, unified pipeline across channels |
| **learn.shareai.run** | Python (教程) | s01-s12 progressive build: loop → tools → planner → subagents → skills → compact → tasks → background → teams → protocols → autonomous → worktree isolation |

### Current Go Agent — Lessons Learned

Proven worth preserving:
- RAG pipeline: +42pp pass rate (p=0.001), LanceDB vector search, hybrid/vector/text fallthrough
- Doom loop detection: track last tool+input, abort on repetition
- Eval framework: 26 benchmark tasks, A/B testing with McNemar test, strict mode
- Reviewer three-layer judgment + coder anti-doom-loop prompts: +23pp Pass@3

Must redesign:
- Unified diff via `git apply` — fragile, LLM generates wrong hunk headers → `recountUnifiedHunkHeaders()` hack
- No human interactivity — batch-only CLI, no TUI, no approval flow
- Coder puts patch content in `commands` field instead of `patch` field — 3 layers of defense needed
- 58k-line `coder_eino.go` — generation, validation, retry, KB logic all coupled
- Reviewer ~44-53% fallback rate even after prompt improvements
- SQLite via forking `sqlite3` CLI processes (not a proper driver)

---

## 3. Architecture

### 3.1 High-Level Architecture

```
                                    ┌──────────────────────────────┐
┌─────────┐                         │        Agent Core            │
│   TUI   │◄── Wire Messages ──────►│                              │
│(Textual)│                         │  ┌────────┐  ┌───────────┐  │
└─────────┘                         │  │  Loop   │  │  Tape     │  │
                                    │  │(s01-02) │  │ Context   │  │
┌─────────┐                         │  └────┬───┘  └───────────┘  │
│ Batch / │◄── Wire Messages ──────►│       │                      │
│ Headless│                         │  ┌────▼───┐  ┌───────────┐  │
└─────────┘                         │  │  Tool  │  │ Approval  │  │
                                    │  │Registry│  │  Policy   │  │
┌─────────┐                         │  └────┬───┘  └───────────┘  │
│HTTP API │◄── Wire Messages ──────►│       │                      │
│(FastAPI)│  (REST + SSE)           │  ┌────▼───────────────────┐  │
└─────────┘                         │  │   Tool Backends        │  │
                                    │  │ ┌───────┐ ┌─────────┐ │  │
┌─────────┐                         │  │ │ Local  │ │   ACP   │ │  │
│ Editor  │◄── ACP (JSON-RPC) ─────►│  │ │Backend │ │ Backend │ │  │
│(VS Code │                         │  │ └───────┘ └─────────┘ │  │
│ / Zed)  │                         │  │ ┌───────┐ ┌─────────┐ │  │
└─────────┘                         │  │ │  MCP   │ │SubAgent │ │  │
                                    │  │ │Provider│ │Dispatch │ │  │
                                    │  │ └───────┘ └─────────┘ │  │
                                    │  └────────────────────────┘  │
                                    │                              │
                                    │  ┌────────────────────────┐  │
                                    │  │     Providers          │  │
                                    │  │ OpenAI | Anthropic     │  │
                                    │  └────────────────────────┘  │
                                    └──────────────────────────────┘
```

### 3.2 Package Structure

```
src/coding_agent/
  core/
    loop.py             # AgentLoop: while loop + tool dispatch (s01-02)
    tape.py             # Tape: append-only Entry storage (JSONL)
    context.py          # Context: anchor-based working set assembly
    session.py          # Session: tape lifecycle + persistence
    planner.py          # TodoWrite-style planning (s03)

  wire/
    protocol.py         # WireMessage Pydantic models (typed message contract)
    local.py            # Local async implementation (TUI/batch)
    acp_adapter.py      # ACP JSON-RPC <-> Wire message translation

  providers/
    base.py             # ChatProvider protocol + StreamEvent types
    openai_compat.py    # OpenAI-compatible (GPT, Deepseek, Qwen, proxies)
    anthropic.py        # Anthropic native (Claude)

  tools/
    registry.py         # ToolRegistry: register + route + schema generation
    backend.py          # ToolBackend protocol (local / ACP switchable)
    local_backend.py    # Direct execution (TUI/batch mode)
    acp_backend.py      # Forward to editor (ACP mode)
    mcp_provider.py     # MCP server dynamic tool loading
    # Built-in tools
    file.py             # read, write, replace (search-and-replace), patch
    shell.py            # bash execution with output capture
    search.py           # grep, glob, repo_search
    kb.py               # RAG search (LanceDB, integrated from existing kb/)
    web.py              # web_search, web_fetch (optional built-in or via MCP)

  agents/
    subagent.py         # Sub-agent dispatch with fork/merge tape (s04)
    team.py             # Agent teams + async mailbox (s09-s10, deferred)
    autonomous.py       # Self-claiming task model (s11, deferred)

  tasks/
    graph.py            # Task graph with dependencies + parallelism (s07)
    background.py       # Background async tasks (s08)
    worktree.py         # Git worktree isolation (s12)

  sandbox/
    base.py             # Sandbox protocol
    snapshot.py         # Git snapshot implementation (default)
    container.py        # Docker/Kata stub (interface only)

  approval/
    policy.py           # Rule engine + yolo/interactive/auto modes
    store.py            # Approval cache (session-scoped)

  skills/               # SKILL.md files (frontmatter + markdown body)
    __init__.py         # Skill loader (lazy: frontmatter first, body on demand)

  ui/
    tui.py              # Textual/Rich TUI
    headless.py         # Batch mode (eval)
    http_server.py      # REST + SSE API (FastAPI/Litestar)

  eval/
    benchmark.py        # Benchmark task runner (from existing framework)
    ab.py               # A/B testing with paired analysis
```

---

## 4. Core Components

### 4.1 Agent Loop (`core/loop.py`)

The minimal kernel: `while True → call model → execute tools → feed results back`.

```python
class AgentLoop:
    def __init__(
        self,
        provider: ChatProvider,
        tools: ToolRegistry,
        tape: Tape,
        context: Context,
        approval: ApprovalPolicy,
        wire: WireProtocol,
    ): ...

    async def run_turn(self, user_input: str) -> TurnOutcome:
        """Single conversation turn: user input → agent response."""
        self.tape.append(Entry.message("user", user_input))
        self.wire.emit(TurnBegin(...))

        for step in range(self.max_steps):
            working_set = self.context.build_working_set(self.tape, token_budget=...)
            response = await self.provider.stream(working_set, tools=self.tools.schemas())

            if response.has_tool_calls:
                for call in response.tool_calls:
                    # Always record the tool call first (before approval)
                    self.tape.append(Entry.tool_call(call))

                    # Approval gate
                    decision = await self.approval.evaluate(call, self.wire)
                    if decision.denied:
                        self.tape.append(Entry.tool_result(call.id, f"[DENIED] {decision.feedback}"))
                        continue

                    # Doom loop check
                    if self.doom_detector.observe(call.name, call.args):
                        self.tape.append(Entry.tool_result(call.id, "[ABORTED] Repetitive tool call detected"))
                        return TurnOutcome(stop_reason="doom_loop", ...)

                    result = await self.tools.execute(call)
                    self.tape.append(Entry.tool_result(call.id, result))
            else:
                # No tool calls = turn complete
                self.tape.append(Entry.message("assistant", response.text))
                self.wire.emit(TurnEnd(...))
                return TurnOutcome(stop_reason="no_tool_calls", ...)

        return TurnOutcome(stop_reason="max_steps_reached", ...)
```

Key behaviors:
- Doom loop detection: track last tool+input, abort on N repetitions (configurable, default 3)
- Token budget enforcement: context.build_working_set handles truncation
- Streaming: provider.stream yields deltas, wire forwards to UI in real-time

### 4.2 Tape (`core/tape.py`)

Append-only fact storage. Every event in the agent's lifecycle is an immutable entry.

```python
EntryKind = Literal["message", "tool_call", "tool_result", "anchor", "event"]

@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    kind: EntryKind
    payload: dict[str, Any]
    timestamp: str  # ISO 8601

    @classmethod
    def message(cls, role: str, content: str) -> "Entry": ...
    @classmethod
    def anchor(cls, name: str, state: dict) -> "Entry": ...
    @classmethod
    def tool_call(cls, call: ToolCall) -> "Entry": ...
    @classmethod
    def tool_result(cls, call_id: str, result: str) -> "Entry": ...

class Tape:
    """Append-only sequence of entries, persisted as JSONL."""

    def __init__(self, path: Path): ...
    def append(self, entry: Entry) -> None: ...
    def entries(self, after_anchor: str | None = None) -> list[Entry]: ...
    def handoff(self, name: str, state: dict) -> None:
        """Create an anchor marking a phase transition."""
    def fork(self) -> "Tape":
        """Create an in-memory fork for sub-agent execution."""
    def merge(self, forked: "Tape") -> None:
        """Merge forked entries back into this tape."""
```

Three invariants:
1. History is append-only, never overwritten
2. Derived data never replaces original facts
3. Context is constructed on demand, not inherited wholesale

### 4.3 Context (`core/context.py`)

Builds LLM-ready message arrays from tape entries, respecting token budgets.

```python
class Context:
    def build_working_set(self, tape: Tape, token_budget: int) -> list[Message]:
        """Assemble a context window from tape entries.

        Strategy:
        1. Find the most recent anchor
        2. Include all entries after that anchor
        3. If over budget, summarize older entries into a synthetic anchor
        4. Always include: system prompt + current plan + recent entries
        """
```

Anchor-based reconstruction means long sessions don't degrade — each phase starts from a clean checkpoint with structured state, not a truncated history.

### 4.4 Wire Protocol (`wire/protocol.py`)

Typed message contract between agent core and frontends.

```python
class WireMessage(BaseModel):
    """Base for all wire messages."""
    timestamp: datetime

# Agent → UI
class TurnBegin(WireMessage): ...
class TurnEnd(WireMessage): ...
class StreamDelta(WireMessage):
    text: str
class ToolCallBegin(WireMessage):
    tool: str
    args: dict
class ToolCallEnd(WireMessage):
    result: str

# UI → Agent (or auto-resolved)
class ApprovalRequest(WireMessage):
    tool: str
    args: dict
    risk_level: Literal["low", "medium", "high"]
class ApprovalResponse(WireMessage):
    decision: Literal["approve", "deny"]
    scope: Literal["once", "session", "always"]
    feedback: str | None = None  # rejection reason sent back to LLM

# Bidirectional
class UserInterrupt(WireMessage):
    message: str | None = None
```

Frontends implement `WireConsumer`:

```python
class WireConsumer(Protocol):
    async def on_message(self, msg: WireMessage) -> None: ...
    async def request(self, msg: WireMessage) -> WireMessage: ...
```

- `ui/tui.py` → renders to terminal, shows approval dialogs
- `ui/headless.py` → logs to file, auto-approves per policy
- `ui/http_server.py` → serializes Wire messages as SSE events, handles REST endpoints
- `wire/acp_adapter.py` → translates to/from ACP JSON-RPC

### 4.5 Provider Layer (`providers/`)

Thin, self-controlled abstraction over LLM APIs.

```python
@dataclass
class StreamEvent:
    type: Literal["delta", "tool_call", "done", "error"]
    text: str | None = None
    tool_call: ToolCall | None = None

class ChatProvider(Protocol):
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]: ...

    @property
    def model_name(self) -> str: ...
    @property
    def max_context_size(self) -> int: ...
```

Implementations:
- `OpenAICompatProvider` — uses `httpx` or `openai` SDK. Covers GPT, Deepseek, Qwen, right.codes proxy, any OpenAI-compatible endpoint
- `AnthropicProvider` — uses `anthropic` SDK. Native tool_use format, no proxy translation issues

### 4.6 Tool System (`tools/`)

Registry-based with backend abstraction for ACP compatibility.

```python
@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Awaitable[str]]

class ToolRegistry:
    def register(self, tool: ToolDef) -> None: ...
    def schemas(self) -> list[ToolSchema]: ...
    async def execute(self, call: ToolCall) -> str: ...

class ToolBackend(Protocol):
    """Abstraction over where tools actually execute."""
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def replace_in_file(self, path: str, old: str, new: str) -> str: ...
    async def exec_shell(self, cmd: str, cwd: str | None = None) -> ShellResult: ...
    async def list_dir(self, path: str, pattern: str | None = None) -> list[str]: ...
    async def search(self, pattern: str, path: str | None = None) -> list[SearchHit]: ...
```

Built-in tools:
| Tool | Operation | Notes |
|------|-----------|-------|
| `file_read` | Read file content | Max bytes limit, path validation |
| `file_write` | Create new file | Only for new files |
| `file_replace` | Search-and-replace in existing file | Primary editing method |
| `file_patch` | Multi-file coordinated edits | Simplified format (not unified diff) |
| `bash` | Execute shell command | Approval-gated |
| `grep` | Search file contents | Ripgrep-style |
| `glob` | Find files by pattern | |
| `kb_search` | RAG vector search | LanceDB, integrated from existing sidecar |
| `web_search` | Web search | Via MCP or built-in |
| `web_fetch` | Fetch URL content | Via MCP or built-in |
| `todo_write` | Create/update task plan | s03 planner |
| `subagent` | Dispatch sub-agent | Fork tape, independent messages (s04) |

### 4.7 File Editing Strategy

**Primary: search-and-replace (`file_replace`)**
- `old_string` → `new_string` in a specified file
- Handles 90%+ of editing scenarios
- LLM error rate much lower than unified diff
- Fuzzy matching as fallback (like OpenCode's approach from Cline/Gemini CLI)

**Secondary: simplified patch (`file_patch`)**
- For multi-file coordinated changes only
- Custom format inspired by Codex CLI:

```
*** Update File: src/foo.py
@@
- old_line_1
- old_line_2
+ new_line_1
+ new_line_2

*** Create File: src/bar.py
+ new file content here

*** Delete File: src/old.py
```

- No hunk line counts (the root cause of our Go agent's fragility)
- Context lines for disambiguation, not for line counting

### 4.8 Sandbox (`sandbox/`)

```python
class Sandbox(Protocol):
    async def snapshot(self, label: str) -> str:
        """Take a snapshot, return snapshot ID."""
    async def restore(self, snapshot_id: str) -> None:
        """Restore to a previous snapshot."""
    async def diff(self, from_id: str, to_id: str | None = None) -> str:
        """Show changes between snapshots."""

class GitSnapshotSandbox(Sandbox):
    """Default: shadow git repo for undo/redo. No isolation."""

class ContainerSandbox(Sandbox):
    """Stub: Docker/Kata container. Interface only for v1."""
```

The sandbox protocol is deliberately minimal — snapshot/restore/diff. Container implementations (Docker, Kata) will add `exec_in_sandbox()` when needed.

### 4.9 Approval System (`approval/`)

Three modes:

```python
class ApprovalMode(Enum):
    YOLO = "yolo"               # Auto-approve everything
    INTERACTIVE = "interactive"  # Ask for every dangerous operation
    AUTO_POLICY = "auto"        # Rule-based, ask when no rule matches

class ApprovalPolicy:
    def __init__(self, mode: ApprovalMode, rules: list[Rule]):
        self.mode = mode
        self.rules = rules
        self.cache = ApprovalStore()  # Session-scoped cache

    async def evaluate(self, call: ToolCall, wire: WireProtocol) -> Decision:
        if self.mode == ApprovalMode.YOLO:
            return Approve()

        # Check cache first (user approved "always" for this pattern)
        if cached := self.cache.lookup(call):
            return cached

        # Rule-based evaluation
        decision = self._match_rules(call)
        if decision is not None:
            return decision

        # No rule matched → ask user (or auto-deny in headless)
        if self.mode == ApprovalMode.INTERACTIVE:
            response = await wire.request(ApprovalRequest(tool=call.name, args=call.args))
            if response.scope in ("session", "always"):
                self.cache.store(call, response)
            return response
        else:
            return Deny(feedback="No approval rule matched")
```

Rules use glob patterns (like OpenCode):

```python
Rule(pattern="bash:ls *", action="allow")
Rule(pattern="bash:rm *", action="ask")
Rule(pattern="file_write:*.env", action="deny")
Rule(pattern="bash:*", action="ask")  # default for bash
```

### 4.10 Sub-Agents (`agents/subagent.py`)

Sub-agents get forked tapes and independent tool execution (s04).

```python
class SubAgent:
    MAX_DEPTH = 3  # Configurable via config.max_subagent_depth

    async def run(
        self, goal: str, parent_tape: Tape, tools: ToolRegistry, depth: int = 0
    ) -> SubAgentResult:
        if depth >= self.MAX_DEPTH:
            return SubAgentResult(success=False, output="Max sub-agent depth reached", entries_count=0)

        forked_tape = parent_tape.fork()  # In-memory, clean history
        forked_tape.handoff("subagent_start", {"goal": goal})

        loop = AgentLoop(
            provider=self.provider,
            tools=tools,  # Can be restricted (e.g., read-only for reviewer)
            tape=forked_tape,
            context=self.context,
            approval=self.approval,
            wire=self.wire,
        )

        result = await loop.run_turn(goal)

        # Selective merge: only merge if sub-agent succeeded
        if result.success:
            parent_tape.merge(forked_tape)

        return SubAgentResult(
            success=result.success,
            output=result.final_message,
            entries_count=len(forked_tape.entries()),
        )
```

### 4.11 MCP Integration (`tools/mcp_provider.py`)

Dynamic tool loading from MCP servers.

```python
class MCPToolProvider:
    """Load tools from MCP servers into the ToolRegistry."""

    async def connect(self, config: MCPServerConfig) -> None:
        self.client = await MCPClient.connect(config)
        tools = await self.client.list_tools()
        for tool in tools:
            self.registry.register(ToolDef(
                name=f"mcp_{config.name}_{tool.name}",
                description=tool.description,
                parameters=tool.input_schema,
                handler=partial(self._call_mcp_tool, tool.name),
            ))

    async def _call_mcp_tool(self, tool_name: str, **kwargs) -> str:
        result = await self.client.call_tool(tool_name, kwargs)
        return result.content
```

Use cases: web_search, browser automation, database access, Slack — all available as community MCP servers without custom implementation.

### 4.12 ACP Integration (`wire/acp_adapter.py`)

Translates between ACP JSON-RPC and Wire messages.

```python
class ACPAdapter:
    """Bridge between ACP (editor side) and Wire (agent core)."""

    async def handle_rpc(self, method: str, params: dict) -> Any:
        match method:
            case "session/prompt":
                # Editor sends user input → Wire TurnBegin
                await self.wire.emit(TurnBegin(...))
                result = await self.loop.run_turn(params["content"])
                return result.to_acp_response()
            case "session/cancel":
                await self.wire.emit(UserInterrupt())

    async def on_wire_message(self, msg: WireMessage):
        match msg:
            case ToolCallBegin(tool="bash", args=args):
                # Agent wants to run shell → ACP terminal/create
                result = await self.rpc.request("terminal/create", {...})
            case ToolCallBegin(tool="file_read", args=args):
                # Agent wants to read file → ACP fs/read_text_file
                result = await self.rpc.request("fs/read_text_file", {"path": args["path"]})
```

### 4.13 RAG / Knowledge Base (`tools/kb.py`)

Integrated from existing Python sidecar (kb/server.py). In v1, can remain as a subprocess or be directly imported.

Key design from current implementation to preserve:
- LanceDB for vector storage
- Hybrid search fallthrough: hybrid → vector → text
- Pluggable embedders (OpenAI API or local sentence-transformers)
- Chunk size 1200, overlap 200
- Atomic rebuild with backup
- Top-k configurable (not hardcoded)

### 4.14 Skills (`skills/`)

Skills are SKILL.md files with validated frontmatter. Loaded lazily: frontmatter parsed at startup, body loaded on demand.

```markdown
---
name: code-review
description: Review code changes for bugs, security issues, and style
inputs:
  - name: scope
    type: string
    description: "File pattern or 'staged' for git staged changes"
---

You are a senior code reviewer. Analyze the following changes...
```

Injected via tool_result when the agent (or user) invokes a skill, not stuffed into the system prompt upfront (s05 pattern).

---

## 5. Configuration (`core/config.py`)

Layered configuration with clear precedence: CLI flags > env vars > config file > defaults.

```python
class Config(BaseModel):
    """Validated configuration. Loaded once at startup."""

    # Provider
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-4o"
    api_key: SecretStr            # required, no default
    base_url: str | None = None   # OpenAI-compatible endpoint override

    # Agent behavior
    max_steps: int = 30
    approval_mode: ApprovalMode = ApprovalMode.INTERACTIVE
    doom_threshold: int = 3       # consecutive identical tool calls before abort

    # Paths
    repo: Path = Path(".")
    tape_dir: Path = Path("~/.coding-agent/tapes").expanduser()
    skills_dir: Path = Path("~/.coding-agent/skills").expanduser()

    # RAG (optional, not required for P0)
    kb_base_url: str | None = None
    kb_embedding_model: str = "text-embedding-3-small"

    # Sub-agents
    max_subagent_depth: int = 3
    subagent_max_steps: int = 15

    # MCP servers (loaded from config file)
    mcp_servers: list[MCPServerConfig] = []

def load_config(
    cli_args: dict | None = None,
    config_path: Path | None = None,
) -> Config:
    """Load config with precedence: CLI flags > env vars > config file > defaults.

    Env var mapping: AGENT_MODEL, AGENT_API_KEY, AGENT_BASE_URL,
    AGENT_APPROVAL_MODE, AGENT_MAX_STEPS, etc.
    """
```

Config file format (`~/.coding-agent/config.toml` or project-local `.agent.toml`):

```toml
[provider]
provider = "openai"
model = "gpt-4o"
base_url = "https://api.right.codes/v1"

[agent]
max_steps = 30
approval_mode = "auto"
doom_threshold = 3

[mcp.web-search]
command = "npx"
args = ["-y", "@anthropic/mcp-web-search"]

[mcp.browser]
command = "npx"
args = ["-y", "@anthropic/mcp-browser"]
```

CLI interface (P0 minimal):

```bash
# Basic usage
python -m coding_agent run --goal "Fix the broken test" --repo .

# With overrides
python -m coding_agent run \
    --goal "Add input validation" \
    --repo ./my-project \
    --model claude-sonnet-4-20250514 \
    --provider anthropic \
    --approval yolo \
    --max-steps 20

# Interactive TUI (P2)
python -m coding_agent chat --repo .

# Resume session
python -m coding_agent resume --session <session-id>

# HTTP API server (P2)
python -m coding_agent serve --port 8080
```

---

## 6. Error Handling & Resilience

### Provider errors

```python
class ProviderErrorHandler:
    """Retry with exponential backoff for transient errors."""

    retry_on = {429, 500, 502, 503, 529}  # rate limit + server errors
    max_retries = 3
    base_delay = 1.0  # seconds, exponential with jitter

    async def call_with_retry(self, fn, *args) -> StreamEvent:
        for attempt in range(self.max_retries + 1):
            try:
                return await fn(*args)
            except ProviderError as e:
                if e.status_code not in self.retry_on or attempt == self.max_retries:
                    raise
                delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(delay)
```

### Malformed tool calls

When the LLM generates invalid tool calls (wrong schema, unknown tool name, missing required args):
- Log the error to tape as an `event` entry
- Return a structured error message as tool_result so the LLM can self-correct
- After 3 consecutive malformed calls, abort the turn with a clear error

### Graceful shutdown

On `Ctrl-C` / `UserInterrupt`:
1. Cancel in-flight provider streaming (close httpx connection)
2. If a tool is executing (e.g., bash), send SIGTERM, wait 5s, then SIGKILL
3. Flush pending tape entries to JSONL (append is atomic at line level)
4. Emit `TurnEnd(interrupted=True)` on wire
5. Session is resumable — tape has all completed entries

### Token counting

```python
class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def count_messages(self, messages: list[Message]) -> int: ...

class TiktokenCounter(TokenCounter):
    """Use tiktoken for OpenAI models (exact count)."""

class ApproximateCounter(TokenCounter):
    """Fallback: 1 token ≈ 4 chars. Used for unknown models."""
```

Provider implementations expose `max_context_size` — the context builder uses `TokenCounter` to stay within budget.

---

## 7. Context Compaction Strategy

When the working set exceeds `token_budget`, the context module uses a three-layer strategy:

### Layer 1: Anchor-based truncation (free)
Drop entries before the most recent anchor. The anchor's `state` field carries a structured summary of prior work. This is the primary mechanism — most turns stay within budget.

### Layer 2: Selective pruning (free)
Within the current anchor window, prune low-value entries:
- Tool results that returned errors (keep only the error message, drop full output)
- Consecutive `file_read` results for the same file (keep only the latest)
- `event` entries (metadata, not needed for LLM reasoning)

### Layer 3: LLM-based summarization (costs one API call)
If still over budget after pruning, make a summarization call:
- Send the oldest N entries to the LLM with: "Summarize these interactions into key facts and decisions"
- Create a new synthetic anchor with the summary as `state`
- Drop the summarized entries

This is expensive and should be rare. The anchor handoff mechanism (called at natural phase transitions — planning done, sub-task complete, etc.) prevents most sessions from reaching Layer 3.

### Doom loop detection (detailed)

```python
class DoomDetector:
    """Detect repetitive tool calls that indicate the agent is stuck."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.last_tool: str | None = None
        self.last_args_hash: str | None = None
        self.count: int = 0

    def observe(self, tool: str, args: dict) -> bool:
        """Returns True if doom loop detected."""
        args_hash = hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()
        if tool == self.last_tool and args_hash == self.last_args_hash:
            self.count += 1
        else:
            self.last_tool = tool
            self.last_args_hash = args_hash
            self.count = 1
        return self.count >= self.threshold
```

Exact match on tool name + MD5 of sorted JSON args. Near-misses (same tool, different args) reset the counter — they indicate the agent is trying something different, not stuck.

---

## 8. Session Management (`core/session.py`)

```python
class Session:
    """Manages a conversation session's lifecycle and persistence."""

    id: str                    # UUID
    tape: Tape                 # The session's tape (JSONL file)
    config: Config             # Session-scoped config snapshot
    created_at: datetime
    updated_at: datetime
    status: Literal["active", "completed", "interrupted"]

    @classmethod
    def create(cls, config: Config) -> "Session":
        """Start a new session. Creates tape file at config.tape_dir/<id>.jsonl"""

    @classmethod
    def load(cls, session_id: str, config: Config) -> "Session":
        """Resume an existing session from its tape file."""

    def close(self, status: str = "completed") -> None:
        """Mark session as complete. Tape remains on disk for later inspection."""
```

Sessions are identified by UUID. Each session has one tape file. Resuming a session loads the tape and reconstructs state from the most recent anchor.

---

## 9. Testing Strategy

### Unit tests (no LLM required)

- **Tape**: append, read, fork, merge, JSONL serialization round-trip
- **Context**: working set assembly, anchor truncation, token budget enforcement
- **ToolRegistry**: registration, schema generation, dispatch
- **ApprovalPolicy**: rule matching, mode behavior, cache hit/miss
- **DoomDetector**: threshold detection, reset on different input
- **Config**: loading precedence, validation, env var mapping

### Integration tests (mock provider)

```python
class MockProvider(ChatProvider):
    """Returns scripted responses for deterministic testing."""
    def __init__(self, responses: list[StreamEvent]): ...
```

- **AgentLoop**: full turn with mock provider + real tools against a temp directory
- **SubAgent**: fork/merge with mock provider
- **Wire**: message flow TUI ↔ core with mock approval responses

### E2E tests (real LLM, limited)

- Smoke test: one simple task (create file, verify content) with real provider
- Must use `--approval yolo` and isolated temp repo
- Run separately from unit/integration (`pytest -m e2e`)

### Eval tests (ported from Go)

- 26 benchmark tasks with A/B framework
- Run as `python -m coding_agent.eval.ab` — separate from pytest

---

## 10. Tape Fork Semantics

`Tape.fork()` creates a **deep copy in memory** (no JSONL file):

```python
def fork(self) -> "Tape":
    """Create an in-memory copy for sub-agent execution.

    - Copies all entries up to this point (deep copy of list, entries are frozen)
    - Forked tape has no file path (in-memory only, entries in a list)
    - Append operations go to the in-memory list only
    - No concurrent write risk (fork is single-owner)
    """
    forked = Tape(path=None)  # None = in-memory mode
    forked._entries = list(self._entries)  # shallow copy of frozen entries
    return forked

def merge(self, forked: "Tape") -> None:
    """Merge new entries from forked tape back into this tape.

    Only appends entries that were added AFTER the fork point.
    """
    fork_point = len(self._entries)
    new_entries = forked._entries[fork_point:]
    for entry in new_entries:
        self.append(entry)  # appends to both memory and JSONL
```

---

## 11. File Patch Semantics

The simplified patch format (`file_patch` tool) follows these rules:

**Application**: All-or-nothing. If any file operation fails (ambiguous match, file not found), the entire patch is rejected and the agent gets an error message to retry.

**Matching**: Context lines (unprefixed) are used for locating the edit position. Matching is:
1. Exact match first
2. If ambiguous (multiple matches), reject with error listing match locations
3. No fuzzy matching for patches — fuzzy matching is only for `file_replace`

**Atomicity**: Changes are staged in memory, then written to all files at once. If writing any file fails, all changes are rolled back (via git snapshot restore).

**Format**:
```
*** Update File: <path>
@@
 context_line_before       (space prefix = context, for positioning)
-removed_line              (minus prefix = delete)
+added_line                (plus prefix = add)
 context_line_after

*** Create File: <path>
+entire file content
+line by line

*** Delete File: <path>
```

No hunk line counts. No `---`/`+++` headers. No `@@ -n,m +n,m @@` line numbers. These are the exact elements that caused fragility in the Go agent.

---

## 12. Logging & Observability

```python
# Use structlog for structured, context-rich logging
import structlog

logger = structlog.get_logger()

# Every log entry includes session_id, turn_number, step_number
logger.info("tool_call", tool="bash", args={"cmd": "go test ./..."}, step=3)
logger.warning("doom_loop_detected", tool="file_replace", count=3)
logger.error("provider_error", status=429, retry_attempt=2)
```

Log levels:
- **DEBUG**: Full tool args/results, provider request/response (only in `--debug` mode)
- **INFO**: Turn begin/end, tool calls (name only), anchor creation
- **WARNING**: Doom loop, approval denied, context budget exceeded
- **ERROR**: Provider failures, malformed tool calls, unhandled exceptions

Logs go to `~/.coding-agent/logs/agent.log` (rotation: daily, retain 10 days). Tape is the source of truth for agent behavior; logs are for operational debugging.

---

## 13. Approval Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `yolo` | Auto-approve all operations | Trusted eval, batch mode |
| `interactive` | Ask user for every dangerous op | Normal TUI usage |
| `auto` | Rule-based, ask when no rule matches | Experienced users with custom rules |

Dangerous operations (default `ask`): `bash:*`, `file_write:*`, `file_replace:*`, `file_patch:*`
Safe operations (default `allow`): `file_read:*`, `grep:*`, `glob:*`, `kb_search:*`, `web_search:*`

---

## 14. Implementation Phases

### P0: Agent Kernel (s01-s02) — MVP

Deliverables:
- `core/loop.py` — while loop + tool dispatch
- `core/tape.py` — append-only JSONL storage
- `core/context.py` — basic working set assembly
- `providers/base.py` + `providers/openai_compat.py` — one provider
- `tools/registry.py` + `tools/file.py` + `tools/shell.py` + `tools/search.py`
- `ui/headless.py` — minimal batch mode for testing

Exit criteria: Agent can receive a goal, read files, execute commands, edit files via search-and-replace, and produce a result. Runnable as `python -m coding_agent run --goal "..." --repo .`

### P1: Planning + Sub-agents (s03-s04)

Deliverables:
- `core/planner.py` — TodoWrite-style task planning
- `agents/subagent.py` — fork/merge tape, independent execution
- `providers/anthropic.py` — second provider

Exit criteria: Agent creates a plan before acting. Can dispatch sub-agents for independent tasks.

### P2: Wire + TUI + HTTP API (interactive mode)

Deliverables:
- `wire/protocol.py` — typed message contract
- `wire/local.py` — async implementation
- `ui/tui.py` — Textual-based TUI with streaming, approval dialogs
- `ui/http_server.py` — REST + SSE API (FastAPI/Litestar)
- `approval/policy.py` + `approval/store.py`

HTTP API endpoints:
- `POST /sessions` — create session
- `POST /sessions/{id}/prompt` — send message (returns SSE stream)
- `POST /sessions/{id}/approve` — approval response
- `GET /sessions/{id}/events` — SSE event stream
- `GET /sessions/{id}` — session state
- `DELETE /sessions/{id}` — close session

CLI entry: `python -m coding_agent serve --port 8080`

HTTP API design rules:
- **One turn at a time per session**: `POST /sessions/{id}/prompt` returns `409 Conflict` if a turn is already in progress. The AgentLoop is not re-entrant.
- **SSE streams**: `GET /sessions/{id}/events` is the persistent EventSource-compatible stream (GET, supports Last-Event-ID for reconnection). `POST /sessions/{id}/prompt` returns an inline SSE stream for the current turn only (non-standard but convenient for programmatic clients).
- **SSE event types**: Wire messages are serialized directly as SSE data with `event:` field matching the WireMessage class name (e.g., `event: StreamDelta`, `event: ApprovalRequest`, `event: TurnEnd`).
- **Approval over HTTP**: When an `ApprovalRequest` SSE event is emitted, the client must respond via `POST /sessions/{id}/approve` within a configurable timeout (default 120s). On timeout, the request is auto-denied with feedback "approval timed out".
- **Session idle timeout**: Sessions with no activity for 30 minutes are automatically closed and their resources released. The tape remains on disk for later `resume`.
- **Concurrent SSE connections**: Multiple SSE clients can connect to the same session (fan-out). All receive the same events.

Exit criteria: Interactive TUI where user can chat, approve/deny tool calls, interrupt execution. HTTP API can create sessions, send prompts, and receive streaming responses. Supports yolo/interactive/auto modes.

### P3: RAG + Context (s05-s06)

Deliverables:
- `tools/kb.py` — integrated from existing kb/server.py
- `core/context.py` — full anchor-based context with token budgeting
- `skills/__init__.py` — skill loader + SKILL.md format

Exit criteria: KB search works, long sessions maintain quality via anchors, skills can be loaded and injected.

### P4: Sandbox + Tasks (s07-s08)

Deliverables:
- `sandbox/snapshot.py` — git snapshot undo/redo
- `sandbox/base.py` — Sandbox protocol (container stub)
- `tasks/graph.py` — task graph with dependencies
- `tasks/background.py` — async background execution

Exit criteria: File changes are snapshot-protected. Tasks can express dependencies and run in parallel.

### P5: Protocol Integration (MCP + ACP)

Deliverables:
- `tools/mcp_provider.py` — dynamic tool loading from MCP servers
- `wire/acp_adapter.py` — ACP JSON-RPC bridge
- `tools/acp_backend.py` — tool execution via editor

Exit criteria: Can load tools from MCP servers (e.g., web_search). Can be launched by VS Code/Zed as an ACP agent.

### P6: Eval Framework

Deliverables:
- `eval/benchmark.py` — benchmark task runner
- `eval/ab.py` — A/B testing with paired analysis, McNemar test
- Port existing 26 benchmark tasks

Exit criteria: Can run A/B eval identical to current Go agent's eval pipeline.

### P7: Multi-Agent (s09-s12, deferred)

Deliverables:
- `agents/team.py` — agent teams with async mailbox
- `agents/autonomous.py` — self-claiming task model
- `tasks/worktree.py` — git worktree isolation per agent

Exit criteria: Multiple agents can collaborate on complex tasks with proper isolation.

---

## 15. Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.12+ | Ecosystem, career, unified stack with KB |
| Package manager | uv | Fast, reliable, user preference |
| Async | asyncio | Native, compatible with all libraries |
| HTTP client | httpx | Async, streaming, used by openai/anthropic SDKs |
| LLM SDKs | openai + anthropic (direct) | Thin wrapper over official SDKs |
| TUI | Textual | Rich terminal UI, async-native, active community |
| HTTP API | FastAPI | Lightweight, async-native, auto-generated OpenAPI docs, SSE support |
| Validation | Pydantic v2 | Wire protocol, config, tool schemas |
| Vector DB | LanceDB | Proven in current implementation |
| Persistence | JSONL (tape) + SQLite (eval) | JSONL for append-only, SQLite for queries |
| Testing | pytest | Standard, user preference |
| MCP | mcp Python SDK | Official SDK |
| ACP | agent-client-protocol Python SDK | Official SDK |

---

## 16. Key Design Decisions

### D1: Tape over message history
Context is a constructed working set, not an inherited history. Anchors mark phase transitions and carry structured state. This prevents context window linear explosion and supports long-running sessions.

### D2: Search-and-replace over unified diff
Primary editing via `old_string → new_string`. No hunk line counting. Simplified patch format only for multi-file coordinated edits. Eliminates the root cause of the Go agent's biggest reliability issue.

### D3: Wire protocol for transport independence
Agent core communicates via typed WireMessages. TUI, batch, and ACP are all wire consumers. Adding a new frontend means implementing WireConsumer, not modifying the core.

### D4: ToolBackend abstraction for ACP
Tools don't know where they execute. In TUI/batch mode, LocalBackend reads files directly. In ACP mode, ACPBackend forwards to the editor via JSON-RPC. Same tool, different backend.

### D5: Fork/merge tape for sub-agents
Sub-agents get a forked tape (in-memory copy). Success → merge back. Failure → discard. Main conversation stays clean.

### D6: Sandbox as protocol, not implementation
v1 uses git snapshots. The Sandbox protocol (snapshot/restore/diff) is ready for Docker/Kata when needed. Eval already has K8S Pod isolation.

### D7: Approval modes are policy, not architecture
yolo/interactive/auto are ApprovalPolicy configurations, not separate code paths. The agent loop always calls `approval.evaluate()` — the policy decides whether to ask, auto-approve, or rule-match.

### D8: MCP for external tools, built-in for core
Core tools (file, shell, search, kb) are built-in for reliability and offline use. External tools (web_search, browser, Slack) load via MCP servers. No need to implement every tool.
