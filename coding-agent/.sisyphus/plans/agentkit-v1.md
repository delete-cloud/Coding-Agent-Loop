# Agentkit v1 — Three-Framework Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite coding-agent into a two-layer architecture: `agentkit` (framework) + `coding_agent` (agent), fusing patterns from Bub (pipeline, ForkTapeStore, plugin discovery), Jido (Directives, Plugin=state_key+hooks, Instruction normalize), and Kapybara (persistent shell, finish_action, two-layer memory).

**Architecture:** Custom HookRuntime dispatches a Bub-style linear pipeline (`resolve_session → load_state → build_context → run_model → save_state → render → dispatch`). Plugins register hooks that return either concrete instances (provide_*) or Directive structs (approve_tool_call, on_turn_end) which the runtime executes. Framework layer (`agentkit`) owns zero LLM logic; agent layer (`coding_agent`) supplies all domain-specific behavior via plugins.

**Tech Stack:** Python 3.12+, hatchling build, pytest, structlog, TOML config, pydantic (models), httpx (HTTP), openai/anthropic SDKs, lancedb (vector store), Click (CLI), Rich (TUI).

---

## Decisions Reference

| # | Decision | Choice |
|---|----------|--------|
| 1 | Hook impl | Custom HookRuntime (no pluggy) |
| 2 | Directive | Mixed mode: provide_* returns instances, side-effect hooks return Directive structs |
| 3 | Repo split | Single repo, two packages first; physical split later |
| 4 | V1 scope | All 9 patterns (ForkTapeStore, Instruction normalize, persistent shell, finish_action, Channel, Plugin lifecycle) |
| 5 | TDD | RED-GREEN-REFACTOR |
| 6 | Plugin granularity | Coarse: 1 Plugin = 1 concern (multiple hooks + state_key) |
| 7 | Storage | 3 Protocols: TapeStore + DocIndex + SessionStore + ForkTapeStore layer |
| 8 | Naming | `agentkit` (framework) + `coding_agent` (agent) |
| 9 | Core loop | Bub-style linear pipeline |
| 10 | Migration | Full rewrite |
| 11 | Hooks (10) | provide_storage, get_tools, provide_llm, approve_tool_call, summarize_context, on_error, mount, on_checkpoint, build_context, on_turn_end |
| 12 | Directive specifics | approve_tool_call→Directive; on_error→observer; provide_*→instances |

---

## File Structure

### agentkit (framework layer): `src/agentkit/`

```
src/agentkit/
├── __init__.py                  # Public API: HookRuntime, Pipeline, Plugin, Entry, Tape, etc.
├── py.typed                     # PEP 561 marker
├── runtime/
│   ├── __init__.py
│   ├── hook_runtime.py          # HookRuntime: call_first, call_many, notify
│   ├── pipeline.py              # Pipeline: process_inbound() linear stage runner
│   └── hookspecs.py             # 10 hook specs with metadata (firstresult, directive, observer)
├── plugin/
│   ├── __init__.py
│   ├── protocol.py              # Plugin Protocol: state_key, hooks(), mount(), lifecycle
│   └── registry.py              # PluginRegistry: register, discover, topological sort
├── directive/
│   ├── __init__.py
│   ├── types.py                 # Directive base + concrete: Approve, Reject, AskUser, Checkpoint, MemoryRecord
│   └── executor.py              # DirectiveExecutor: dispatch directives to side effects
├── tape/
│   ├── __init__.py
│   ├── models.py                # Entry dataclass (id, kind, payload, ts), EntryKind
│   ├── tape.py                  # Tape: append-only log, iteration, serialization
│   └── store.py                 # TapeStore Protocol + ForkTapeStore (begin/commit/rollback)
├── storage/
│   ├── __init__.py
│   ├── protocols.py             # TapeStore, DocIndex, SessionStore Protocols
│   └── session.py               # SessionStore default impl (JSONL + JSON metadata)
├── tools/
│   ├── __init__.py
│   ├── decorator.py             # @tool decorator: wraps functions, generates schemas
│   ├── registry.py              # ToolRegistry: register, get, schemas, execute
│   └── schema.py                # ToolSchema model (name, description, parameters JSON Schema)
├── providers/
│   ├── __init__.py
│   ├── protocol.py              # LLMProvider Protocol: stream(), model_name, max_context_size
│   └── models.py                # StreamEvent, Message, ToolCall models
├── channel/
│   ├── __init__.py
│   ├── protocol.py              # Channel Protocol: send, receive, subscribe
│   └── local.py                 # LocalChannel: in-process async channel
├── instruction/
│   ├── __init__.py
│   └── normalize.py             # Instruction normalize: str|dict|Message → Message
├── config/
│   ├── __init__.py
│   └── loader.py                # TOML config loader: load_config(path) → AgentConfig
├── context/
│   ├── __init__.py
│   └── builder.py               # ContextBuilder: assemble messages from tape + grounding
├── errors.py                    # Framework error hierarchy
└── _types.py                    # Shared type aliases (HookName, PluginId, etc.)
```

### coding_agent (agent layer): `src/coding_agent/`

```
src/coding_agent/
├── __init__.py
├── __main__.py                  # Bootstrap: load agent.toml → create_agent() → pipeline.run()
├── agent.toml                   # Agent config: model, provider, plugins, storage backends
├── plugins/
│   ├── __init__.py
│   ├── core_tools.py            # CoreToolsPlugin: file, search, shell tools via @tool
│   ├── llm_provider.py          # LLMProviderPlugin: provide_llm hook, Anthropic/OpenAI
│   ├── storage.py               # StoragePlugin: provide_storage, session management
│   ├── approval.py              # ApprovalPlugin: approve_tool_call → Directive
│   ├── summarizer.py            # SummarizerPlugin: summarize_context hook
│   ├── memory.py                # MemoryPlugin: build_context (grounding) + on_turn_end (finish_action)
│   └── shell_session.py         # ShellSessionPlugin: persistent bash via on_checkpoint
├── tools/
│   ├── __init__.py
│   ├── file_ops.py              # @tool: file_read, file_write, file_replace, glob, grep
│   ├── shell.py                 # @tool: bash_run (+ persistent session support)
│   ├── search.py                # @tool: code_search, web_search
│   ├── planner.py               # @tool: todo_write, todo_read
│   └── subagent.py              # @tool: spawn_subagent
└── providers/
    ├── __init__.py
    ├── anthropic.py             # AnthropicProvider: implements LLMProvider
    └── openai_compat.py         # OpenAICompatProvider: implements LLMProvider
```

### Tests: `tests/`

```
tests/
├── conftest.py                  # Shared fixtures: mock_plugin, mock_runtime, tape_factory
├── agentkit/
│   ├── runtime/
│   │   ├── test_hook_runtime.py
│   │   ├── test_pipeline.py
│   │   └── test_hookspecs.py
│   ├── plugin/
│   │   ├── test_protocol.py
│   │   └── test_registry.py
│   ├── directive/
│   │   ├── test_types.py
│   │   └── test_executor.py
│   ├── tape/
│   │   ├── test_models.py
│   │   ├── test_tape.py
│   │   └── test_store.py
│   ├── storage/
│   │   ├── test_protocols.py
│   │   └── test_session.py
│   ├── tools/
│   │   ├── test_decorator.py
│   │   ├── test_registry.py
│   │   └── test_schema.py
│   ├── channel/
│   │   ├── test_protocol.py
│   │   └── test_local.py
│   ├── instruction/
│   │   └── test_normalize.py
│   ├── config/
│   │   └── test_loader.py
│   ├── context/
│   │   └── test_builder.py
│   └── test_errors.py
└── coding_agent/
    ├── plugins/
    │   ├── test_core_tools.py
    │   ├── test_llm_provider.py
    │   ├── test_storage.py
    │   ├── test_approval.py
    │   ├── test_summarizer.py
    │   ├── test_memory.py
    │   └── test_shell_session.py
    ├── tools/
    │   ├── test_file_ops.py
    │   ├── test_shell.py
    │   └── test_search.py
    └── test_bootstrap.py
```

---

## Task Dependency Graph

```
T1 (errors + types) ──┐
                       ├── T2 (Entry + Tape models)
                       │         │
T3 (Directive types) ──┤         │
                       │         ├── T5 (TapeStore + ForkTapeStore)
T4 (Plugin Protocol) ──┤         │
                       │         ├── T8 (ContextBuilder)
                       ├── T6 (HookRuntime)
                       │         │
                       │         ├── T7 (Hookspecs)
                       │         │
T14 (LLM Provider) ───┤         │
                       │         ├── T10 (Pipeline) ← depends on T6, T7, T14
                       │         │         │
T9 (@tool + Registry)──┤         │         ├── T13 (TOML Config)
                       │         │         │         │
T11 (Channel) ─────────┤         │         │         │
                       │         │         │         │
T12 (Instruction) ─────┘         │         │         │
                                 │         │         │
T5 (Storage Protocols) ──────────┘         │         │
                                           │         │
T8 (ContextBuilder) ───────────────────────┘         │
                                                     │
--- Agent Layer ---                                   │
T15 (LLMProviderPlugin) ────────────────────────────┤
T16 (StoragePlugin) ────────────────────────────────┤
T17 (CoreToolsPlugin + file/shell/search tools) ────┤
T18 (ApprovalPlugin) ───────────────────────────────┤
T19 (SummarizerPlugin) ─────────────────────────────┤
T20 (MemoryPlugin + finish_action) ─────────────────┤
T21 (ShellSessionPlugin + persistent bash) ──────────┤
T22 (DirectiveExecutor integration) ─────────────────┤
T23 (End-to-end integration test) ───────────────────┘
```

> **Execution order note**: T14 (LLM Provider Protocol + StreamEvent Models) MUST be
> completed before T10 (Pipeline), because Pipeline imports `TextEvent`, `ToolCallEvent`,
> `DoneEvent` from `agentkit.providers.models`. The recommended execution order for the
> agentkit layer is: T1 → T2/T3/T4 (parallel) → T5/T6/T9/T11/T12/T14 (parallel) →
> T7 → T8 → T10 → T13 → T22.

---

## Tasks

### Task 1: Error Hierarchy + Shared Types

**Files:**
- Create: `src/agentkit/errors.py`
- Create: `src/agentkit/_types.py`
- Create: `src/agentkit/__init__.py`
- Create: `src/agentkit/py.typed`
- Test: `tests/agentkit/test_errors.py`

- [ ] **Step 1: Create package skeleton**

```bash
mkdir -p src/agentkit/runtime src/agentkit/plugin src/agentkit/directive \
  src/agentkit/tape src/agentkit/storage src/agentkit/tools \
  src/agentkit/providers src/agentkit/channel src/agentkit/instruction \
  src/agentkit/config src/agentkit/context \
  tests/agentkit/runtime tests/agentkit/plugin tests/agentkit/directive \
  tests/agentkit/tape tests/agentkit/storage tests/agentkit/tools \
  tests/agentkit/channel tests/agentkit/instruction tests/agentkit/config \
  tests/agentkit/context tests/coding_agent/plugins tests/coding_agent/tools
touch src/agentkit/py.typed
```

Create `__init__.py` in every package directory (empty for now).

- [ ] **Step 2: Write failing tests for error hierarchy**

```python
# tests/agentkit/test_errors.py
import pytest
from agentkit.errors import (
    AgentKitError,
    HookError,
    PipelineError,
    PluginError,
    DirectiveError,
    StorageError,
    ToolError,
    ConfigError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        for cls in [HookError, PipelineError, PluginError, DirectiveError,
                    StorageError, ToolError, ConfigError]:
            err = cls("test")
            assert isinstance(err, AgentKitError)
            assert isinstance(err, Exception)

    def test_error_message_preserved(self):
        err = HookError("hook 'provide_llm' failed")
        assert str(err) == "hook 'provide_llm' failed"

    def test_hook_error_captures_hook_name(self):
        err = HookError("failed", hook_name="provide_llm")
        assert err.hook_name == "provide_llm"

    def test_plugin_error_captures_plugin_id(self):
        err = PluginError("failed", plugin_id="memory")
        assert err.plugin_id == "memory"

    def test_pipeline_error_captures_stage(self):
        err = PipelineError("failed", stage="load_state")
        assert err.stage == "load_state"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentkit'`

- [ ] **Step 4: Implement error hierarchy**

```python
# src/agentkit/errors.py
"""agentkit error hierarchy.

All framework errors inherit from AgentKitError.
Domain-specific errors carry contextual attributes (hook_name, plugin_id, stage).
"""

from __future__ import annotations


class AgentKitError(Exception):
    """Base error for all agentkit exceptions."""


class HookError(AgentKitError):
    """A hook invocation failed."""

    def __init__(self, message: str, *, hook_name: str | None = None) -> None:
        super().__init__(message)
        self.hook_name = hook_name


class PipelineError(AgentKitError):
    """A pipeline stage failed."""

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage


class PluginError(AgentKitError):
    """Plugin initialization or lifecycle error."""

    def __init__(self, message: str, *, plugin_id: str | None = None) -> None:
        super().__init__(message)
        self.plugin_id = plugin_id


class DirectiveError(AgentKitError):
    """Directive execution failed."""


class StorageError(AgentKitError):
    """Storage operation failed."""


class ToolError(AgentKitError):
    """Tool execution failed."""


class ConfigError(AgentKitError):
    """Configuration loading or validation error."""
```

- [ ] **Step 5: Implement shared types**

```python
# src/agentkit/_types.py
"""Shared type aliases used across agentkit."""

from __future__ import annotations

from typing import Any, Literal

# Hook system
HookName = str
PluginId = str

# Entry kinds — extensible via Literal union
EntryKind = Literal["message", "tool_call", "tool_result", "anchor", "event"]

# Role for messages
Role = Literal["system", "user", "assistant", "tool"]

# JSON-compatible dict
JsonDict = dict[str, Any]

# Pipeline stage names
StageName = Literal[
    "resolve_session",
    "load_state",
    "build_context",
    "run_model",
    "save_state",
    "render",
    "dispatch",
]
```

- [ ] **Step 6: Update agentkit __init__.py with public API**

```python
# src/agentkit/__init__.py
"""agentkit — A hook-driven agent framework."""

from agentkit.errors import (
    AgentKitError,
    ConfigError,
    DirectiveError,
    HookError,
    PipelineError,
    PluginError,
    StorageError,
    ToolError,
)

__all__ = [
    "AgentKitError",
    "ConfigError",
    "DirectiveError",
    "HookError",
    "PipelineError",
    "PluginError",
    "StorageError",
    "ToolError",
]
```

- [ ] **Step 7: Update pyproject.toml to include agentkit package**

Add `"src/agentkit"` to the packages list in `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/coding_agent", "src/agentkit"]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/test_errors.py -v`
Expected: All 5 tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/agentkit/ tests/agentkit/ pyproject.toml
git commit -m "feat(agentkit): add error hierarchy and shared types"
```

---

### Task 2: Entry + Tape Models

**Files:**
- Create: `src/agentkit/tape/models.py`
- Create: `src/agentkit/tape/tape.py`
- Create: `src/agentkit/tape/__init__.py`
- Test: `tests/agentkit/tape/test_models.py`
- Test: `tests/agentkit/tape/test_tape.py`

- [ ] **Step 1: Write failing tests for Entry model**

```python
# tests/agentkit/tape/test_models.py
import pytest
from agentkit.tape.models import Entry, EntryKind
from datetime import datetime


class TestEntry:
    def test_create_message_entry(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hello"})
        assert entry.kind == "message"
        assert entry.payload["content"] == "hello"
        assert entry.id  # auto-generated UUID
        assert isinstance(entry.timestamp, float)

    def test_entry_is_frozen(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        with pytest.raises(AttributeError):
            entry.kind = "tool_call"

    def test_entry_kinds(self):
        for kind in ("message", "tool_call", "tool_result", "anchor", "event"):
            entry = Entry(kind=kind, payload={})
            assert entry.kind == kind

    def test_entry_to_dict(self):
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        d = entry.to_dict()
        assert d["kind"] == "message"
        assert d["payload"]["content"] == "hi"
        assert "id" in d
        assert "timestamp" in d

    def test_entry_from_dict(self):
        d = {"id": "abc-123", "kind": "message", "payload": {"role": "user", "content": "hi"}, "timestamp": 1000.0}
        entry = Entry.from_dict(d)
        assert entry.id == "abc-123"
        assert entry.kind == "message"
        assert entry.payload["content"] == "hi"
        assert entry.timestamp == 1000.0

    def test_entry_roundtrip(self):
        original = Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "/a.py"}})
        restored = Entry.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.kind == original.kind
        assert restored.payload == original.payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/tape/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Entry model**

```python
# src/agentkit/tape/models.py
"""Tape entry model — the atomic unit of agent conversation history."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentkit._types import EntryKind


@dataclass(frozen=True)
class Entry:
    """An immutable entry in the conversation tape.

    Entries are the atomic unit of agent history. Each entry has a kind
    (message, tool_call, tool_result, anchor, event) and an arbitrary payload.
    """

    kind: EntryKind
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize entry to a plain dict."""
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        """Deserialize entry from a plain dict."""
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agentkit/tape/test_models.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Write failing tests for Tape**

```python
# tests/agentkit/tape/test_tape.py
import pytest
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestTape:
    def test_empty_tape(self):
        tape = Tape()
        assert len(tape) == 0
        assert list(tape) == []

    def test_append_entry(self):
        tape = Tape()
        entry = Entry(kind="message", payload={"role": "user", "content": "hi"})
        tape.append(entry)
        assert len(tape) == 1
        assert tape[0] is entry

    def test_iterate_entries(self):
        tape = Tape()
        e1 = Entry(kind="message", payload={"role": "user", "content": "a"})
        e2 = Entry(kind="message", payload={"role": "assistant", "content": "b"})
        tape.append(e1)
        tape.append(e2)
        assert list(tape) == [e1, e2]

    def test_slice(self):
        tape = Tape()
        entries = [Entry(kind="message", payload={"content": str(i)}) for i in range(5)]
        for e in entries:
            tape.append(e)
        assert tape[1:3] == entries[1:3]

    def test_filter_by_kind(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(Entry(kind="tool_call", payload={"name": "bash"}))
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "ok"}))
        messages = tape.filter(kind="message")
        assert len(messages) == 2

    def test_fork_creates_independent_copy(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = tape.fork()
        forked.append(Entry(kind="message", payload={"content": "fork-only"}))
        assert len(tape) == 1
        assert len(forked) == 2

    def test_fork_preserves_parent_id(self):
        tape = Tape(tape_id="parent-1")
        forked = tape.fork()
        assert forked.parent_id == "parent-1"
        assert forked.tape_id != "parent-1"

    def test_serialize_roundtrip(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(Entry(kind="tool_call", payload={"name": "bash"}))
        data = tape.to_list()
        restored = Tape.from_list(data)
        assert len(restored) == 2
        assert restored[0].kind == "message"
        assert restored[1].kind == "tool_call"

    def test_jsonl_roundtrip(self, tmp_path):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        path = tmp_path / "tape.jsonl"
        tape.save_jsonl(path)
        restored = Tape.load_jsonl(path)
        assert len(restored) == 1
        assert restored[0].payload["content"] == "hi"
```

- [ ] **Step 6: Implement Tape**

```python
# src/agentkit/tape/tape.py
"""Tape — append-only conversation log with fork support."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, overload

from agentkit._types import EntryKind
from agentkit.tape.models import Entry


class Tape:
    """Thread-safe, append-only conversation log.

    Supports forking (creating independent copies that track lineage),
    filtering by entry kind, and JSONL serialization.
    """

    def __init__(
        self,
        entries: list[Entry] | None = None,
        tape_id: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        self._entries: list[Entry] = list(entries or [])
        self.tape_id: str = tape_id or str(uuid.uuid4())
        self.parent_id: str | None = parent_id
        self._lock = Lock()

    def append(self, entry: Entry) -> None:
        """Append an entry to the tape (thread-safe)."""
        with self._lock:
            self._entries.append(entry)

    def filter(self, kind: EntryKind) -> list[Entry]:
        """Return entries matching the given kind."""
        with self._lock:
            return [e for e in self._entries if e.kind == kind]

    def fork(self) -> Tape:
        """Create an independent copy with lineage tracking."""
        with self._lock:
            return Tape(
                entries=list(self._entries),
                parent_id=self.tape_id,
            )

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize all entries to a list of dicts."""
        with self._lock:
            return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]], **kwargs: Any) -> Tape:
        """Deserialize from a list of dicts."""
        entries = [Entry.from_dict(d) for d in data]
        return cls(entries=entries, **kwargs)

    def save_jsonl(self, path: Path) -> None:
        """Save tape to JSONL file."""
        with self._lock:
            with open(path, "w") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry.to_dict()) + "\n")

    @classmethod
    def load_jsonl(cls, path: Path, **kwargs: Any) -> Tape:
        """Load tape from JSONL file."""
        entries: list[Entry] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(Entry.from_dict(json.loads(line)))
        return cls(entries=entries, **kwargs)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @overload
    def __getitem__(self, index: int) -> Entry: ...
    @overload
    def __getitem__(self, index: slice) -> list[Entry]: ...

    def __getitem__(self, index: int | slice) -> Entry | list[Entry]:
        with self._lock:
            return self._entries[index]

    def __iter__(self) -> Iterator[Entry]:
        with self._lock:
            return iter(list(self._entries))
```

- [ ] **Step 7: Update tape __init__.py**

```python
# src/agentkit/tape/__init__.py
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

__all__ = ["Entry", "Tape"]
```

- [ ] **Step 8: Run all tape tests**

Run: `uv run pytest tests/agentkit/tape/ -v`
Expected: All 15 tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/agentkit/tape/ tests/agentkit/tape/
git commit -m "feat(agentkit): add Entry model and Tape with fork support"
```

---

### Task 3: Directive Types

**Files:**
- Create: `src/agentkit/directive/types.py`
- Create: `src/agentkit/directive/__init__.py`
- Test: `tests/agentkit/directive/test_types.py`

- [ ] **Step 1: Write failing tests for Directive types**

```python
# tests/agentkit/directive/test_types.py
import pytest
from agentkit.directive.types import (
    Directive,
    Approve,
    Reject,
    AskUser,
    Checkpoint,
    MemoryRecord,
)


class TestDirectiveTypes:
    def test_approve_is_directive(self):
        d = Approve()
        assert isinstance(d, Directive)

    def test_reject_carries_reason(self):
        d = Reject(reason="dangerous command")
        assert d.reason == "dangerous command"

    def test_ask_user_carries_question(self):
        d = AskUser(question="Run rm -rf?")
        assert d.question == "Run rm -rf?"

    def test_checkpoint_carries_data(self):
        d = Checkpoint(plugin_id="memory", state={"key": "value"})
        assert d.plugin_id == "memory"
        assert d.state == {"key": "value"}

    def test_memory_record_fields(self):
        d = MemoryRecord(
            summary="User fixed a bug in auth.py",
            tags=["bugfix", "auth"],
            importance=0.8,
        )
        assert d.summary == "User fixed a bug in auth.py"
        assert d.tags == ["bugfix", "auth"]
        assert d.importance == 0.8

    def test_directive_is_frozen(self):
        d = Approve()
        with pytest.raises(AttributeError):
            d.kind = "reject"  # type: ignore[attr-defined]

    def test_all_directives_have_kind(self):
        assert Approve().kind == "approve"
        assert Reject(reason="no").kind == "reject"
        assert AskUser(question="?").kind == "ask_user"
        assert Checkpoint(plugin_id="x", state={}).kind == "checkpoint"
        assert MemoryRecord(summary="x").kind == "memory_record"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/directive/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Directive types**

```python
# src/agentkit/directive/types.py
"""Directive types — structured effect descriptions returned by hooks.

Directives are frozen dataclasses that describe what the runtime should do.
The runtime's DirectiveExecutor dispatches each directive to the appropriate
side effect. Plugins never perform side effects directly — they return Directives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Directive:
    """Base class for all directives. Subclasses must set `kind`."""

    kind: str = field(init=False)


@dataclass(frozen=True)
class Approve(Directive):
    """Approve a tool call to proceed."""

    kind: str = field(init=False, default="approve")


@dataclass(frozen=True)
class Reject(Directive):
    """Reject a tool call with a reason."""

    reason: str = ""
    kind: str = field(init=False, default="reject")


@dataclass(frozen=True)
class AskUser(Directive):
    """Pause and ask the user a question before proceeding."""

    question: str = ""
    kind: str = field(init=False, default="ask_user")


@dataclass(frozen=True)
class Checkpoint(Directive):
    """Request the runtime to persist plugin state."""

    plugin_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    kind: str = field(init=False, default="checkpoint")


@dataclass(frozen=True)
class MemoryRecord(Directive):
    """A structured memory produced by finish_action at turn end."""

    summary: str = ""
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    kind: str = field(init=False, default="memory_record")
```

- [ ] **Step 4: Update directive __init__.py**

```python
# src/agentkit/directive/__init__.py
from agentkit.directive.types import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    MemoryRecord,
    Reject,
)

__all__ = ["Approve", "AskUser", "Checkpoint", "Directive", "MemoryRecord", "Reject"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/agentkit/directive/test_types.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/directive/ tests/agentkit/directive/
git commit -m "feat(agentkit): add Directive types (Approve, Reject, AskUser, Checkpoint, MemoryRecord)"
```

---

### Task 4: Plugin Protocol

**Files:**
- Create: `src/agentkit/plugin/protocol.py`
- Create: `src/agentkit/plugin/registry.py`
- Create: `src/agentkit/plugin/__init__.py`
- Test: `tests/agentkit/plugin/test_protocol.py`
- Test: `tests/agentkit/plugin/test_registry.py`

- [ ] **Step 1: Write failing tests for Plugin Protocol**

```python
# tests/agentkit/plugin/test_protocol.py
import pytest
from agentkit.plugin.protocol import Plugin


class DummyPlugin:
    """A minimal plugin implementation for testing."""

    state_key = "dummy"

    def hooks(self) -> dict[str, callable]:
        return {"mount": self.do_mount}

    def do_mount(self) -> dict:
        return {"initialized": True}


class PluginWithoutStateKey:
    """Plugin missing state_key — should fail protocol check."""

    def hooks(self) -> dict[str, callable]:
        return {}


class TestPluginProtocol:
    def test_valid_plugin_satisfies_protocol(self):
        p = DummyPlugin()
        assert isinstance(p, Plugin)

    def test_plugin_state_key(self):
        p = DummyPlugin()
        assert p.state_key == "dummy"

    def test_plugin_hooks_returns_dict(self):
        p = DummyPlugin()
        h = p.hooks()
        assert isinstance(h, dict)
        assert "mount" in h

    def test_missing_state_key_fails_protocol(self):
        p = PluginWithoutStateKey()
        assert not isinstance(p, Plugin)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/plugin/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Plugin Protocol**

```python
# src/agentkit/plugin/protocol.py
"""Plugin Protocol — the contract every plugin must satisfy.

A Plugin is a coarse-grained unit of agent behavior. Each Plugin:
  - Has a unique state_key for its state namespace
  - Declares which hooks it implements via hooks() → dict[str, callable]
  - Optionally implements mount() for initialization
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Protocol that all agentkit plugins must satisfy.

    Attributes:
        state_key: Unique identifier for this plugin's state namespace.
    """

    state_key: str

    def hooks(self) -> dict[str, Callable[..., Any]]:
        """Return a mapping of hook_name → callable for this plugin.

        Example:
            {"provide_llm": self.provide_llm, "on_error": self.on_error}
        """
        ...
```

- [ ] **Step 4: Run protocol tests**

Run: `uv run pytest tests/agentkit/plugin/test_protocol.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Write failing tests for PluginRegistry**

```python
# tests/agentkit/plugin/test_registry.py
import pytest
from agentkit.plugin.registry import PluginRegistry
from agentkit.plugin.protocol import Plugin
from agentkit.errors import PluginError


class FakePluginA:
    state_key = "alpha"

    def hooks(self):
        return {"mount": self.do_mount, "get_tools": self.get_tools}

    def do_mount(self):
        return {"ready": True}

    def get_tools(self):
        return []


class FakePluginB:
    state_key = "beta"

    def hooks(self):
        return {"mount": self.do_mount}

    def do_mount(self):
        return {}


class InvalidPlugin:
    """Not a valid plugin — missing state_key."""

    def hooks(self):
        return {}


class TestPluginRegistry:
    def test_register_plugin(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        assert "alpha" in reg.plugin_ids()

    def test_register_multiple(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        reg.register(FakePluginB())
        assert reg.plugin_ids() == ["alpha", "beta"]

    def test_duplicate_state_key_raises(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        with pytest.raises(PluginError, match="duplicate state_key"):
            reg.register(FakePluginA())

    def test_invalid_plugin_raises(self):
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="does not satisfy Plugin protocol"):
            reg.register(InvalidPlugin())  # type: ignore[arg-type]

    def test_get_hooks_for_name(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        reg.register(FakePluginB())
        mount_hooks = reg.get_hooks("mount")
        assert len(mount_hooks) == 2

    def test_get_hooks_for_missing_name(self):
        reg = PluginRegistry()
        reg.register(FakePluginA())
        hooks = reg.get_hooks("nonexistent")
        assert hooks == []

    def test_get_plugin_by_id(self):
        reg = PluginRegistry()
        plugin = FakePluginA()
        reg.register(plugin)
        assert reg.get("alpha") is plugin

    def test_get_missing_plugin_raises(self):
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="not found"):
            reg.get("nonexistent")
```

- [ ] **Step 6: Implement PluginRegistry**

```python
# src/agentkit/plugin/registry.py
"""PluginRegistry — manages plugin registration and hook lookup.

Plugins are registered by instance. The registry validates the Plugin protocol,
ensures unique state_keys, and provides fast hook-name → callable[] lookups.
"""

from __future__ import annotations

from typing import Any, Callable

from agentkit.errors import PluginError
from agentkit.plugin.protocol import Plugin


class PluginRegistry:
    """Registry for agentkit plugins.

    Maintains insertion order. Provides hook lookup by name.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}  # state_key → Plugin
        self._hook_index: dict[str, list[Callable[..., Any]]] = {}  # hook_name → [callable]

    def register(self, plugin: Plugin) -> None:
        """Register a plugin. Raises PluginError on protocol violation or duplicate key."""
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{type(plugin).__name__} does not satisfy Plugin protocol",
                plugin_id=getattr(plugin, "state_key", "<unknown>"),
            )
        key = plugin.state_key
        if key in self._plugins:
            raise PluginError(
                f"duplicate state_key '{key}'",
                plugin_id=key,
            )
        self._plugins[key] = plugin
        for hook_name, hook_fn in plugin.hooks().items():
            self._hook_index.setdefault(hook_name, []).append(hook_fn)

    def plugin_ids(self) -> list[str]:
        """Return all registered plugin IDs in insertion order."""
        return list(self._plugins.keys())

    def get(self, plugin_id: str) -> Plugin:
        """Get a plugin by state_key. Raises PluginError if not found."""
        if plugin_id not in self._plugins:
            raise PluginError(
                f"plugin '{plugin_id}' not found",
                plugin_id=plugin_id,
            )
        return self._plugins[plugin_id]

    def get_hooks(self, hook_name: str) -> list[Callable[..., Any]]:
        """Return all callables registered for a hook name."""
        return list(self._hook_index.get(hook_name, []))
```

- [ ] **Step 7: Update plugin __init__.py**

```python
# src/agentkit/plugin/__init__.py
from agentkit.plugin.protocol import Plugin
from agentkit.plugin.registry import PluginRegistry

__all__ = ["Plugin", "PluginRegistry"]
```

- [ ] **Step 8: Run all plugin tests**

Run: `uv run pytest tests/agentkit/plugin/ -v`
Expected: All 12 tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/agentkit/plugin/ tests/agentkit/plugin/
git commit -m "feat(agentkit): add Plugin Protocol and PluginRegistry"
```

---

### Task 5: Storage Protocols + ForkTapeStore

**Files:**
- Create: `src/agentkit/storage/protocols.py`
- Create: `src/agentkit/tape/store.py`
- Create: `src/agentkit/storage/session.py`
- Create: `src/agentkit/storage/__init__.py`
- Test: `tests/agentkit/storage/test_protocols.py`
- Test: `tests/agentkit/tape/test_store.py`
- Test: `tests/agentkit/storage/test_session.py`

- [ ] **Step 1: Write failing tests for storage protocols**

```python
# tests/agentkit/storage/test_protocols.py
import pytest
from agentkit.storage.protocols import TapeStore, DocIndex, SessionStore


class InMemoryTapeStore:
    """Minimal TapeStore for protocol testing."""

    def __init__(self):
        self._tapes = {}

    async def save(self, tape_id: str, entries: list[dict]) -> None:
        self._tapes[tape_id] = entries

    async def load(self, tape_id: str) -> list[dict]:
        return self._tapes.get(tape_id, [])

    async def list_ids(self) -> list[str]:
        return list(self._tapes.keys())


class InMemoryDocIndex:
    def __init__(self):
        self._docs = []

    async def upsert(self, doc_id: str, text: str, metadata: dict) -> None:
        self._docs.append({"id": doc_id, "text": text, "metadata": metadata})

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        return self._docs[:limit]

    async def delete(self, doc_id: str) -> None:
        self._docs = [d for d in self._docs if d["id"] != doc_id]


class InMemorySessionStore:
    def __init__(self):
        self._sessions = {}

    async def save_session(self, session_id: str, data: dict) -> None:
        self._sessions[session_id] = data

    async def load_session(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class TestStorageProtocols:
    def test_tape_store_satisfies_protocol(self):
        store = InMemoryTapeStore()
        assert isinstance(store, TapeStore)

    def test_doc_index_satisfies_protocol(self):
        idx = InMemoryDocIndex()
        assert isinstance(idx, DocIndex)

    def test_session_store_satisfies_protocol(self):
        store = InMemorySessionStore()
        assert isinstance(store, SessionStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/storage/test_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement storage protocols**

```python
# src/agentkit/storage/protocols.py
"""Storage Protocols — abstract interfaces for persistence.

Three protocols define the storage contract:
  - TapeStore: Persist and retrieve conversation tapes (JSONL entries)
  - DocIndex: Vector-searchable document index for memory/knowledge
  - SessionStore: Session metadata persistence (config, state snapshots)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TapeStore(Protocol):
    """Protocol for tape persistence."""

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None: ...
    async def load(self, tape_id: str) -> list[dict[str, Any]]: ...
    async def list_ids(self) -> list[str]: ...


@runtime_checkable
class DocIndex(Protocol):
    """Protocol for vector-searchable document storage."""

    async def upsert(self, doc_id: str, text: str, metadata: dict[str, Any]) -> None: ...
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    async def delete(self, doc_id: str) -> None: ...


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session metadata persistence."""

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None: ...
    async def load_session(self, session_id: str) -> dict[str, Any] | None: ...
    async def list_sessions(self) -> list[str]: ...
    async def delete_session(self, session_id: str) -> None: ...
```

- [ ] **Step 4: Run protocol tests**

Run: `uv run pytest tests/agentkit/storage/test_protocols.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Write failing tests for ForkTapeStore**

```python
# tests/agentkit/tape/test_store.py
import pytest
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class InMemoryTapeStore:
    def __init__(self):
        self._tapes = {}

    async def save(self, tape_id, entries):
        self._tapes[tape_id] = entries

    async def load(self, tape_id):
        return self._tapes.get(tape_id, [])

    async def list_ids(self):
        return list(self._tapes.keys())


class TestForkTapeStore:
    @pytest.fixture
    def backing_store(self):
        return InMemoryTapeStore()

    @pytest.fixture
    def fork_store(self, backing_store):
        return ForkTapeStore(backing_store)

    @pytest.mark.asyncio
    async def test_begin_creates_fork(self, fork_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        assert forked.parent_id == tape.tape_id
        assert len(forked) == 1

    @pytest.mark.asyncio
    async def test_fork_is_independent(self, fork_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "fork-addition"}))
        assert len(tape) == 1
        assert len(forked) == 2

    @pytest.mark.asyncio
    async def test_commit_persists(self, fork_store, backing_store):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"content": "original"}))
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="tool_call", payload={"name": "bash"}))
        await fork_store.commit(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert len(loaded) == 2

    @pytest.mark.asyncio
    async def test_rollback_discards(self, fork_store, backing_store):
        tape = Tape()
        forked = fork_store.begin(tape)
        forked.append(Entry(kind="message", payload={"content": "will be discarded"}))
        fork_store.rollback(forked)
        loaded = await backing_store.load(forked.tape_id)
        assert loaded == []

    @pytest.mark.asyncio
    async def test_commit_after_rollback_raises(self, fork_store):
        tape = Tape()
        forked = fork_store.begin(tape)
        fork_store.rollback(forked)
        with pytest.raises(ValueError, match="already finalized"):
            await fork_store.commit(forked)
```

- [ ] **Step 6: Implement ForkTapeStore**

```python
# src/agentkit/tape/store.py
"""ForkTapeStore — transactional tape operations.

Wraps a TapeStore to provide begin/commit/rollback semantics:
  - begin(tape) → creates a fork
  - commit(fork) → persists the fork to the backing store
  - rollback(fork) → discards the fork
"""

from __future__ import annotations

from agentkit.storage.protocols import TapeStore
from agentkit.tape.tape import Tape


class ForkTapeStore:
    """Transactional layer over a TapeStore.

    Usage:
        fork = store.begin(tape)
        fork.append(entry)
        await store.commit(fork)   # persists
        # OR
        store.rollback(fork)       # discards
    """

    def __init__(self, backing: TapeStore) -> None:
        self._backing = backing
        self._active: dict[str, Tape] = {}      # tape_id → forked Tape
        self._finalized: set[str] = set()        # tape_ids that were committed or rolled back

    def begin(self, tape: Tape) -> Tape:
        """Create a transactional fork of the given tape."""
        forked = tape.fork()
        self._active[forked.tape_id] = forked
        return forked

    async def commit(self, fork: Tape) -> None:
        """Persist the fork to the backing store."""
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        self._finalized.add(fork.tape_id)
        self._active.pop(fork.tape_id, None)
        await self._backing.save(fork.tape_id, fork.to_list())

    def rollback(self, fork: Tape) -> None:
        """Discard the fork without persisting."""
        if fork.tape_id in self._finalized:
            raise ValueError(f"tape '{fork.tape_id}' already finalized")
        self._finalized.add(fork.tape_id)
        self._active.pop(fork.tape_id, None)
```

- [ ] **Step 7: Update storage + tape __init__.py**

```python
# src/agentkit/storage/__init__.py
from agentkit.storage.protocols import DocIndex, SessionStore, TapeStore

__all__ = ["DocIndex", "SessionStore", "TapeStore"]
```

Update `src/agentkit/tape/__init__.py`:

```python
# src/agentkit/tape/__init__.py
from agentkit.tape.models import Entry
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape

__all__ = ["Entry", "ForkTapeStore", "Tape"]
```

- [ ] **Step 8: Run all storage and tape tests**

Run: `uv run pytest tests/agentkit/storage/ tests/agentkit/tape/ -v`
Expected: All tests PASS (3 protocol + 5 fork + 15 tape/entry = 23 total)

Note: Requires `pytest-asyncio`. Add to `pyproject.toml` dev dependencies if missing:
```toml
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "pytest-cov"]
```

- [ ] **Step 9: Commit**

```bash
git add src/agentkit/storage/ src/agentkit/tape/ tests/agentkit/storage/ tests/agentkit/tape/
git commit -m "feat(agentkit): add storage protocols (TapeStore, DocIndex, SessionStore) and ForkTapeStore"
```

---

### Task 6: HookRuntime

**Files:**
- Create: `src/agentkit/runtime/hook_runtime.py`
- Create: `src/agentkit/runtime/__init__.py`
- Test: `tests/agentkit/runtime/test_hook_runtime.py`

- [ ] **Step 1: Write failing tests for HookRuntime**

```python
# tests/agentkit/runtime/test_hook_runtime.py
import pytest
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.errors import HookError


class ProviderPlugin:
    state_key = "provider"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return {"model": "gpt-4"}


class FallbackProviderPlugin:
    state_key = "fallback"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return {"model": "claude"}


class ToolPluginA:
    state_key = "tools_a"

    def hooks(self):
        return {"get_tools": self.get_tools}

    def get_tools(self, **kwargs):
        return [{"name": "bash"}]


class ToolPluginB:
    state_key = "tools_b"

    def hooks(self):
        return {"get_tools": self.get_tools}

    def get_tools(self, **kwargs):
        return [{"name": "file_read"}]


class ErrorPlugin:
    state_key = "error"

    def hooks(self):
        return {"on_error": self.on_error}

    def on_error(self, **kwargs):
        raise RuntimeError("observer failure should be swallowed")


class BrokenPlugin:
    state_key = "broken"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        raise ValueError("hook crashed")


class NonePlugin:
    state_key = "none_provider"

    def hooks(self):
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs):
        return None  # Returns None — should be skipped by call_first


class TestHookRuntime:
    @pytest.fixture
    def registry(self):
        return PluginRegistry()

    @pytest.fixture
    def runtime(self, registry):
        return HookRuntime(registry)

    def test_call_first_returns_first_non_none(self, registry, runtime):
        registry.register(NonePlugin())
        registry.register(ProviderPlugin())
        registry.register(FallbackProviderPlugin())
        result = runtime.call_first("provide_llm")
        assert result == {"model": "gpt-4"}

    def test_call_first_returns_none_when_no_hooks(self, runtime):
        result = runtime.call_first("nonexistent_hook")
        assert result is None

    def test_call_first_skips_none_returns(self, registry, runtime):
        registry.register(NonePlugin())
        registry.register(ProviderPlugin())
        result = runtime.call_first("provide_llm")
        assert result == {"model": "gpt-4"}

    def test_call_many_collects_all(self, registry, runtime):
        registry.register(ToolPluginA())
        registry.register(ToolPluginB())
        results = runtime.call_many("get_tools")
        assert len(results) == 2
        names = [r[0]["name"] for r in results]
        assert "bash" in names
        assert "file_read" in names

    def test_call_many_empty(self, runtime):
        results = runtime.call_many("nonexistent")
        assert results == []

    def test_notify_swallows_errors(self, registry, runtime):
        registry.register(ErrorPlugin())
        # Should NOT raise — observer errors are swallowed
        runtime.notify("on_error", error="test")

    def test_call_first_propagates_errors(self, registry, runtime):
        registry.register(BrokenPlugin())
        with pytest.raises(HookError, match="hook crashed"):
            runtime.call_first("provide_llm")

    def test_call_first_passes_kwargs(self, registry, runtime):
        class KwargsPlugin:
            state_key = "kwargs"

            def hooks(self):
                return {"custom": self.custom}

            def custom(self, **kwargs):
                return kwargs.get("x", 0) + kwargs.get("y", 0)

        registry.register(KwargsPlugin())
        result = runtime.call_first("custom", x=3, y=4)
        assert result == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement HookRuntime**

```python
# src/agentkit/runtime/hook_runtime.py
"""HookRuntime — the core dispatch engine for plugin hooks.

Three dispatch modes:
  - call_first(hook, **kw): Returns first non-None result. Raises HookError on failure.
  - call_many(hook, **kw): Collects all non-None results. Raises HookError on failure.
  - notify(hook, **kw): Fire-and-forget observer. Swallows all exceptions (logs them).

This is a custom implementation (no pluggy) that natively supports:
  - Directive returns (checked by the caller, not by the runtime)
  - Plugin state_key tracking
  - Synchronous dispatch (async wrappers at pipeline level)
"""

from __future__ import annotations

import logging
from typing import Any

from agentkit.errors import HookError
from agentkit.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


class HookRuntime:
    """Dispatches hook calls to registered plugin callables.

    Args:
        registry: The PluginRegistry containing all registered plugins.
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def call_first(self, hook_name: str, **kwargs: Any) -> Any:
        """Call hooks in order, return the first non-None result.

        Returns None if no hooks are registered or all return None.
        Raises HookError if a hook raises an exception.
        """
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    return result
            except Exception as exc:
                raise HookError(
                    str(exc),
                    hook_name=hook_name,
                ) from exc
        return None

    def call_many(self, hook_name: str, **kwargs: Any) -> list[Any]:
        """Call all hooks, collect non-None results.

        Returns empty list if no hooks registered.
        Raises HookError if any hook raises an exception.
        """
        callables = self._registry.get_hooks(hook_name)
        results: list[Any] = []
        for fn in callables:
            try:
                result = fn(**kwargs)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                raise HookError(
                    str(exc),
                    hook_name=hook_name,
                ) from exc
        return results

    def notify(self, hook_name: str, **kwargs: Any) -> None:
        """Fire-and-forget: call all hooks, swallow exceptions.

        Used for observer hooks (on_error, on_checkpoint) where failures
        should not interrupt the main pipeline.
        """
        callables = self._registry.get_hooks(hook_name)
        for fn in callables:
            try:
                fn(**kwargs)
            except Exception:
                logger.exception(
                    "Observer hook '%s' raised (swallowed)", hook_name
                )
```

- [ ] **Step 4: Update runtime __init__.py**

```python
# src/agentkit/runtime/__init__.py
from agentkit.runtime.hook_runtime import HookRuntime

__all__ = ["HookRuntime"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/agentkit/runtime/test_hook_runtime.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/ tests/agentkit/runtime/
git commit -m "feat(agentkit): add HookRuntime with call_first, call_many, notify dispatch"
```

---

### Task 7: Hook Specs

**Files:**
- Create: `src/agentkit/runtime/hookspecs.py`
- Test: `tests/agentkit/runtime/test_hookspecs.py`

- [ ] **Step 1: Write failing tests for hookspecs**

```python
# tests/agentkit/runtime/test_hookspecs.py
import pytest
from agentkit.runtime.hookspecs import HOOK_SPECS, HookSpec


class TestHookSpecs:
    def test_all_11_hooks_defined(self):
        expected = {
            "provide_storage", "get_tools", "provide_llm",
            "approve_tool_call", "summarize_context", "on_error",
            "mount", "on_checkpoint", "build_context", "on_turn_end",
            "execute_tool",
        }
        assert set(HOOK_SPECS.keys()) == expected

    def test_hookspec_has_required_fields(self):
        for name, spec in HOOK_SPECS.items():
            assert isinstance(spec, HookSpec), f"{name} is not a HookSpec"
            assert isinstance(spec.name, str)
            assert isinstance(spec.firstresult, bool)
            assert isinstance(spec.is_observer, bool)
            assert isinstance(spec.returns_directive, bool)

    def test_provide_hooks_are_firstresult(self):
        assert HOOK_SPECS["provide_storage"].firstresult is True
        assert HOOK_SPECS["provide_llm"].firstresult is True

    def test_get_tools_is_not_firstresult(self):
        assert HOOK_SPECS["get_tools"].firstresult is False

    def test_on_error_is_observer(self):
        assert HOOK_SPECS["on_error"].is_observer is True

    def test_approve_tool_call_returns_directive(self):
        assert HOOK_SPECS["approve_tool_call"].returns_directive is True

    def test_on_turn_end_returns_directive(self):
        assert HOOK_SPECS["on_turn_end"].returns_directive is True

    def test_mount_returns_state(self):
        spec = HOOK_SPECS["mount"]
        assert spec.firstresult is False
        assert spec.is_observer is False

    def test_on_checkpoint_is_observer(self):
        assert HOOK_SPECS["on_checkpoint"].is_observer is True

    def test_build_context_is_not_firstresult(self):
        spec = HOOK_SPECS["build_context"]
        assert spec.firstresult is False
        assert spec.returns_directive is False

    def test_execute_tool_is_firstresult(self):
        spec = HOOK_SPECS["execute_tool"]
        assert spec.firstresult is True
        assert spec.is_observer is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/runtime/test_hookspecs.py -v`
Expected: FAIL

- [ ] **Step 3: Implement hookspecs**

```python
# src/agentkit/runtime/hookspecs.py
"""Hook specifications — metadata for the 10 agentkit hooks.

Each HookSpec declares:
  - name: the hook identifier
  - firstresult: if True, runtime uses call_first (stop at first non-None)
  - is_observer: if True, runtime uses notify (fire-and-forget, swallow errors)
  - returns_directive: if True, the return value is a Directive struct
  - doc: human-readable description
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HookSpec:
    """Metadata for a single hook."""

    name: str
    firstresult: bool = False
    is_observer: bool = False
    returns_directive: bool = False
    doc: str = ""


HOOK_SPECS: dict[str, HookSpec] = {
    "provide_storage": HookSpec(
        name="provide_storage",
        firstresult=True,
        doc="Return a TapeStore instance (with optional ForkTapeStore wrapping).",
    ),
    "get_tools": HookSpec(
        name="get_tools",
        firstresult=False,
        doc="Collect tool schemas from all plugins. call_many gathers lists.",
    ),
    "provide_llm": HookSpec(
        name="provide_llm",
        firstresult=True,
        doc="Return an LLMProvider instance for the current session.",
    ),
    "approve_tool_call": HookSpec(
        name="approve_tool_call",
        firstresult=True,
        returns_directive=True,
        doc="Return Approve/Reject/AskUser directive for a tool call.",
    ),
    "summarize_context": HookSpec(
        name="summarize_context",
        firstresult=True,
        doc="Compress tape entries when context window is exhausted.",
    ),
    "on_error": HookSpec(
        name="on_error",
        is_observer=True,
        doc="Observer: notified on pipeline errors. Cannot affect flow.",
    ),
    "mount": HookSpec(
        name="mount",
        firstresult=False,
        doc="Plugin initialization. Returns initial plugin state dict.",
    ),
    "on_checkpoint": HookSpec(
        name="on_checkpoint",
        is_observer=True,
        doc="Observer: notified at turn boundaries for state persistence.",
    ),
    "build_context": HookSpec(
        name="build_context",
        firstresult=False,
        doc="Inject grounding context (memories, KB results) before prompt build.",
    ),
    "on_turn_end": HookSpec(
        name="on_turn_end",
        firstresult=False,
        returns_directive=True,
        doc="finish_action: produce MemoryRecord directive at turn end.",
    ),
    "execute_tool": HookSpec(
        name="execute_tool",
        firstresult=True,
        doc="Execute a tool by name and return the result. Called by Pipeline.run_model.",
    ),
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agentkit/runtime/test_hookspecs.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/runtime/hookspecs.py tests/agentkit/runtime/test_hookspecs.py
git commit -m "feat(agentkit): add 10 hook specifications with metadata"
```

---

### Task 8: ContextBuilder

**Files:**
- Create: `src/agentkit/context/builder.py`
- Create: `src/agentkit/context/__init__.py`
- Test: `tests/agentkit/context/test_builder.py`

- [ ] **Step 1: Write failing tests for ContextBuilder**

```python
# tests/agentkit/context/test_builder.py
import pytest
from agentkit.context.builder import ContextBuilder
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class TestContextBuilder:
    def test_empty_tape_returns_system_only(self):
        tape = Tape()
        builder = ContextBuilder(system_prompt="You are a helpful agent.")
        messages = builder.build(tape)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful agent."

    def test_message_entries_become_messages(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "hi"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3  # system + user + assistant
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_tool_call_entries_become_assistant_tool_use(self):
        tape = Tape()
        tape.append(Entry(kind="tool_call", payload={
            "id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}
        }))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + tool_call
        assert messages[1]["role"] == "assistant"

    def test_tool_result_entries_become_tool_messages(self):
        tape = Tape()
        tape.append(Entry(kind="tool_result", payload={
            "tool_call_id": "tc_1", "content": "file1.py\nfile2.py"
        }))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert messages[1]["role"] == "tool"

    def test_grounding_injected_before_last_user_message(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "fix the bug"}))
        builder = ContextBuilder(system_prompt="system")
        grounding = [{"role": "system", "content": "[Memory] User prefers Python."}]
        messages = builder.build(tape, grounding=grounding)
        # system + grounding + user
        assert len(messages) == 3
        assert messages[1]["content"] == "[Memory] User prefers Python."
        assert messages[2]["content"] == "fix the bug"

    def test_anchor_entries_are_preserved(self):
        tape = Tape()
        tape.append(Entry(kind="anchor", payload={"content": "Important context"}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        # system + anchor-as-system + user
        assert len(messages) == 3

    def test_event_entries_are_skipped(self):
        tape = Tape()
        tape.append(Entry(kind="event", payload={"type": "metrics", "data": {}}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + user (event skipped)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/context/test_builder.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ContextBuilder**

```python
# src/agentkit/context/builder.py
"""ContextBuilder — assembles LLM messages from tape entries + grounding.

Converts tape entries into the messages format expected by LLM providers.
Handles grounding injection (memory context injected before the last user message).
"""

from __future__ import annotations

from typing import Any

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class ContextBuilder:
    """Builds LLM message lists from tape entries.

    Args:
        system_prompt: The system prompt to prepend to every message list.
    """

    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt

    def build(
        self,
        tape: Tape,
        grounding: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a message list from tape entries.

        Args:
            tape: The conversation tape.
            grounding: Optional grounding messages to inject before the last user message.

        Returns:
            List of message dicts with 'role' and 'content' keys.
        """
        messages: list[dict[str, Any]] = []

        # Convert tape entries to messages
        for entry in tape:
            msg = self._entry_to_message(entry)
            if msg is not None:
                messages.append(msg)

        # Inject grounding before last user message
        if grounding:
            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx is not None:
                for j, g in enumerate(grounding):
                    messages.insert(last_user_idx + j, g)
            else:
                messages.extend(grounding)

        # Prepend system prompt
        system = {"role": "system", "content": self._system_prompt}
        return [system] + messages

    def _entry_to_message(self, entry: Entry) -> dict[str, Any] | None:
        """Convert a single tape entry to a message dict."""
        if entry.kind == "message":
            return {
                "role": entry.payload.get("role", "user"),
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "tool_call":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": entry.payload.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": entry.payload.get("name", ""),
                        "arguments": entry.payload.get("arguments", {}),
                    },
                }],
            }
        elif entry.kind == "tool_result":
            return {
                "role": "tool",
                "tool_call_id": entry.payload.get("tool_call_id", ""),
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "anchor":
            return {
                "role": "system",
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "event":
            return None  # Events are metadata, not sent to LLM
        return None
```

- [ ] **Step 4: Update context __init__.py**

```python
# src/agentkit/context/__init__.py
from agentkit.context.builder import ContextBuilder

__all__ = ["ContextBuilder"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/agentkit/context/test_builder.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/context/ tests/agentkit/context/
git commit -m "feat(agentkit): add ContextBuilder with grounding injection"
```

---

### Task 9: @tool Decorator + ToolRegistry

**Files:**
- Create: `src/agentkit/tools/schema.py`
- Create: `src/agentkit/tools/decorator.py`
- Create: `src/agentkit/tools/registry.py`
- Create: `src/agentkit/tools/__init__.py`
- Test: `tests/agentkit/tools/test_schema.py`
- Test: `tests/agentkit/tools/test_decorator.py`
- Test: `tests/agentkit/tools/test_registry.py`

- [ ] **Step 1: Write failing tests for ToolSchema**

```python
# tests/agentkit/tools/test_schema.py
import pytest
from agentkit.tools.schema import ToolSchema


class TestToolSchema:
    def test_create_schema(self):
        schema = ToolSchema(
            name="file_read",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        assert schema.name == "file_read"
        assert schema.description == "Read a file"
        assert schema.parameters["required"] == ["path"]

    def test_to_openai_format(self):
        schema = ToolSchema(
            name="bash",
            description="Run a command",
            parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        oai = schema.to_openai_format()
        assert oai["type"] == "function"
        assert oai["function"]["name"] == "bash"
        assert oai["function"]["description"] == "Run a command"

    def test_schema_is_frozen(self):
        schema = ToolSchema(name="x", description="x", parameters={})
        with pytest.raises(AttributeError):
            schema.name = "y"
```

- [ ] **Step 2: Implement ToolSchema**

```python
# src/agentkit/tools/schema.py
"""ToolSchema — describes a tool's interface for LLM function calling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """Immutable tool description for LLM function calling.

    Attributes:
        name: Tool identifier (used in function calls).
        description: Human-readable description shown to LLM.
        parameters: JSON Schema for the tool's parameters.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
```

- [ ] **Step 3: Run schema tests**

Run: `uv run pytest tests/agentkit/tools/test_schema.py -v`
Expected: All 3 tests PASS

- [ ] **Step 4: Write failing tests for @tool decorator**

```python
# tests/agentkit/tools/test_decorator.py
import pytest
from agentkit.tools.decorator import tool


class TestToolDecorator:
    def test_basic_decoration(self):
        @tool
        def greet(name: str) -> str:
            """Say hello to someone."""
            return f"Hello, {name}!"

        assert hasattr(greet, "_tool_schema")
        assert greet._tool_schema.name == "greet"
        assert greet._tool_schema.description == "Say hello to someone."

    def test_custom_name(self):
        @tool(name="custom_greet")
        def greet(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        assert greet._tool_schema.name == "custom_greet"

    def test_custom_description(self):
        @tool(description="A custom description")
        def greet(name: str) -> str:
            """Original docstring."""
            return f"Hello, {name}!"

        assert greet._tool_schema.description == "A custom description"

    def test_function_still_callable(self):
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        assert add(2, 3) == 5

    def test_parameters_extracted_from_annotations(self):
        @tool
        def search(query: str, limit: int = 10) -> list:
            """Search for things."""
            return []

        params = search._tool_schema.parameters
        assert "query" in params["properties"]
        assert "limit" in params["properties"]
        assert "query" in params["required"]
        assert "limit" not in params["required"]  # has default

    def test_no_docstring_uses_empty_description(self):
        @tool
        def nodoc(x: int) -> int:
            return x

        assert nodoc._tool_schema.description == ""

    def test_async_function(self):
        @tool
        async def async_read(path: str) -> str:
            """Read a file async."""
            return "content"

        assert async_read._tool_schema.name == "async_read"
        assert async_read._tool_schema.description == "Read a file async."
```

- [ ] **Step 5: Implement @tool decorator**

```python
# src/agentkit/tools/decorator.py
"""@tool decorator — marks functions as agent tools and generates schemas.

Usage:
    @tool
    def file_read(path: str) -> str:
        '''Read file contents.'''
        ...

    @tool(name="bash_run", description="Execute a shell command")
    async def run_command(cmd: str, timeout: int = 30) -> str:
        ...

The decorator attaches a ToolSchema to the function as `_tool_schema`.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, TypeVar, overload

from agentkit.tools.schema import ToolSchema

F = TypeVar("F", bound=Callable[..., Any])

# Python type annotation → JSON Schema type mapping
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _extract_parameters(fn: Callable[..., Any]) -> dict[str, Any]:
    """Extract JSON Schema parameters from function signature + annotations."""
    sig = inspect.signature(fn)
    hints = {k: v for k, v in inspect.get_annotations(fn).items() if k != "return"}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        prop: dict[str, Any] = {}
        if name in hints:
            hint = hints[name]
            json_type = _TYPE_MAP.get(hint, "string")
            prop["type"] = json_type
        else:
            prop["type"] = "string"

        properties[name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


@overload
def tool(fn: F) -> F: ...
@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[F], F]: ...


def tool(
    fn: F | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> F | Callable[[F], F]:
    """Decorate a function as an agent tool.

    Can be used bare (@tool) or with arguments (@tool(name="x")).
    Attaches a ToolSchema as fn._tool_schema.
    """

    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or (func.__doc__ or "").strip()
        params = _extract_parameters(func)

        schema = ToolSchema(
            name=tool_name,
            description=tool_desc,
            parameters=params,
        )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        # For async functions, preserve async nature
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await func(*args, **kwargs)

            async_wrapper._tool_schema = schema  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        wrapper._tool_schema = schema  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator
```

- [ ] **Step 6: Run decorator tests**

Run: `uv run pytest tests/agentkit/tools/test_decorator.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Write failing tests for ToolRegistry**

```python
# tests/agentkit/tools/test_registry.py
import pytest
from agentkit.tools.registry import ToolRegistry
from agentkit.tools.decorator import tool
from agentkit.errors import ToolError


@tool
def fake_read(path: str) -> str:
    """Read a file."""
    return f"contents of {path}"


@tool
def fake_write(path: str, content: str) -> str:
    """Write a file."""
    return "ok"


class TestToolRegistry:
    def test_register_tool(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        assert "fake_read" in reg.names()

    def test_register_multiple(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        reg.register(fake_write)
        assert set(reg.names()) == {"fake_read", "fake_write"}

    def test_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        with pytest.raises(ToolError, match="already registered"):
            reg.register(fake_read)

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        fn = reg.get("fake_read")
        assert fn("test.py") == "contents of test.py"

    def test_get_missing_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ToolError, match="not found"):
            reg.get("nonexistent")

    def test_schemas(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        reg.register(fake_write)
        schemas = reg.schemas()
        assert len(schemas) == 2
        names = {s.name for s in schemas}
        assert names == {"fake_read", "fake_write"}

    def test_execute(self):
        reg = ToolRegistry()
        reg.register(fake_read)
        result = reg.execute("fake_read", path="/tmp/a.py")
        assert result == "contents of /tmp/a.py"

    @pytest.mark.asyncio
    async def test_execute_async(self):
        @tool
        async def async_tool(x: int) -> int:
            """Double."""
            return x * 2

        reg = ToolRegistry()
        reg.register(async_tool)
        result = await reg.execute_async("async_tool", x=5)
        assert result == 10

    def test_register_plain_function_raises(self):
        def no_decorator(x: int) -> int:
            return x

        reg = ToolRegistry()
        with pytest.raises(ToolError, match="missing @tool decorator"):
            reg.register(no_decorator)
```

- [ ] **Step 8: Implement ToolRegistry**

```python
# src/agentkit/tools/registry.py
"""ToolRegistry — central registry for agent tools.

Tools are registered by their decorated function. The registry provides:
  - Schema listing for LLM function calling
  - Sync and async tool execution by name
  - Duplicate detection
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from agentkit.errors import ToolError
from agentkit.tools.schema import ToolSchema


class ToolRegistry:
    """Registry for @tool-decorated functions."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._schemas: dict[str, ToolSchema] = {}

    def register(self, fn: Callable[..., Any]) -> None:
        """Register a @tool-decorated function."""
        schema: ToolSchema | None = getattr(fn, "_tool_schema", None)
        if schema is None:
            raise ToolError(
                f"'{getattr(fn, '__name__', fn)}' missing @tool decorator"
            )
        if schema.name in self._tools:
            raise ToolError(f"tool '{schema.name}' already registered")
        self._tools[schema.name] = fn
        self._schemas[schema.name] = schema

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def get(self, name: str) -> Callable[..., Any]:
        """Get a tool function by name. Raises ToolError if not found."""
        if name not in self._tools:
            raise ToolError(f"tool '{name}' not found")
        return self._tools[name]

    def schemas(self) -> list[ToolSchema]:
        """Return schemas for all registered tools."""
        return list(self._schemas.values())

    def execute(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool synchronously by name."""
        fn = self.get(name)
        return fn(**kwargs)

    async def execute_async(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool by name, awaiting if async."""
        fn = self.get(name)
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
```

- [ ] **Step 9: Update tools __init__.py**

```python
# src/agentkit/tools/__init__.py
from agentkit.tools.decorator import tool
from agentkit.tools.registry import ToolRegistry
from agentkit.tools.schema import ToolSchema

__all__ = ["ToolRegistry", "ToolSchema", "tool"]
```

- [ ] **Step 10: Run all tools tests**

Run: `uv run pytest tests/agentkit/tools/ -v`
Expected: All 19 tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/agentkit/tools/ tests/agentkit/tools/
git commit -m "feat(agentkit): add @tool decorator, ToolSchema, and ToolRegistry"
```

---

### Task 10: Pipeline

**Depends on:** T1, T2, T4, T6, T7, T14

**Files:**
- Create: `src/agentkit/runtime/pipeline.py`
- Modify: `src/agentkit/runtime/__init__.py`
- Test: `tests/agentkit/runtime/test_pipeline.py`

- [ ] **Step 1: Write failing tests for Pipeline**

```python
# tests/agentkit/runtime/test_pipeline.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.errors import PipelineError


class MinimalPlugin:
    """Plugin that implements just enough hooks for pipeline testing."""

    state_key = "minimal"

    def __init__(self):
        self.mounted = False
        self.mount_called = False

    def hooks(self):
        return {
            "mount": self.do_mount,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "summarize_context": self.summarize_context,
            "execute_tool": self.execute_tool,
        }

    def do_mount(self, **kwargs):
        self.mount_called = True
        return {"ready": True}

    def provide_llm(self, **kwargs):
        return MagicMock()  # Mock LLM provider

    def provide_storage(self, **kwargs):
        return MagicMock()  # Mock storage

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return None  # No summarization needed in tests

    def execute_tool(self, name: str = "", **kwargs):
        return f"executed:{name}"


class TestPipelineContext:
    def test_create_context(self):
        tape = Tape()
        ctx = PipelineContext(
            tape=tape,
            session_id="ses-1",
            config={"model": "gpt-4"},
        )
        assert ctx.tape is tape
        assert ctx.session_id == "ses-1"
        assert ctx.config["model"] == "gpt-4"
        assert ctx.plugin_states == {}

    def test_context_plugin_state_access(self):
        ctx = PipelineContext(tape=Tape(), session_id="x")
        ctx.plugin_states["memory"] = {"last_query": "test"}
        assert ctx.plugin_states["memory"]["last_query"] == "test"


class TestPipeline:
    @pytest.fixture
    def setup(self):
        registry = PluginRegistry()
        plugin = MinimalPlugin()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        return pipeline, plugin

    def test_pipeline_creates(self, setup):
        pipeline, _ = setup
        assert pipeline is not None

    @pytest.mark.asyncio
    async def test_mount_calls_plugins(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert plugin.mount_called

    @pytest.mark.asyncio
    async def test_mount_populates_plugin_states(self, setup):
        pipeline, _ = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert "minimal" in ctx.plugin_states

    def test_pipeline_stages_defined(self, setup):
        pipeline, _ = setup
        stages = pipeline.stage_names
        expected = [
            "resolve_session", "load_state", "build_context",
            "run_model", "save_state", "render", "dispatch",
        ]
        assert stages == expected

    @pytest.mark.asyncio
    async def test_run_single_turn(self, setup):
        """Test that pipeline can execute a single turn with a mocked LLM stream."""
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        # Mock the LLM provider to return a simple text response
        from agentkit.providers.models import TextEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        ctx.llm_provider.stream = mock_stream

        result = await pipeline.run_turn(ctx)
        assert result is not None
        # Verify assistant message was appended to tape
        last_entry = list(ctx.tape)[-1]
        assert last_entry.payload["role"] == "assistant"
        assert "Hello back!" in last_entry.payload["content"]

    @pytest.mark.asyncio
    async def test_run_turn_with_tool_call(self, setup):
        """Test the tool-calling loop: LLM emits tool call → approve → execute → respond."""
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "read file.txt"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: model asks for a tool
                yield ToolCallEvent(tool_call_id="tc1", name="file_read", arguments={"path": "file.txt"})
                yield DoneEvent()
            else:
                # Second call: model responds with text after seeing tool result
                yield TextEvent(text="File contents: test data")
                yield DoneEvent()

        ctx.llm_provider.stream = mock_stream

        result = await pipeline.run_turn(ctx)
        # Should have: user msg, tool_call, tool_result, assistant msg
        entries = list(ctx.tape)
        assert any(e.kind == "tool_call" for e in entries)
        assert any(e.kind == "tool_result" for e in entries)
        assert entries[-1].payload["role"] == "assistant"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agentkit/runtime/test_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Pipeline**

```python
# src/agentkit/runtime/pipeline.py
"""Pipeline — Bub-style linear stage runner for agent turns.

Stages: resolve_session → load_state → build_context → run_model → save_state → render → dispatch

Each stage is a method that takes PipelineContext and modifies it in place.
Stages delegate to hooks via HookRuntime. The pipeline itself contains zero
LLM logic — all behavior comes from plugins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agentkit._types import StageName
from agentkit.errors import PipelineError
from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Mutable context threaded through pipeline stages.

    Attributes:
        tape: The conversation tape for this session.
        session_id: Current session identifier.
        config: Agent configuration dict.
        plugin_states: Per-plugin state, keyed by state_key.
        messages: Built message list (populated by build_context stage).
        llm_provider: LLM provider instance (populated by resolve hooks).
        tool_schemas: Tool schemas for function calling.
        response_entries: New entries from model response (populated by run_model).
        output: Final rendered output for the channel.
    """

    tape: Tape
    session_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    plugin_states: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    llm_provider: Any = None
    storage: Any = None
    tool_schemas: list[Any] = field(default_factory=list)
    response_entries: list[Any] = field(default_factory=list)
    output: Any = None


class Pipeline:
    """Linear pipeline that runs one agent turn through 7 stages.

    Each stage calls the appropriate hooks. Plugins provide all behavior.
    """

    STAGES: list[StageName] = [
        "resolve_session",
        "load_state",
        "build_context",
        "run_model",
        "save_state",
        "render",
        "dispatch",
    ]

    def __init__(
        self,
        runtime: HookRuntime,
        registry: PluginRegistry,
        directive_executor: Any = None,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._directive_executor = directive_executor

    @property
    def stage_names(self) -> list[str]:
        """Return ordered stage names."""
        return list(self.STAGES)

    async def mount(self, ctx: PipelineContext) -> None:
        """Initialize all plugins via the mount hook.

        Iterates plugins directly (not via call_many) so each plugin's
        state can be mapped to its state_key without ambiguity.
        """
        for plugin_id in self._registry.plugin_ids():
            plugin = self._registry.get(plugin_id)
            mount_hook = plugin.hooks().get("mount")
            if mount_hook is not None:
                state = mount_hook()
                if state is not None:
                    ctx.plugin_states[plugin_id] = state

    async def run_turn(self, ctx: PipelineContext) -> PipelineContext:
        """Execute one full turn through all pipeline stages.

        Wraps the turn in a ForkTapeStore transaction (begin/commit/rollback)
        if the storage supports it. On success, commits; on failure, rolls back.
        """
        # Begin ForkTapeStore transaction if storage supports it
        fork = None
        if ctx.storage is not None and hasattr(ctx.storage, "begin"):
            fork = ctx.storage.begin(ctx.tape)

        try:
            for stage in self.STAGES:
                try:
                    handler = getattr(self, f"_stage_{stage}", None)
                    if handler is not None:
                        await handler(ctx)
                    else:
                        logger.debug("Stage '%s' has no handler, skipping", stage)
                except PipelineError:
                    raise
                except Exception as exc:
                    self._runtime.notify("on_error", stage=stage, error=exc)
                    raise PipelineError(str(exc), stage=stage) from exc

            # Commit transaction on success
            if fork is not None:
                ctx.storage.commit(fork)

            return ctx
        except Exception:
            # Rollback on any failure
            if fork is not None:
                ctx.storage.rollback(fork)
            raise

    async def _stage_resolve_session(self, ctx: PipelineContext) -> None:
        """Resolve session — currently a no-op, session_id already set."""
        pass

    async def _stage_load_state(self, ctx: PipelineContext) -> None:
        """Load state: get storage and LLM provider via hooks."""
        ctx.storage = self._runtime.call_first("provide_storage")
        ctx.llm_provider = self._runtime.call_first("provide_llm")

        # Collect tool schemas from all plugins
        tool_lists = self._runtime.call_many("get_tools")
        ctx.tool_schemas = []
        for tool_list in tool_lists:
            if isinstance(tool_list, list):
                ctx.tool_schemas.extend(tool_list)
            else:
                ctx.tool_schemas.append(tool_list)

    async def _stage_build_context(self, ctx: PipelineContext) -> None:
        """Build context: summarize if needed, collect grounding, assemble messages.

        Steps:
        1. Call summarize_context — if context window is exhausted, the
           SummarizerPlugin compresses old tape entries in place.
        2. Collect grounding from build_context hooks (memories, KB, etc.).
        3. Assemble messages via ContextBuilder.
        """
        # 1. Summarize if context window is exhausted
        summary = self._runtime.call_first("summarize_context", tape=ctx.tape)
        if summary is not None:
            logger.info("Context summarized: %d entries remaining", len(ctx.tape))

        # 2. Collect grounding context from plugins
        grounding_results = self._runtime.call_many("build_context", tape=ctx.tape)
        grounding: list[dict[str, Any]] = []
        for result in grounding_results:
            if isinstance(result, list):
                grounding.extend(result)

        # Import here to avoid circular — ContextBuilder is a sibling module
        from agentkit.context.builder import ContextBuilder

        system_prompt = ctx.config.get("system_prompt", "You are a helpful assistant.")
        builder = ContextBuilder(system_prompt=system_prompt)
        ctx.messages = builder.build(ctx.tape, grounding=grounding or None)

    async def _stage_run_model(self, ctx: PipelineContext) -> None:
        """Run model: call LLM and process response with tool-calling loop.

        This is the core agent turn loop:
        1. Stream LLM response → consume TextEvent / ToolCallEvent / DoneEvent
        2. If tool calls: approve each → execute → append result → rebuild context → loop
        3. If text only: append assistant message → done
        4. Safety: caps at max_tool_rounds (default 20) to prevent infinite loops.
        """
        if ctx.llm_provider is None:
            logger.warning("No LLM provider available, skipping run_model")
            return

        max_tool_rounds = ctx.config.get("max_tool_rounds", 20)

        for _round in range(max_tool_rounds):
            # Convert tool schemas for LLM
            tool_dicts = (
                [s.to_dict() for s in ctx.tool_schemas]
                if ctx.tool_schemas
                else None
            )

            # Stream LLM response
            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            async for event in ctx.llm_provider.stream(
                ctx.messages, tools=tool_dicts
            ):
                if isinstance(event, TextEvent):
                    text_chunks.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_calls.append({
                        "id": event.tool_call_id,
                        "name": event.name,
                        "arguments": event.arguments,
                    })
                elif isinstance(event, DoneEvent):
                    break

            # Text-only response — append and finish turn
            if text_chunks and not tool_calls:
                ctx.tape.append(Entry(
                    kind="message",
                    payload={"role": "assistant", "content": "".join(text_chunks)},
                ))
                break

            # Tool calls — approve, execute, loop
            if tool_calls:
                # Record tool_call entry
                ctx.tape.append(Entry(
                    kind="tool_call",
                    payload={"role": "assistant", "tool_calls": tool_calls},
                ))

                for tc in tool_calls:
                    # Approval check via hook → returns Directive
                    directive = self._runtime.call_first(
                        "approve_tool_call",
                        tool_name=tc["name"],
                        arguments=tc["arguments"],
                    )

                    # Execute directive (Approve → True, Reject → False)
                    approved = True
                    if directive is not None and self._directive_executor is not None:
                        approved = await self._directive_executor.execute(directive)

                    if not approved:
                        ctx.tape.append(Entry(
                            kind="tool_result",
                            payload={
                                "tool_call_id": tc["id"],
                                "content": f"Tool call rejected: {getattr(directive, 'reason', 'policy')}",
                            },
                        ))
                        continue

                    # Execute tool via hook
                    result = self._runtime.call_first(
                        "execute_tool",
                        name=tc["name"],
                        arguments=tc["arguments"],
                    )

                    ctx.tape.append(Entry(
                        kind="tool_result",
                        payload={
                            "tool_call_id": tc["id"],
                            "content": str(result) if result is not None else "",
                        },
                    ))

                # Rebuild context with tool results for next LLM round
                await self._stage_build_context(ctx)
                continue

            # Empty response (no text, no tool calls) — finish
            break

    async def _stage_save_state(self, ctx: PipelineContext) -> None:
        """Save state: persist tape and notify on_checkpoint."""
        self._runtime.notify("on_checkpoint", ctx=ctx)

    async def _stage_render(self, ctx: PipelineContext) -> None:
        """Render: call on_turn_end for finish_action directives and execute them.

        Collects directives (e.g. MemoryRecord) from on_turn_end hooks,
        then passes each to the DirectiveExecutor for side-effect execution.
        """
        directives = self._runtime.call_many("on_turn_end", tape=ctx.tape)
        ctx.output = {"directives": directives}

        # Execute each directive (e.g. persist MemoryRecord, handle Checkpoint)
        if self._directive_executor is not None:
            for directive in directives:
                if directive is not None:
                    await self._directive_executor.execute(directive)

    async def _stage_dispatch(self, ctx: PipelineContext) -> None:
        """Dispatch: send output to channel. No-op until Channel is connected."""
        pass
```

- [ ] **Step 4: Update runtime __init__.py**

```python
# src/agentkit/runtime/__init__.py
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.pipeline import Pipeline, PipelineContext

__all__ = ["HookRuntime", "Pipeline", "PipelineContext"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/agentkit/runtime/test_pipeline.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/ tests/agentkit/runtime/
git commit -m "feat(agentkit): add Pipeline with 7-stage linear execution"
```

---

### Task 11: Channel System

**Files:**
- Create: `src/agentkit/channel/protocol.py`
- Create: `src/agentkit/channel/local.py`
- Create: `src/agentkit/channel/__init__.py`
- Test: `tests/agentkit/channel/test_protocol.py`
- Test: `tests/agentkit/channel/test_local.py`

- [ ] **Step 1: Write failing tests for Channel protocol**

```python
# tests/agentkit/channel/test_protocol.py
import pytest
from agentkit.channel.protocol import Channel


class FakeChannel:
    def __init__(self):
        self._messages = []
        self._subscribers = []

    async def send(self, message: dict) -> None:
        self._messages.append(message)
        for sub in self._subscribers:
            await sub(message)

    async def receive(self) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    def subscribe(self, callback) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        self._subscribers.remove(callback)


class TestChannelProtocol:
    def test_fake_satisfies_protocol(self):
        ch = FakeChannel()
        assert isinstance(ch, Channel)

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        ch = FakeChannel()
        await ch.send({"type": "text", "content": "hello"})
        msg = await ch.receive()
        assert msg["content"] == "hello"

    @pytest.mark.asyncio
    async def test_receive_empty(self):
        ch = FakeChannel()
        msg = await ch.receive()
        assert msg is None
```

- [ ] **Step 2: Implement Channel protocol**

```python
# src/agentkit/channel/protocol.py
"""Channel Protocol — bidirectional communication between agent and consumer.

Replaces the Wire protocol from the original codebase with a more general
Channel abstraction that supports pub/sub patterns.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Protocol, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    """Protocol for agent ↔ consumer communication."""

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message to the channel."""
        ...

    async def receive(self) -> dict[str, Any] | None:
        """Receive the next message, or None if empty."""
        ...

    def subscribe(self, callback: Callable[..., Any]) -> None:
        """Register a callback for incoming messages."""
        ...

    def unsubscribe(self, callback: Callable[..., Any]) -> None:
        """Remove a callback."""
        ...
```

- [ ] **Step 3: Run protocol tests**

Run: `uv run pytest tests/agentkit/channel/test_protocol.py -v`
Expected: All 3 tests PASS

- [ ] **Step 4: Write failing tests for LocalChannel**

```python
# tests/agentkit/channel/test_local.py
import pytest
import asyncio
from agentkit.channel.local import LocalChannel


class TestLocalChannel:
    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        ch = LocalChannel()
        await ch.send({"type": "text", "content": "hello"})
        msg = await ch.receive()
        assert msg is not None
        assert msg["content"] == "hello"

    @pytest.mark.asyncio
    async def test_receive_empty_returns_none(self):
        ch = LocalChannel()
        msg = await ch.receive()
        assert msg is None

    @pytest.mark.asyncio
    async def test_fifo_order(self):
        ch = LocalChannel()
        await ch.send({"n": 1})
        await ch.send({"n": 2})
        await ch.send({"n": 3})
        assert (await ch.receive())["n"] == 1
        assert (await ch.receive())["n"] == 2
        assert (await ch.receive())["n"] == 3

    @pytest.mark.asyncio
    async def test_subscriber_called(self):
        ch = LocalChannel()
        received = []

        async def on_msg(msg):
            received.append(msg)

        ch.subscribe(on_msg)
        await ch.send({"content": "test"})
        assert len(received) == 1
        assert received[0]["content"] == "test"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        ch = LocalChannel()
        received = []

        async def on_msg(msg):
            received.append(msg)

        ch.subscribe(on_msg)
        ch.unsubscribe(on_msg)
        await ch.send({"content": "ignored"})
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        ch = LocalChannel()
        a, b = [], []

        async def sub_a(msg):
            a.append(msg)

        async def sub_b(msg):
            b.append(msg)

        ch.subscribe(sub_a)
        ch.subscribe(sub_b)
        await ch.send({"n": 1})
        assert len(a) == 1
        assert len(b) == 1
```

- [ ] **Step 5: Implement LocalChannel**

```python
# src/agentkit/channel/local.py
"""LocalChannel — in-process async channel for agent communication."""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from typing import Any, Callable


class LocalChannel:
    """In-process FIFO channel with pub/sub support.

    Messages are stored in a deque and delivered to subscribers on send().
    receive() pops from the queue (non-blocking, returns None if empty).
    """

    def __init__(self, maxlen: int | None = None) -> None:
        self._queue: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[Callable[..., Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message: enqueue and notify subscribers."""
        self._queue.append(message)
        for sub in list(self._subscribers):
            result = sub(message)
            if inspect.isawaitable(result):
                await result

    async def receive(self) -> dict[str, Any] | None:
        """Receive next message from queue, or None if empty."""
        if self._queue:
            return self._queue.popleft()
        return None

    def subscribe(self, callback: Callable[..., Any]) -> None:
        """Register a message callback."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[..., Any]) -> None:
        """Remove a message callback."""
        self._subscribers.remove(callback)
```

- [ ] **Step 6: Update channel __init__.py**

```python
# src/agentkit/channel/__init__.py
from agentkit.channel.local import LocalChannel
from agentkit.channel.protocol import Channel

__all__ = ["Channel", "LocalChannel"]
```

- [ ] **Step 7: Run all channel tests**

Run: `uv run pytest tests/agentkit/channel/ -v`
Expected: All 9 tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/agentkit/channel/ tests/agentkit/channel/
git commit -m "feat(agentkit): add Channel protocol and LocalChannel implementation"
```

---

### Task 12: Instruction Normalize

**Files:**
- Create: `src/agentkit/instruction/normalize.py`
- Create: `src/agentkit/instruction/__init__.py`
- Test: `tests/agentkit/instruction/test_normalize.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/agentkit/instruction/test_normalize.py
import pytest
from agentkit.instruction.normalize import normalize_instruction


class TestNormalizeInstruction:
    def test_string_becomes_user_message(self):
        result = normalize_instruction("hello")
        assert result == {"role": "user", "content": "hello"}

    def test_dict_with_role_passes_through(self):
        msg = {"role": "system", "content": "you are helpful"}
        result = normalize_instruction(msg)
        assert result == msg

    def test_dict_without_role_gets_user(self):
        msg = {"content": "do the thing"}
        result = normalize_instruction(msg)
        assert result == {"role": "user", "content": "do the thing"}

    def test_list_of_strings(self):
        result = normalize_instruction(["hello", "world"])
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hello"}
        assert result[1] == {"role": "user", "content": "world"}

    def test_list_of_dicts(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"content": "usr"},
        ]
        result = normalize_instruction(msgs)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_mixed_list(self):
        result = normalize_instruction(["text", {"role": "system", "content": "sys"}])
        assert result[0] == {"role": "user", "content": "text"}
        assert result[1] == {"role": "system", "content": "sys"}

    def test_none_raises(self):
        with pytest.raises(TypeError, match="cannot normalize"):
            normalize_instruction(None)

    def test_empty_string(self):
        result = normalize_instruction("")
        assert result == {"role": "user", "content": ""}

    def test_empty_list(self):
        result = normalize_instruction([])
        assert result == []
```

- [ ] **Step 2: Implement normalize_instruction**

```python
# src/agentkit/instruction/normalize.py
"""Instruction normalizer — flexible input, uniform output.

Converts various instruction formats into a standard message dict or list:
  - str → {"role": "user", "content": str}
  - dict with role → pass through
  - dict without role → add "user" role
  - list → normalize each element
"""

from __future__ import annotations

from typing import Any, overload


@overload
def normalize_instruction(instruction: str) -> dict[str, Any]: ...
@overload
def normalize_instruction(instruction: dict[str, Any]) -> dict[str, Any]: ...
@overload
def normalize_instruction(instruction: list[Any]) -> list[dict[str, Any]]: ...


def normalize_instruction(
    instruction: str | dict[str, Any] | list[Any],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Normalize an instruction to standard message format.

    Args:
        instruction: A string, dict, or list of strings/dicts.

    Returns:
        A message dict or list of message dicts.

    Raises:
        TypeError: If instruction type is not supported.
    """
    if isinstance(instruction, str):
        return {"role": "user", "content": instruction}
    elif isinstance(instruction, dict):
        if "role" not in instruction:
            return {"role": "user", **instruction}
        return instruction
    elif isinstance(instruction, list):
        return [normalize_instruction(item) for item in instruction]
    else:
        raise TypeError(f"cannot normalize instruction of type {type(instruction).__name__}")
```

- [ ] **Step 3: Update instruction __init__.py**

```python
# src/agentkit/instruction/__init__.py
from agentkit.instruction.normalize import normalize_instruction

__all__ = ["normalize_instruction"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agentkit/instruction/test_normalize.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/instruction/ tests/agentkit/instruction/
git commit -m "feat(agentkit): add instruction normalizer (str|dict|list → Message)"
```

---

### Task 13: TOML Config Loader + Bootstrap

**Files:**
- Create: `src/agentkit/config/loader.py`
- Create: `src/agentkit/config/__init__.py`
- Test: `tests/agentkit/config/test_loader.py`

- [ ] **Step 1: Write failing tests for config loader**

```python
# tests/agentkit/config/test_loader.py
import pytest
from pathlib import Path
from agentkit.config.loader import load_config, AgentConfig
from agentkit.errors import ConfigError


class TestAgentConfig:
    def test_config_fields(self):
        cfg = AgentConfig(
            name="my-agent",
            model="gpt-4",
            provider="openai",
            system_prompt="You are helpful.",
            plugins=["core_tools", "memory"],
            max_turns=30,
        )
        assert cfg.name == "my-agent"
        assert cfg.model == "gpt-4"
        assert cfg.plugins == ["core_tools", "memory"]

    def test_config_defaults(self):
        cfg = AgentConfig(name="test", model="gpt-4", provider="openai")
        assert cfg.system_prompt == ""
        assert cfg.plugins == []
        assert cfg.max_turns == 30


class TestLoadConfig:
    def test_load_valid_toml(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text('''
[agent]
name = "test-agent"
model = "claude-sonnet"
provider = "anthropic"
system_prompt = "You are a coding assistant."
max_turns = 50

[agent.plugins]
enabled = ["core_tools", "memory", "shell_session"]

[storage]
tape_backend = "jsonl"
doc_backend = "lancedb"

[storage.paths]
tapes = "./data/tapes"
docs = "./data/docs"
sessions = "./data/sessions"
''')
        cfg = load_config(toml_file)
        assert cfg.name == "test-agent"
        assert cfg.model == "claude-sonnet"
        assert cfg.provider == "anthropic"
        assert "core_tools" in cfg.plugins
        assert cfg.max_turns == 50
        assert cfg.extra["storage"]["tape_backend"] == "jsonl"

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.toml")

    def test_load_invalid_toml_raises(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="parse"):
            load_config(bad)

    def test_load_missing_agent_section_raises(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text('[storage]\nbackend = "jsonl"\n')
        with pytest.raises(ConfigError, match="\\[agent\\] section"):
            load_config(toml_file)

    def test_load_missing_required_field_raises(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text('[agent]\nname = "test"\n')
        with pytest.raises(ConfigError, match="model"):
            load_config(toml_file)
```

- [ ] **Step 2: Implement config loader**

```python
# src/agentkit/config/loader.py
"""TOML configuration loader for agentkit agents.

Loads agent.toml files and produces AgentConfig instances.
Required fields: name, model, provider.
Optional: system_prompt, plugins, max_turns, and arbitrary extra sections.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit.errors import ConfigError


@dataclass
class AgentConfig:
    """Parsed agent configuration.

    Attributes:
        name: Agent name.
        model: Model identifier (e.g., "gpt-4", "claude-sonnet").
        provider: Provider name (e.g., "openai", "anthropic").
        system_prompt: System prompt text.
        plugins: List of enabled plugin names.
        max_turns: Maximum turns per session.
        extra: Additional config sections (storage, etc.).
    """

    name: str
    model: str
    provider: str
    system_prompt: str = ""
    plugins: list[str] = field(default_factory=list)
    max_turns: int = 30
    extra: dict[str, Any] = field(default_factory=dict)


def load_config(path: Path) -> AgentConfig:
    """Load and validate an agent.toml configuration file.

    Args:
        path: Path to the TOML config file.

    Returns:
        Parsed AgentConfig.

    Raises:
        ConfigError: If file is missing, unparseable, or missing required fields.
    """
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc

    if "agent" not in data:
        raise ConfigError(f"missing [agent] section in {path}")

    agent = data["agent"]

    # Validate required fields
    for required in ("name", "model", "provider"):
        if required not in agent:
            raise ConfigError(f"missing required field '{required}' in [agent] section")

    # Extract plugins list
    plugins_section = agent.get("plugins", {})
    plugins = plugins_section.get("enabled", []) if isinstance(plugins_section, dict) else []

    # Collect extra sections (everything except [agent])
    extra = {k: v for k, v in data.items() if k != "agent"}

    return AgentConfig(
        name=agent["name"],
        model=agent["model"],
        provider=agent["provider"],
        system_prompt=agent.get("system_prompt", ""),
        plugins=plugins,
        max_turns=agent.get("max_turns", 30),
        extra=extra,
    )
```

- [ ] **Step 3: Update config __init__.py**

```python
# src/agentkit/config/__init__.py
from agentkit.config.loader import AgentConfig, load_config

__all__ = ["AgentConfig", "load_config"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agentkit/config/test_loader.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/config/ tests/agentkit/config/
git commit -m "feat(agentkit): add TOML config loader with AgentConfig model"
```

---

### Task 14: LLM Provider Protocol + StreamEvent Models

**Files:**
- Create: `src/agentkit/providers/protocol.py`
- Create: `src/agentkit/providers/models.py`
- Create: `src/agentkit/providers/__init__.py`
- Test: `tests/agentkit/providers/test_protocol.py` (create `tests/agentkit/providers/` dir in T1 skeleton step)

Note: Provider *implementations* (Anthropic, OpenAI) belong in the agent layer (Task 15). This task defines only the framework-level protocol and event models.

- [ ] **Step 1: Write failing tests for provider protocol and models**

```python
# tests/agentkit/providers/test_protocol.py (create dir tests/agentkit/providers/ first)
import pytest
from agentkit.providers.protocol import LLMProvider
from agentkit.providers.models import StreamEvent, ToolCallEvent, TextEvent, DoneEvent


class FakeLLM:
    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(self, messages, tools=None, **kwargs):
        yield TextEvent(text="Hello")
        yield DoneEvent()


class TestLLMProviderProtocol:
    def test_fake_satisfies_protocol(self):
        llm = FakeLLM()
        assert isinstance(llm, LLMProvider)

    def test_model_name(self):
        llm = FakeLLM()
        assert llm.model_name == "fake-model"

    def test_max_context_size(self):
        llm = FakeLLM()
        assert llm.max_context_size == 128000


class TestStreamEvents:
    def test_text_event(self):
        e = TextEvent(text="hello")
        assert e.kind == "text"
        assert e.text == "hello"

    def test_tool_call_event(self):
        e = ToolCallEvent(
            tool_call_id="tc_1",
            name="bash",
            arguments={"cmd": "ls"},
        )
        assert e.kind == "tool_call"
        assert e.name == "bash"

    def test_done_event(self):
        e = DoneEvent()
        assert e.kind == "done"

    def test_all_events_are_stream_events(self):
        for cls in (TextEvent, ToolCallEvent, DoneEvent):
            if cls is DoneEvent:
                e = cls()
            elif cls is TextEvent:
                e = cls(text="x")
            else:
                e = cls(tool_call_id="x", name="x", arguments={})
            assert isinstance(e, StreamEvent)
```

- [ ] **Step 2: Implement provider protocol and models**

```python
# src/agentkit/providers/models.py
"""Stream event models for LLM provider responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """Base class for LLM stream events."""
    kind: str = field(init=False)


@dataclass(frozen=True)
class TextEvent(StreamEvent):
    """A chunk of text from the LLM."""
    text: str = ""
    kind: str = field(init=False, default="text")


@dataclass(frozen=True)
class ToolCallEvent(StreamEvent):
    """The LLM wants to call a tool."""
    tool_call_id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    kind: str = field(init=False, default="tool_call")


@dataclass(frozen=True)
class DoneEvent(StreamEvent):
    """The LLM stream is complete."""
    kind: str = field(init=False, default="done")
```

```python
# src/agentkit/providers/protocol.py
"""LLMProvider Protocol — the contract for LLM backends."""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from agentkit.providers.models import StreamEvent


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM provider implementations.

    Implementations must provide:
      - stream(): Async iterator of StreamEvents
      - model_name: Current model identifier
      - max_context_size: Token limit
    """

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

- [ ] **Step 3: Update providers __init__.py**

```python
# src/agentkit/providers/__init__.py
from agentkit.providers.models import DoneEvent, StreamEvent, TextEvent, ToolCallEvent
from agentkit.providers.protocol import LLMProvider

__all__ = ["DoneEvent", "LLMProvider", "StreamEvent", "TextEvent", "ToolCallEvent"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agentkit/providers/ -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/providers/ tests/agentkit/providers/
git commit -m "feat(agentkit): add LLMProvider protocol and StreamEvent models"
```

---

## Agent Layer Tasks

> Tasks 15-23 implement the `coding_agent` layer on top of the `agentkit` framework.
> Each task creates a Plugin that wires domain-specific behavior into framework hooks.

---

### Task 15: LLMProviderPlugin (Anthropic + OpenAI)

**Files:**
- Create: `src/coding_agent/plugins/llm_provider.py`
- Create: `src/coding_agent/plugins/__init__.py`
- Create: `src/coding_agent/providers/anthropic.py`
- Create: `src/coding_agent/providers/openai_compat.py`
- Create: `src/coding_agent/providers/__init__.py`
- Test: `tests/coding_agent/plugins/test_llm_provider.py`

**Migrates from:** `src/coding_agent/providers/anthropic.py`, `src/coding_agent/providers/openai_compat.py`

- [ ] **Step 1: Write failing tests for LLMProviderPlugin**

```python
# tests/coding_agent/plugins/test_llm_provider.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from agentkit.providers.protocol import LLMProvider


class TestLLMProviderPlugin:
    def test_state_key(self):
        plugin = LLMProviderPlugin(provider="anthropic", model="claude-sonnet", api_key="sk-test")
        assert plugin.state_key == "llm_provider"

    def test_hooks_include_provide_llm(self):
        plugin = LLMProviderPlugin(provider="anthropic", model="claude-sonnet", api_key="sk-test")
        hooks = plugin.hooks()
        assert "provide_llm" in hooks

    def test_provide_llm_returns_provider_instance(self):
        plugin = LLMProviderPlugin(provider="anthropic", model="claude-sonnet", api_key="sk-test")
        result = plugin.provide_llm()
        assert isinstance(result, LLMProvider)

    def test_provide_llm_openai(self):
        plugin = LLMProviderPlugin(provider="openai", model="gpt-4", api_key="sk-test")
        result = plugin.provide_llm()
        assert isinstance(result, LLMProvider)
        assert result.model_name == "gpt-4"

    def test_unknown_provider_raises(self):
        plugin = LLMProviderPlugin(provider="unknown", model="x", api_key="sk-test")
        with pytest.raises(ValueError, match="unsupported provider"):
            plugin.provide_llm()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/coding_agent/plugins/test_llm_provider.py -v`
Expected: FAIL

- [ ] **Step 3: Implement LLMProviderPlugin**

Migrate the existing Anthropic and OpenAI provider implementations from `src/coding_agent/providers/` to implement the `LLMProvider` protocol from agentkit. The plugin wraps provider creation.

```python
# src/coding_agent/plugins/llm_provider.py
"""LLMProviderPlugin — provides LLM backend via provide_llm hook.

Supports Anthropic and OpenAI-compatible providers.
Config: provider name, model, API key.
"""

from __future__ import annotations

from typing import Any, Callable

from agentkit.providers.protocol import LLMProvider


class LLMProviderPlugin:
    """Plugin that provides an LLM provider instance.

    Args:
        provider: Provider name ("anthropic" or "openai").
        model: Model identifier.
        api_key: API key for the provider.
        base_url: Optional base URL override (for OpenAI-compatible APIs).
    """

    state_key = "llm_provider"

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        self._provider_name = provider
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._instance: LLMProvider | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs: Any) -> LLMProvider:
        """Create and cache the LLM provider instance."""
        if self._instance is not None:
            return self._instance

        if self._provider_name == "anthropic":
            from coding_agent.providers.anthropic import AnthropicProvider

            self._instance = AnthropicProvider(
                model=self._model,
                api_key=self._api_key,
            )
        elif self._provider_name in ("openai", "openai_compat"):
            from coding_agent.providers.openai_compat import OpenAICompatProvider

            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
            )
        else:
            raise ValueError(f"unsupported provider: {self._provider_name}")

        return self._instance
```

Note: The actual AnthropicProvider and OpenAICompatProvider classes are migrated from the existing codebase to implement the `LLMProvider` protocol. They emit `TextEvent`, `ToolCallEvent`, `DoneEvent` instead of the old `StreamEvent` format. The migration involves:
1. Copy existing `src/coding_agent/providers/anthropic.py` → new file
2. Replace `ChatProvider` protocol references with `LLMProvider` from agentkit
3. Replace old event types with `agentkit.providers.models.{TextEvent, ToolCallEvent, DoneEvent}`
4. Keep the core streaming logic intact — it already works

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/coding_agent/plugins/test_llm_provider.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/ src/coding_agent/providers/ tests/coding_agent/plugins/
git commit -m "feat(coding_agent): add LLMProviderPlugin with Anthropic and OpenAI support"
```

---

### Task 16: StoragePlugin

**Files:**
- Create: `src/coding_agent/plugins/storage.py`
- Create: `src/agentkit/storage/session.py`
- Test: `tests/coding_agent/plugins/test_storage.py`
- Test: `tests/agentkit/storage/test_session.py`

**Migrates from:** `src/coding_agent/core/session.py`

- [ ] **Step 1: Write failing tests for SessionStore default impl**

```python
# tests/agentkit/storage/test_session.py
import pytest
from pathlib import Path
from agentkit.storage.session import FileSessionStore
from agentkit.storage.protocols import SessionStore


class TestFileSessionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return FileSessionStore(base_dir=tmp_path)

    def test_satisfies_protocol(self, store):
        assert isinstance(store, SessionStore)

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        await store.save_session("ses-1", {"model": "gpt-4", "turns": 5})
        data = await store.load_session("ses-1")
        assert data is not None
        assert data["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self, store):
        data = await store.load_session("nonexistent")
        assert data is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        await store.save_session("a", {"x": 1})
        await store.save_session("b", {"x": 2})
        ids = await store.list_sessions()
        assert set(ids) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        await store.save_session("del-me", {"x": 1})
        await store.delete_session("del-me")
        assert await store.load_session("del-me") is None
```

- [ ] **Step 2: Implement FileSessionStore**

```python
# src/agentkit/storage/session.py
"""FileSessionStore — JSON file-based session persistence.

Each session is stored as a JSON file: {base_dir}/{session_id}.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FileSessionStore:
    """File-based SessionStore implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.json"

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.write_text(json.dumps(data, indent=2))

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    async def list_sessions(self) -> list[str]:
        return [p.stem for p in self._base_dir.glob("*.json")]

    async def delete_session(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
```

- [ ] **Step 3: Write failing tests for StoragePlugin**

```python
# tests/coding_agent/plugins/test_storage.py
import pytest
from pathlib import Path
from coding_agent.plugins.storage import StoragePlugin
from agentkit.storage.protocols import TapeStore, SessionStore
from agentkit.tape.store import ForkTapeStore


class TestStoragePlugin:
    def test_state_key(self):
        plugin = StoragePlugin(data_dir=Path("/tmp/test-data"))
        assert plugin.state_key == "storage"

    def test_hooks_include_provide_storage(self):
        plugin = StoragePlugin(data_dir=Path("/tmp/test-data"))
        hooks = plugin.hooks()
        assert "provide_storage" in hooks

    def test_provide_storage_returns_fork_tape_store(self, tmp_path):
        plugin = StoragePlugin(data_dir=tmp_path)
        result = plugin.provide_storage()
        assert isinstance(result, ForkTapeStore)

    def test_mount_returns_initial_state(self, tmp_path):
        plugin = StoragePlugin(data_dir=tmp_path)
        hooks = plugin.hooks()
        state = hooks["mount"]()
        assert "session_store" in state
        assert isinstance(state["session_store"], SessionStore)
```

- [ ] **Step 4: Implement StoragePlugin**

```python
# src/coding_agent/plugins/storage.py
"""StoragePlugin — provides tape storage and session management.

Implements provide_storage (returns ForkTapeStore) and mount (initializes session store).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentkit.storage.session import FileSessionStore
from agentkit.tape.store import ForkTapeStore


class JSONLTapeStore:
    """Simple JSONL-based TapeStore implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, tape_id: str, entries: list[dict]) -> None:
        import json

        path = self._base_dir / f"{tape_id}.jsonl"
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    async def load(self, tape_id: str) -> list[dict]:
        import json

        path = self._base_dir / f"{tape_id}.jsonl"
        if not path.exists():
            return []
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    async def list_ids(self) -> list[str]:
        return [p.stem for p in self._base_dir.glob("*.jsonl")]


class StoragePlugin:
    """Plugin providing storage backends."""

    state_key = "storage"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._fork_store: ForkTapeStore | None = None
        self._session_store: FileSessionStore | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "provide_storage": self.provide_storage,
            "mount": self.do_mount,
        }

    def provide_storage(self, **kwargs: Any) -> ForkTapeStore:
        if self._fork_store is None:
            backing = JSONLTapeStore(self._data_dir / "tapes")
            self._fork_store = ForkTapeStore(backing)
        return self._fork_store

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        if self._session_store is None:
            self._session_store = FileSessionStore(self._data_dir / "sessions")
        return {"session_store": self._session_store}
```

- [ ] **Step 5: Run all storage tests**

Run: `uv run pytest tests/agentkit/storage/ tests/coding_agent/plugins/test_storage.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/storage/ src/coding_agent/plugins/storage.py tests/agentkit/storage/ tests/coding_agent/plugins/test_storage.py
git commit -m "feat: add FileSessionStore, JSONLTapeStore, and StoragePlugin"
```

---

### Task 17: CoreToolsPlugin + Tool Implementations

**Files:**
- Create: `src/coding_agent/plugins/core_tools.py`
- Create: `src/coding_agent/tools/file_ops.py`
- Create: `src/coding_agent/tools/shell.py`
- Create: `src/coding_agent/tools/search.py`
- Create: `src/coding_agent/tools/planner.py`
- Create: `src/coding_agent/tools/__init__.py`
- Test: `tests/coding_agent/plugins/test_core_tools.py`
- Test: `tests/coding_agent/tools/test_file_ops.py`
- Test: `tests/coding_agent/tools/test_shell.py`

**Migrates from:** `src/coding_agent/tools/file.py`, `src/coding_agent/tools/shell.py`, `src/coding_agent/tools/search.py`, `src/coding_agent/tools/planner.py`

- [ ] **Step 1: Write failing tests for CoreToolsPlugin**

```python
# tests/coding_agent/plugins/test_core_tools.py
import pytest
from coding_agent.plugins.core_tools import CoreToolsPlugin
from agentkit.tools.schema import ToolSchema


class TestCoreToolsPlugin:
    def test_state_key(self):
        plugin = CoreToolsPlugin()
        assert plugin.state_key == "core_tools"

    def test_hooks_include_get_tools(self):
        plugin = CoreToolsPlugin()
        hooks = plugin.hooks()
        assert "get_tools" in hooks

    def test_hooks_include_execute_tool(self):
        plugin = CoreToolsPlugin()
        hooks = plugin.hooks()
        assert "execute_tool" in hooks

    def test_get_tools_returns_schemas(self):
        plugin = CoreToolsPlugin()
        schemas = plugin.get_tools()
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert all(isinstance(s, ToolSchema) for s in schemas)

    def test_execute_tool_runs_tool(self, tmp_path):
        """Test that execute_tool actually runs the tool with unpacked arguments."""
        plugin = CoreToolsPlugin()
        # Create a temp file to read
        f = tmp_path / "test.txt"
        f.write_text("test content")
        result = plugin.execute_tool(name="file_read", arguments={"path": str(f)})
        assert "test content" in result

    def test_includes_file_tools(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "file_read" in names
        assert "file_write" in names

    def test_includes_shell_tool(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "bash_run" in names

    def test_includes_search_tools(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "grep_search" in names
        assert "glob_files" in names
```

- [ ] **Step 2: Write failing tests for file_ops tools**

```python
# tests/coding_agent/tools/test_file_ops.py
import pytest
from coding_agent.tools.file_ops import file_read, file_write, file_replace


class TestFileOps:
    def test_file_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = file_read(path=str(f))
        assert "hello world" in result

    def test_file_read_missing(self, tmp_path):
        result = file_read(path=str(tmp_path / "missing.txt"))
        assert "error" in result.lower() or "not found" in result.lower()

    def test_file_write(self, tmp_path):
        f = tmp_path / "out.txt"
        result = file_write(path=str(f), content="written")
        assert f.read_text() == "written"

    def test_file_replace(self, tmp_path):
        f = tmp_path / "repl.txt"
        f.write_text("old text here")
        result = file_replace(path=str(f), old="old", new="new")
        assert f.read_text() == "new text here"
```

- [ ] **Step 3: Implement tool functions with @tool decorator**

Migrate existing tool implementations from `src/coding_agent/tools/file.py`, `shell.py`, `search.py` to use the `@tool` decorator. The core logic remains the same — only the registration mechanism changes.

```python
# src/coding_agent/tools/file_ops.py
"""File operation tools — read, write, replace, glob, grep."""

from __future__ import annotations

import os
from pathlib import Path

from agentkit.tools import tool


@tool(description="Read file contents. Returns file text or error message.")
def file_read(path: str) -> str:
    """Read file contents."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: file not found: {path}"
        return p.read_text()
    except Exception as e:
        return f"Error reading {path}: {e}"


@tool(description="Write content to a file. Creates parent directories if needed.")
def file_write(path: str, content: str) -> str:
    """Write content to file."""
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


@tool(description="Replace exact string in a file.")
def file_replace(path: str, old: str, new: str) -> str:
    """Replace text in file."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: file not found: {path}"
        content = p.read_text()
        if old not in content:
            return f"Error: '{old}' not found in {path}"
        updated = content.replace(old, new, 1)
        p.write_text(updated)
        return f"Replaced in {path}"
    except Exception as e:
        return f"Error: {e}"


@tool(description="Search for files matching a glob pattern.")
def glob_files(pattern: str, directory: str = ".") -> str:
    """Find files matching glob pattern."""
    try:
        base = Path(directory).expanduser().resolve()
        matches = sorted(str(p) for p in base.glob(pattern))
        if not matches:
            return "No files matched."
        return "\n".join(matches[:100])
    except Exception as e:
        return f"Error: {e}"


@tool(description="Search file contents for a regex pattern.")
def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
    """Search files for pattern."""
    import re
    import subprocess

    try:
        cmd = ["grep", "-rn", pattern, directory]
        if include:
            cmd.extend(["--include", include])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout.strip()
        if not output:
            return "No matches found."
        lines = output.split("\n")
        if len(lines) > 50:
            return "\n".join(lines[:50]) + f"\n... ({len(lines)} total matches)"
        return output
    except Exception as e:
        return f"Error: {e}"
```

```python
# src/coding_agent/tools/shell.py
"""Shell execution tool."""

from __future__ import annotations

import subprocess

from agentkit.tools import tool


@tool(description="Run a shell command and return stdout/stderr.")
def bash_run(command: str, timeout: int = 120) -> str:
    """Execute a bash command."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
```

```python
# src/coding_agent/tools/planner.py
"""Planner tools — todo management."""

from __future__ import annotations

import json

from agentkit.tools import tool

# In-memory todo storage (will be persisted via on_checkpoint)
_todos: list[dict] = []


@tool(description="Write/update the todo list. Replaces the entire list.")
def todo_write(todos: str) -> str:
    """Update the todo list. Pass JSON array of {content, status, priority}."""
    global _todos
    try:
        _todos = json.loads(todos)
        return f"Updated {len(_todos)} todos"
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"


@tool(description="Read the current todo list.")
def todo_read() -> str:
    """Read current todos."""
    if not _todos:
        return "No todos."
    return json.dumps(_todos, indent=2)
```

- [ ] **Step 4: Implement CoreToolsPlugin**

```python
# src/coding_agent/plugins/core_tools.py
"""CoreToolsPlugin — registers file, shell, search, planner tools.

Implements get_tools hook to provide tool schemas to the framework.
"""

from __future__ import annotations

from typing import Any, Callable

from agentkit.tools import ToolRegistry, ToolSchema


class CoreToolsPlugin:
    """Plugin that provides core agent tools."""

    state_key = "core_tools"

    def __init__(self) -> None:
        self._registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        from coding_agent.tools.file_ops import (
            file_read,
            file_replace,
            file_write,
            glob_files,
            grep_search,
        )
        from coding_agent.tools.shell import bash_run
        from coding_agent.tools.planner import todo_read, todo_write

        for fn in (file_read, file_write, file_replace, glob_files,
                   grep_search, bash_run, todo_write, todo_read):
            self._registry.register(fn)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        """Return schemas for all registered tools."""
        return self._registry.schemas()

    def execute_tool(self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Execute a tool by name. Called by Pipeline via execute_tool hook.

        The arguments dict is unpacked as keyword args to the tool function.
        E.g. arguments={"path": "file.txt"} → file_read(path="file.txt")
        """
        args = arguments or {}
        return self._registry.execute(name, **args)

    async def execute_tool_async(self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Execute a tool by name (async version)."""
        args = arguments or {}
        return await self._registry.execute_async(name, **args)
```

- [ ] **Step 5: Run all tools tests**

Run: `uv run pytest tests/coding_agent/plugins/test_core_tools.py tests/coding_agent/tools/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/plugins/core_tools.py src/coding_agent/tools/ tests/coding_agent/
git commit -m "feat(coding_agent): add CoreToolsPlugin with file, shell, search, planner tools"
```

---

### Task 18: ApprovalPlugin

**Files:**
- Create: `src/coding_agent/plugins/approval.py`
- Test: `tests/coding_agent/plugins/test_approval.py`

**Migrates from:** `src/coding_agent/approval/policy.py`, `src/coding_agent/approval/store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coding_agent/plugins/test_approval.py
import pytest
from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy
from agentkit.directive.types import Approve, Reject, AskUser


class TestApprovalPlugin:
    def test_state_key(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        assert plugin.state_key == "approval"

    def test_hooks_include_approve_tool_call(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        hooks = plugin.hooks()
        assert "approve_tool_call" in hooks

    def test_auto_policy_approves_all(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        result = plugin.approve_tool_call(tool_name="bash_run", arguments={"cmd": "ls"})
        assert isinstance(result, Approve)

    def test_manual_policy_asks_user(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.MANUAL)
        result = plugin.approve_tool_call(tool_name="bash_run", arguments={"cmd": "rm -rf /"})
        assert isinstance(result, AskUser)

    def test_safe_only_approves_safe_tools(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.SAFE_ONLY,
            safe_tools={"file_read", "grep_search"},
        )
        assert isinstance(
            plugin.approve_tool_call(tool_name="file_read", arguments={}),
            Approve,
        )
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            AskUser,
        )

    def test_blocklist_rejects(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.AUTO,
            blocked_tools={"dangerous_tool"},
        )
        result = plugin.approve_tool_call(tool_name="dangerous_tool", arguments={})
        assert isinstance(result, Reject)
```

- [ ] **Step 2: Implement ApprovalPlugin**

```python
# src/coding_agent/plugins/approval.py
"""ApprovalPlugin — tool call approval via Directive pattern.

Returns Approve/Reject/AskUser directives based on configured policy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from agentkit.directive.types import Approve, AskUser, Reject


class ApprovalPolicy(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    SAFE_ONLY = "safe_only"


class ApprovalPlugin:
    """Plugin implementing approve_tool_call hook."""

    state_key = "approval"

    def __init__(
        self,
        policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        safe_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
    ) -> None:
        self._policy = policy
        self._safe_tools = safe_tools or set()
        self._blocked_tools = blocked_tools or set()

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"approve_tool_call": self.approve_tool_call}

    def approve_tool_call(
        self, tool_name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any,
    ) -> Approve | Reject | AskUser:
        """Evaluate a tool call and return an approval directive."""
        # Always reject blocked tools
        if tool_name in self._blocked_tools:
            return Reject(reason=f"tool '{tool_name}' is blocked")

        if self._policy == ApprovalPolicy.AUTO:
            return Approve()
        elif self._policy == ApprovalPolicy.MANUAL:
            return AskUser(question=f"Allow tool '{tool_name}' with args {arguments}?")
        elif self._policy == ApprovalPolicy.SAFE_ONLY:
            if tool_name in self._safe_tools:
                return Approve()
            return AskUser(question=f"Tool '{tool_name}' requires approval. Allow?")

        return Approve()
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/coding_agent/plugins/test_approval.py -v`
Expected: All 6 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/plugins/approval.py tests/coding_agent/plugins/test_approval.py
git commit -m "feat(coding_agent): add ApprovalPlugin with Directive-based tool approval"
```

---

### Task 19: SummarizerPlugin

**Files:**
- Create: `src/coding_agent/plugins/summarizer.py`
- Test: `tests/coding_agent/plugins/test_summarizer.py`

**Migrates from:** `src/coding_agent/summarizer/`

- [ ] **Step 1: Write failing tests**

```python
# tests/coding_agent/plugins/test_summarizer.py
import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestSummarizerPlugin:
    def test_state_key(self):
        plugin = SummarizerPlugin(max_entries=10)
        assert plugin.state_key == "summarizer"

    def test_hooks_include_summarize_context(self):
        plugin = SummarizerPlugin(max_entries=10)
        hooks = plugin.hooks()
        assert "summarize_context" in hooks

    def test_short_tape_unchanged(self):
        plugin = SummarizerPlugin(max_entries=100)
        tape = Tape()
        for i in range(5):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"msg {i}"}))
        result = plugin.summarize_context(tape=tape)
        assert result is None  # No summarization needed

    def test_long_tape_gets_summarized(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        for i in range(20):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"message number {i}"}))
        result = plugin.summarize_context(tape=tape)
        assert result is not None
        assert len(result) < 20  # Summarized tape has fewer entries

    def test_preserves_recent_entries(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        for i in range(20):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"}))
        result = plugin.summarize_context(tape=tape)
        if result is not None:
            # Last entries should be the most recent ones
            last_contents = [e.payload["content"] for e in result[-3:]]
            assert "msg-19" in last_contents
```

- [ ] **Step 2: Implement SummarizerPlugin**

```python
# src/coding_agent/plugins/summarizer.py
"""SummarizerPlugin — context window management via rule-based summarization.

When tape exceeds max_entries, older entries are compressed into a summary anchor.
Recent entries (keep_recent) are always preserved verbatim.

Note: LLM-based summarization can be added later as an enhancement.
For V1, we use rule-based truncation with anchor insertion.
"""

from __future__ import annotations

from typing import Any, Callable

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class SummarizerPlugin:
    """Plugin implementing summarize_context hook."""

    state_key = "summarizer"

    def __init__(
        self,
        max_entries: int = 100,
        keep_recent: int = 20,
    ) -> None:
        self._max_entries = max_entries
        self._keep_recent = keep_recent

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"summarize_context": self.summarize_context}

    def summarize_context(self, tape: Tape | None = None, **kwargs: Any) -> list[Entry] | None:
        """Summarize tape if it exceeds max_entries.

        Returns a new entry list with old entries compressed into an anchor,
        or None if no summarization is needed.
        """
        if tape is None:
            return None

        entries = list(tape)
        if len(entries) <= self._max_entries:
            return None

        # Split into old (to summarize) and recent (to keep)
        split_point = len(entries) - self._keep_recent
        old_entries = entries[:split_point]
        recent_entries = entries[split_point:]

        # Create summary of old entries
        summary_parts = []
        for entry in old_entries:
            if entry.kind == "message":
                role = entry.payload.get("role", "?")
                content = entry.payload.get("content", "")
                preview = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"[{role}] {preview}")
            elif entry.kind == "tool_call":
                name = entry.payload.get("name", "?")
                summary_parts.append(f"[tool_call] {name}")
            elif entry.kind == "tool_result":
                summary_parts.append("[tool_result] ...")

        summary_text = f"[Summarized {len(old_entries)} earlier entries]\n" + "\n".join(summary_parts[-10:])

        anchor = Entry(
            kind="anchor",
            payload={"content": summary_text},
        )

        return [anchor] + recent_entries
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/coding_agent/plugins/test_summarizer.py -v`
Expected: All 5 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/plugins/summarizer.py tests/coding_agent/plugins/test_summarizer.py
git commit -m "feat(coding_agent): add SummarizerPlugin with rule-based context compression"
```

---

### Task 20: MemoryPlugin (Grounding + finish_action)

**Files:**
- Create: `src/coding_agent/plugins/memory.py`
- Test: `tests/coding_agent/plugins/test_memory.py`

**Innovates on:** Kapybara Grounding+Getter dual mode, Nowledge decay/confidence, finish_action

- [ ] **Step 1: Write failing tests**

```python
# tests/coding_agent/plugins/test_memory.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from coding_agent.plugins.memory import MemoryPlugin
from agentkit.directive.types import MemoryRecord
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestMemoryPlugin:
    def test_state_key(self):
        plugin = MemoryPlugin()
        assert plugin.state_key == "memory"

    def test_hooks(self):
        plugin = MemoryPlugin()
        hooks = plugin.hooks()
        assert "build_context" in hooks   # Grounding mode
        assert "on_turn_end" in hooks     # finish_action
        assert "mount" in hooks

    def test_mount_returns_initial_state(self):
        plugin = MemoryPlugin()
        state = plugin.do_mount()
        assert "memories" in state
        assert isinstance(state["memories"], list)

    def test_build_context_returns_grounding_messages(self):
        plugin = MemoryPlugin()
        # Simulate having some memories
        plugin._memories = [
            {"summary": "User prefers Python", "importance": 0.9},
            {"summary": "Project uses pytest", "importance": 0.7},
        ]
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "help me debug"}))
        result = plugin.build_context(tape=tape)
        assert isinstance(result, list)
        assert len(result) > 0
        # Grounding messages should be system role
        assert all(msg["role"] == "system" for msg in result)

    def test_on_turn_end_returns_memory_record_directive(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth.py"}))
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "I fixed the bug in auth.py"}))
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.summary != ""

    def test_on_turn_end_with_empty_tape(self):
        plugin = MemoryPlugin()
        tape = Tape()
        result = plugin.on_turn_end(tape=tape)
        # With empty tape, should return a minimal record or None
        assert result is None or isinstance(result, MemoryRecord)

    def test_memory_importance_scoring(self):
        plugin = MemoryPlugin()
        # Simple heuristic: longer conversations = more important
        tape = Tape()
        for i in range(10):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"step {i}"}))
            tape.append(Entry(kind="tool_call", payload={"name": "bash_run"}))
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.importance > 0.3  # Multi-step should score higher
```

- [ ] **Step 2: Implement MemoryPlugin**

```python
# src/coding_agent/plugins/memory.py
"""MemoryPlugin — Grounding + finish_action memory management.

Two modes:
  - Grounding (build_context): Automatically injects relevant memories
    as system messages before each turn.
  - finish_action (on_turn_end): Forces structured MemoryRecord production
    at the end of every turn for persistent learning.

Innovation over Bub: Two-layer memory (near-term compacted + long-term raw),
importance scoring, tag extraction.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from agentkit.directive.types import MemoryRecord
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class MemoryPlugin:
    """Plugin implementing memory management via grounding + finish_action."""

    state_key = "memory"

    def __init__(self, max_grounding: int = 5) -> None:
        self._max_grounding = max_grounding
        self._memories: list[dict[str, Any]] = []

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "build_context": self.build_context,
            "on_turn_end": self.on_turn_end,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialize memory state."""
        return {"memories": self._memories}

    def build_context(self, tape: Tape | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        """Grounding mode: inject relevant memories as system messages."""
        if not self._memories:
            return []

        # Sort by importance, take top N
        sorted_memories = sorted(
            self._memories, key=lambda m: m.get("importance", 0.5), reverse=True
        )
        top = sorted_memories[: self._max_grounding]

        grounding_messages = []
        for mem in top:
            content = f"[Memory] {mem['summary']}"
            if mem.get("tags"):
                content += f" (tags: {', '.join(mem['tags'])})"
            grounding_messages.append({"role": "system", "content": content})

        return grounding_messages

    def on_turn_end(self, tape: Tape | None = None, **kwargs: Any) -> MemoryRecord | None:
        """finish_action: extract a structured memory from the turn.

        Analyzes the tape to produce a MemoryRecord with:
          - summary: What happened in this turn
          - tags: Extracted topic tags
          - importance: Heuristic score (0-1)
        """
        if tape is None or len(tape) == 0:
            return None

        entries = list(tape)
        if len(entries) < 2:
            return None

        # Extract summary from last assistant message
        last_assistant = None
        for entry in reversed(entries):
            if entry.kind == "message" and entry.payload.get("role") == "assistant":
                last_assistant = entry.payload.get("content", "")
                break

        if not last_assistant:
            return None

        # Create summary (truncate if too long)
        summary = last_assistant[:200]
        if len(last_assistant) > 200:
            summary += "..."

        # Extract tags from content
        tags = self._extract_tags(entries)

        # Score importance based on turn complexity
        importance = self._score_importance(entries)

        record = MemoryRecord(
            summary=summary,
            tags=tags,
            importance=importance,
        )

        # Store for future grounding
        self._memories.append({
            "summary": record.summary,
            "tags": record.tags,
            "importance": record.importance,
        })

        return record

    def _extract_tags(self, entries: list[Entry]) -> list[str]:
        """Extract topic tags from tape entries."""
        tags: set[str] = set()
        for entry in entries:
            if entry.kind == "tool_call":
                name = entry.payload.get("name", "")
                if name:
                    tags.add(name)
            elif entry.kind == "message":
                content = entry.payload.get("content", "")
                # Simple heuristic: extract file paths as tags
                paths = re.findall(r'[\w/]+\.\w+', content)
                for p in paths[:3]:
                    tags.add(p)
        return sorted(tags)[:5]

    def _score_importance(self, entries: list[Entry]) -> float:
        """Score turn importance (0-1) based on complexity heuristics."""
        tool_calls = sum(1 for e in entries if e.kind == "tool_call")
        messages = sum(1 for e in entries if e.kind == "message")

        # More tool calls = more complex = more important
        tool_score = min(tool_calls / 10.0, 0.5)
        # More messages = longer conversation = somewhat important
        msg_score = min(messages / 20.0, 0.3)
        # Base importance
        base = 0.2

        return min(base + tool_score + msg_score, 1.0)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/coding_agent/plugins/test_memory.py -v`
Expected: All 7 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/plugins/memory.py tests/coding_agent/plugins/test_memory.py
git commit -m "feat(coding_agent): add MemoryPlugin with grounding injection and finish_action"
```

---

### Task 21: ShellSessionPlugin (Persistent Bash)

**Files:**
- Create: `src/coding_agent/plugins/shell_session.py`
- Test: `tests/coding_agent/plugins/test_shell_session.py`

**Innovates from:** Kapybara persistent shell session pattern

- [ ] **Step 1: Write failing tests**

```python
# tests/coding_agent/plugins/test_shell_session.py
import pytest
from coding_agent.plugins.shell_session import ShellSessionPlugin
from agentkit.directive.types import Checkpoint


class TestShellSessionPlugin:
    def test_state_key(self):
        plugin = ShellSessionPlugin()
        assert plugin.state_key == "shell_session"

    def test_hooks(self):
        plugin = ShellSessionPlugin()
        hooks = plugin.hooks()
        assert "mount" in hooks
        assert "on_checkpoint" in hooks

    def test_mount_initializes_session_state(self):
        plugin = ShellSessionPlugin()
        state = plugin.do_mount()
        assert "cwd" in state
        assert "env_vars" in state
        assert "active" in state

    def test_checkpoint_captures_cwd(self):
        plugin = ShellSessionPlugin()
        plugin._state = {"cwd": "/home/user/project", "env_vars": {"PATH": "/usr/bin"}, "active": True}
        plugin.on_checkpoint()
        # on_checkpoint is observer — just logs, doesn't return
        # The state should be available for persistence

    def test_get_session_context(self):
        plugin = ShellSessionPlugin()
        plugin._state = {"cwd": "/tmp", "env_vars": {}, "active": True}
        ctx = plugin.get_session_context()
        assert ctx["cwd"] == "/tmp"

    def test_update_cwd(self):
        plugin = ShellSessionPlugin()
        plugin._state = {"cwd": "/home", "env_vars": {}, "active": True}
        plugin.update_cwd("/home/user")
        assert plugin._state["cwd"] == "/home/user"
```

- [ ] **Step 2: Implement ShellSessionPlugin**

```python
# src/coding_agent/plugins/shell_session.py
"""ShellSessionPlugin — persistent shell session management.

Tracks working directory and environment variables across tool calls,
enabling persistent shell sessions (Kapybara pattern).

The plugin:
  - mount: Initializes session with current directory
  - on_checkpoint: Logs session state for persistence
  - Exposes get_session_context() for shell tools to use
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ShellSessionPlugin:
    """Plugin for persistent shell session state."""

    state_key = "shell_session"

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "on_checkpoint": self.on_checkpoint,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialize shell session state."""
        self._state = {
            "cwd": os.getcwd(),
            "env_vars": {},
            "active": True,
        }
        return dict(self._state)

    def on_checkpoint(self, **kwargs: Any) -> None:
        """Observer: log session state for persistence."""
        logger.debug(
            "Shell session checkpoint: cwd=%s, env_count=%d",
            self._state.get("cwd", "?"),
            len(self._state.get("env_vars", {})),
        )

    def get_session_context(self) -> dict[str, Any]:
        """Get current session context for shell tools."""
        return dict(self._state)

    def update_cwd(self, new_cwd: str) -> None:
        """Update the tracked working directory."""
        self._state["cwd"] = new_cwd

    def update_env(self, key: str, value: str) -> None:
        """Track an environment variable change."""
        self._state.setdefault("env_vars", {})[key] = value
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/coding_agent/plugins/test_shell_session.py -v`
Expected: All 6 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/plugins/shell_session.py tests/coding_agent/plugins/test_shell_session.py
git commit -m "feat(coding_agent): add ShellSessionPlugin for persistent bash sessions"
```

---

### Task 22: DirectiveExecutor Integration

**Files:**
- Create: `src/agentkit/directive/executor.py`
- Modify: `src/agentkit/directive/__init__.py`
- Test: `tests/agentkit/directive/test_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/agentkit/directive/test_executor.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.directive.executor import DirectiveExecutor
from agentkit.directive.types import Approve, Reject, AskUser, Checkpoint, MemoryRecord


class TestDirectiveExecutor:
    @pytest.fixture
    def executor(self):
        return DirectiveExecutor()

    @pytest.mark.asyncio
    async def test_approve_returns_true(self, executor):
        result = await executor.execute(Approve())
        assert result is True

    @pytest.mark.asyncio
    async def test_reject_returns_false(self, executor):
        result = await executor.execute(Reject(reason="not allowed"))
        assert result is False

    @pytest.mark.asyncio
    async def test_ask_user_with_handler(self):
        async def user_handler(question: str) -> bool:
            return True  # Simulate user approval

        executor = DirectiveExecutor(ask_user_handler=user_handler)
        result = await executor.execute(AskUser(question="Allow?"))
        assert result is True

    @pytest.mark.asyncio
    async def test_ask_user_without_handler_defaults_reject(self):
        executor = DirectiveExecutor()
        result = await executor.execute(AskUser(question="Allow?"))
        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_calls_storage(self):
        storage_handler = AsyncMock()
        executor = DirectiveExecutor(checkpoint_handler=storage_handler)
        await executor.execute(Checkpoint(plugin_id="memory", state={"key": "val"}))
        storage_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_record_calls_handler(self):
        memory_handler = AsyncMock()
        executor = DirectiveExecutor(memory_handler=memory_handler)
        record = MemoryRecord(summary="test", tags=["a"], importance=0.5)
        await executor.execute(record)
        memory_handler.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_unknown_directive_raises(self, executor):
        from agentkit.directive.types import Directive

        class UnknownDirective(Directive):
            kind: str = "unknown"

        with pytest.raises(ValueError, match="unknown directive"):
            await executor.execute(UnknownDirective())
```

- [ ] **Step 2: Implement DirectiveExecutor**

```python
# src/agentkit/directive/executor.py
"""DirectiveExecutor — dispatches Directive structs to side effects.

The runtime calls execute(directive) after receiving directives from hooks.
Each directive type maps to a specific handler:
  - Approve → return True (proceed)
  - Reject → return False (stop)
  - AskUser → call user interaction handler
  - Checkpoint → persist plugin state
  - MemoryRecord → store memory
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from agentkit.directive.types import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    MemoryRecord,
    Reject,
)

logger = logging.getLogger(__name__)

AsyncHandler = Callable[..., Coroutine[Any, Any, Any]]


class DirectiveExecutor:
    """Executes Directive structs by dispatching to registered handlers."""

    def __init__(
        self,
        ask_user_handler: AsyncHandler | None = None,
        checkpoint_handler: AsyncHandler | None = None,
        memory_handler: AsyncHandler | None = None,
    ) -> None:
        self._ask_user = ask_user_handler
        self._checkpoint = checkpoint_handler
        self._memory = memory_handler

    async def execute(self, directive: Directive) -> Any:
        """Execute a directive and return the result.

        Returns:
            True for Approve, False for Reject/denied AskUser.
            Handler results for Checkpoint/MemoryRecord.

        Raises:
            ValueError: For unknown directive types.
        """
        if isinstance(directive, Approve):
            return True
        elif isinstance(directive, Reject):
            logger.info("Rejected: %s", directive.reason)
            return False
        elif isinstance(directive, AskUser):
            if self._ask_user is not None:
                return await self._ask_user(directive.question)
            logger.warning("No ask_user handler — defaulting to reject")
            return False
        elif isinstance(directive, Checkpoint):
            if self._checkpoint is not None:
                await self._checkpoint(directive)
            else:
                logger.debug("No checkpoint handler — skipping")
            return None
        elif isinstance(directive, MemoryRecord):
            if self._memory is not None:
                await self._memory(directive)
            else:
                logger.debug("No memory handler — skipping")
            return None
        else:
            raise ValueError(f"unknown directive kind: {directive.kind}")
```

- [ ] **Step 3: Update directive __init__.py**

```python
# src/agentkit/directive/__init__.py
from agentkit.directive.executor import DirectiveExecutor
from agentkit.directive.types import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    MemoryRecord,
    Reject,
)

__all__ = [
    "Approve",
    "AskUser",
    "Checkpoint",
    "Directive",
    "DirectiveExecutor",
    "MemoryRecord",
    "Reject",
]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/agentkit/directive/ -v`
Expected: All 14 tests PASS (7 types + 7 executor)

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/directive/ tests/agentkit/directive/
git commit -m "feat(agentkit): add DirectiveExecutor for dispatching hook directives"
```

---

### Task 23: Bootstrap + End-to-End Integration Test

**Files:**
- Create: `src/coding_agent/agent.toml`
- Modify: `src/coding_agent/__main__.py`
- Test: `tests/coding_agent/test_bootstrap.py`
- Test: `tests/integration/test_e2e.py`

- [ ] **Step 1: Create agent.toml config**

```toml
# src/coding_agent/agent.toml
[agent]
name = "coding-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"
system_prompt = """You are a skilled coding assistant. You help users write, debug, and improve code.
You have access to file operations, shell commands, and search tools.
Always explain your reasoning before making changes."""
max_turns = 30

[agent.plugins]
enabled = [
    "llm_provider",
    "storage",
    "core_tools",
    "approval",
    "summarizer",
    "memory",
    "shell_session",
]

[storage]
tape_backend = "jsonl"
doc_backend = "lancedb"

[storage.paths]
tapes = "./data/tapes"
docs = "./data/docs"
sessions = "./data/sessions"

[approval]
policy = "auto"
blocked_tools = []

[summarizer]
max_entries = 100
keep_recent = 20
```

- [ ] **Step 2: Write failing bootstrap test**

```python
# tests/coding_agent/test_bootstrap.py
import pytest
from pathlib import Path
from coding_agent.__main__ import create_agent


class TestBootstrap:
    def test_create_agent_returns_pipeline_and_context(self, tmp_path):
        """Test that create_agent wires everything together."""
        from agentkit.runtime.pipeline import Pipeline, PipelineContext

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",  # Won't actually call LLM
        )
        assert isinstance(pipeline, Pipeline)
        assert isinstance(ctx, PipelineContext)

    def test_all_plugins_registered(self, tmp_path):
        from agentkit.runtime.pipeline import Pipeline, PipelineContext

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )
        # Verify all expected plugins are registered
        plugin_ids = pipeline._registry.plugin_ids()
        assert "llm_provider" in plugin_ids
        assert "storage" in plugin_ids
        assert "core_tools" in plugin_ids
        assert "approval" in plugin_ids
        assert "memory" in plugin_ids
```

- [ ] **Step 3: Implement create_agent bootstrap**

```python
# src/coding_agent/__main__.py (rewrite)
"""coding_agent bootstrap — loads config and wires plugins into the pipeline.

Usage:
    python -m coding_agent                    # Interactive REPL
    python -m coding_agent --config path.toml # Custom config
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agentkit.config.loader import AgentConfig, load_config
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.tape import Tape


def create_agent(
    config_path: Path | None = None,
    data_dir: Path | None = None,
    api_key: str | None = None,
    model_override: str | None = None,
) -> tuple[Pipeline, PipelineContext]:
    """Create a fully wired agent from config.

    Args:
        config_path: Path to agent.toml. Defaults to bundled config.
        data_dir: Data directory for storage. Defaults to ./data.
        api_key: API key override (otherwise from env).
        model_override: Override model from config.

    Returns:
        (Pipeline, PipelineContext) ready for run_turn().
    """
    import os

    if config_path is None:
        config_path = Path(__file__).parent / "agent.toml"
    if data_dir is None:
        data_dir = Path("./data")

    cfg = load_config(config_path)

    if model_override:
        cfg.model = model_override

    resolved_key = api_key or os.environ.get("AGENT_API_KEY", "")

    # Create plugin instances
    registry = PluginRegistry()

    from coding_agent.plugins.llm_provider import LLMProviderPlugin
    from coding_agent.plugins.storage import StoragePlugin
    from coding_agent.plugins.core_tools import CoreToolsPlugin
    from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy
    from coding_agent.plugins.summarizer import SummarizerPlugin
    from coding_agent.plugins.memory import MemoryPlugin
    from coding_agent.plugins.shell_session import ShellSessionPlugin

    # Parse approval policy from config
    approval_cfg = cfg.extra.get("approval", {})
    policy_str = approval_cfg.get("policy", "auto")
    policy = ApprovalPolicy(policy_str)

    # Parse summarizer config
    sum_cfg = cfg.extra.get("summarizer", {})

    # Register plugins in order
    registry.register(LLMProviderPlugin(
        provider=cfg.provider,
        model=cfg.model,
        api_key=resolved_key,
    ))
    registry.register(StoragePlugin(data_dir=data_dir))
    registry.register(CoreToolsPlugin())
    registry.register(ApprovalPlugin(
        policy=policy,
        blocked_tools=set(approval_cfg.get("blocked_tools", [])),
    ))
    registry.register(SummarizerPlugin(
        max_entries=sum_cfg.get("max_entries", 100),
        keep_recent=sum_cfg.get("keep_recent", 20),
    ))
    registry.register(MemoryPlugin())
    registry.register(ShellSessionPlugin())

    # Wire runtime and pipeline
    runtime = HookRuntime(registry)

    # Create DirectiveExecutor with handlers
    from agentkit.directive.executor import DirectiveExecutor

    directive_executor = DirectiveExecutor(
        # ask_user_handler and checkpoint_handler can be wired to
        # Channel and Storage later; for now use defaults.
    )

    pipeline = Pipeline(
        runtime=runtime,
        registry=registry,
        directive_executor=directive_executor,
    )

    # Create initial context
    ctx = PipelineContext(
        tape=Tape(),
        session_id="",
        config={
            "system_prompt": cfg.system_prompt,
            "model": cfg.model,
            "provider": cfg.provider,
            "max_turns": cfg.max_turns,
        },
    )

    return pipeline, ctx


def main() -> None:
    """Entry point for python -m coding_agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Coding Agent")
    parser.add_argument("--config", type=Path, help="Path to agent.toml")
    parser.add_argument("--model", type=str, help="Override model")
    parser.add_argument("--api-key", type=str, help="API key")
    args = parser.parse_args()

    pipeline, ctx = create_agent(
        config_path=args.config,
        model_override=args.model,
        api_key=args.api_key,
    )

    # TODO: Wire to CLI/TUI (existing cli/ and ui/ modules)
    # For now, print confirmation
    print(f"Agent '{ctx.config.get('model', '?')}' ready with {len(pipeline._registry.plugin_ids())} plugins")
    print(f"Plugins: {', '.join(pipeline._registry.plugin_ids())}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write integration test**

```python
# tests/integration/test_e2e.py
"""End-to-end integration test — verify all layers wire together."""

import pytest
from pathlib import Path
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_pipeline_mount_and_turn(self, tmp_path):
        """Test that the full pipeline can mount and execute a turn."""
        from coding_agent.__main__ import create_agent

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        # Mount all plugins
        await pipeline.mount(ctx)

        # Verify plugin states populated
        assert len(ctx.plugin_states) > 0

        # Add a user message
        ctx.tape.append(Entry(
            kind="message",
            payload={"role": "user", "content": "hello"},
        ))

        # Run a turn (LLM is mocked via test API key — run_model stage will be a no-op)
        result = await pipeline.run_turn(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_tool_registration_complete(self, tmp_path):
        """Verify all expected tools are registered."""
        from coding_agent.__main__ import create_agent

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        # Collect tools via hook
        tool_lists = pipeline._runtime.call_many("get_tools")
        all_tools = []
        for tl in tool_lists:
            if isinstance(tl, list):
                all_tools.extend(tl)

        tool_names = {t.name for t in all_tools}
        assert "file_read" in tool_names
        assert "file_write" in tool_names
        assert "bash_run" in tool_names
        assert "grep_search" in tool_names

    @pytest.mark.asyncio
    async def test_approval_directive_flow(self, tmp_path):
        """Test that approve_tool_call returns proper Directive."""
        from coding_agent.__main__ import create_agent
        from agentkit.directive.types import Approve

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        # Call approve_tool_call — default policy is AUTO
        result = pipeline._runtime.call_first(
            "approve_tool_call",
            tool_name="file_read",
            arguments={"path": "/tmp/test.txt"},
        )
        assert isinstance(result, Approve)

    @pytest.mark.asyncio
    async def test_memory_grounding_flow(self, tmp_path):
        """Test that build_context returns grounding messages."""
        from coding_agent.__main__ import create_agent

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        # build_context with empty memory returns empty list
        results = pipeline._runtime.call_many("build_context", tape=ctx.tape)
        # At minimum, it should not raise
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_golden_path_tool_assisted_turn(self, tmp_path):
        """Golden-path test: user msg → model emits tool_call → approval →
        tool executed → result appended → model emits text → tape committed.

        This is THE test that proves one complete agent turn works end-to-end.
        """
        from unittest.mock import AsyncMock
        from coding_agent.__main__ import create_agent
        from agentkit.directive.types import Approve
        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent
        from agentkit.tape.models import Entry

        config_path = Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )

        # Mount all plugins
        await pipeline.mount(ctx)

        # Add user message
        ctx.tape.append(Entry(
            kind="message",
            payload={"role": "user", "content": "Read the file test.txt"},
        ))

        # Mock LLM provider to simulate tool-assisted turn
        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: model requests a tool
                yield ToolCallEvent(
                    tool_call_id="tc-001",
                    name="file_read",
                    arguments={"path": "test.txt"},
                )
                yield DoneEvent()
            else:
                # Second call: model responds with text after tool result
                yield TextEvent(text="The file contains: hello world")
                yield DoneEvent()

        ctx.llm_provider = AsyncMock()
        ctx.llm_provider.stream = mock_stream

        # Run one turn
        result = await pipeline.run_turn(ctx)

        # Verify the full sequence in tape
        entries = list(ctx.tape)
        kinds = [e.kind for e in entries]

        # Expected: message → tool_call → tool_result → message (assistant)
        assert kinds[0] == "message"       # user
        assert "tool_call" in kinds        # model asked for tool
        assert "tool_result" in kinds      # tool was executed
        assert entries[-1].kind == "message"  # final assistant response
        assert entries[-1].payload["role"] == "assistant"
        assert "hello world" in entries[-1].payload["content"]

        # Verify the LLM was called twice (tool loop)
        assert call_count == 2
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/ -v --ignore=tests/coding_agent/tools/test_planner.py`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS. Note any pre-existing test failures from the original codebase.

- [ ] **Step 7: Update agentkit __init__.py with full public API**

```python
# src/agentkit/__init__.py
"""agentkit — A hook-driven agent framework.

Core API:
    HookRuntime, Pipeline, PipelineContext — Runtime and execution
    Plugin, PluginRegistry — Plugin system
    Directive, Approve, Reject, AskUser, Checkpoint, MemoryRecord — Effect descriptions
    Entry, Tape, ForkTapeStore — Conversation history
    TapeStore, DocIndex, SessionStore — Storage protocols
    ToolSchema, ToolRegistry, tool — Tool system
    Channel, LocalChannel — Communication
    AgentConfig, load_config — Configuration
    ContextBuilder — Message assembly
    normalize_instruction — Input normalization
"""

from agentkit.channel import Channel, LocalChannel
from agentkit.config import AgentConfig, load_config
from agentkit.context import ContextBuilder
from agentkit.directive import (
    Approve,
    AskUser,
    Checkpoint,
    Directive,
    DirectiveExecutor,
    MemoryRecord,
    Reject,
)
from agentkit.errors import (
    AgentKitError,
    ConfigError,
    DirectiveError,
    HookError,
    PipelineError,
    PluginError,
    StorageError,
    ToolError,
)
from agentkit.instruction import normalize_instruction
from agentkit.plugin import Plugin, PluginRegistry
from agentkit.providers import DoneEvent, LLMProvider, StreamEvent, TextEvent, ToolCallEvent
from agentkit.runtime import HookRuntime, Pipeline, PipelineContext
from agentkit.storage import DocIndex, SessionStore, TapeStore
from agentkit.tape import Entry, ForkTapeStore, Tape
from agentkit.tools import ToolRegistry, ToolSchema, tool

__all__ = [
    # Runtime
    "HookRuntime", "Pipeline", "PipelineContext",
    # Plugins
    "Plugin", "PluginRegistry",
    # Directives
    "Directive", "DirectiveExecutor",
    "Approve", "Reject", "AskUser", "Checkpoint", "MemoryRecord",
    # Tape
    "Entry", "Tape", "ForkTapeStore",
    # Storage
    "TapeStore", "DocIndex", "SessionStore",
    # Tools
    "ToolSchema", "ToolRegistry", "tool",
    # Providers
    "LLMProvider", "StreamEvent", "TextEvent", "ToolCallEvent", "DoneEvent",
    # Channel
    "Channel", "LocalChannel",
    # Config
    "AgentConfig", "load_config",
    # Context
    "ContextBuilder",
    # Instruction
    "normalize_instruction",
    # Errors
    "AgentKitError", "HookError", "PipelineError", "PluginError",
    "DirectiveError", "StorageError", "ToolError", "ConfigError",
]
```

- [ ] **Step 8: Commit**

```bash
git add src/ tests/
git commit -m "feat: complete agentkit + coding_agent bootstrap with end-to-end integration"
```

---

## Summary

| Task | Component | Layer | Tests |
|------|-----------|-------|-------|
| T1 | Error hierarchy + types | agentkit | 5 |
| T2 | Entry + Tape models | agentkit | 15 |
| T3 | Directive types | agentkit | 7 |
| T4 | Plugin Protocol + Registry | agentkit | 12 |
| T5 | Storage Protocols + ForkTapeStore | agentkit | 8 |
| T6 | HookRuntime | agentkit | 8 |
| T7 | Hook Specs | agentkit | 11 |
| T8 | ContextBuilder | agentkit | 7 |
| T9 | @tool + ToolRegistry | agentkit | 19 |
| T10 | Pipeline | agentkit | 7 |
| T11 | Channel | agentkit | 9 |
| T12 | Instruction Normalize | agentkit | 9 |
| T13 | TOML Config | agentkit | 7 |
| T14 | LLM Provider Protocol | agentkit | 7 |
| T15 | LLMProviderPlugin | coding_agent | 5 |
| T16 | StoragePlugin | coding_agent | 9 |
| T17 | CoreToolsPlugin + Tools | coding_agent | 12 |
| T18 | ApprovalPlugin | coding_agent | 6 |
| T19 | SummarizerPlugin | coding_agent | 5 |
| T20 | MemoryPlugin | coding_agent | 7 |
| T21 | ShellSessionPlugin | coding_agent | 6 |
| T22 | DirectiveExecutor | agentkit | 7 |
| T23 | Bootstrap + E2E | both | 7 |
| **Total** | | | **~196** |

### What This Plan Does NOT Cover (Future Tasks)

- **CLI/TUI migration**: The existing `cli/` and `ui/` modules are not rewritten. They will need adapting to the new Pipeline/Channel API in a follow-up.
- **LLM Provider migration**: Task 15 references migrating existing provider code. The core streaming logic is kept; only the interface changes.
- **Subagent system**: The subagent tool is kept minimal (spawn_subagent placeholder). Full implementation deferred.
- **DocIndex implementation**: The DocIndex protocol is defined but no concrete implementation (LanceDB) is created in this plan. Deferred to a follow-up.
- **K8s deployment**: Out of scope for V1 code architecture.
- **Nowledge-level knowledge graph**: Explicitly excluded per scope boundaries.
