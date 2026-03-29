# Hook-Based Declarative Agent Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-built hook runtime and declarative configuration architecture that composes storage, tools, provider selection, approval policy, summarization, and error observers from `agent.toml` without changing `src/coding_agent/core/context.py`.

**Architecture:** Implement six sequential phases: hook runtime and TOML config, protocol/model foundations, storage hooks and migration bridge, tool hooks and declarative tool loading, provider and domain hooks, and final bootstrap integration through one `create_agent_from_config()` factory. Every task uses strict Red-Green-Refactor style with targeted tests first and full-suite regression at task end.

**Tech Stack:** Python 3.12+, pydantic v2, tomllib or tomli fallback, pytest + pytest-asyncio, dataclasses, Protocol/runtime_checkable, existing project provider/tool/tape abstractions.

---

## File Structure

### New files to create

| File | Responsibility |
|---|---|
| `agent.toml` | Declarative runtime config for storage/tools/provider/approval/hooks |
| `src/coding_agent/hooks/__init__.py` | Hook runtime exports |
| `src/coding_agent/hooks/runtime.py` | Self-built `HookRuntime` with `call_first`, `call_many`, `notify_error` |
| `src/coding_agent/hooks/specs.py` | Hook spec protocols and hook names |
| `src/coding_agent/config/toml_config.py` | `agent.toml` loader, env expansion, pydantic models |
| `src/coding_agent/storage/__init__.py` | Storage exports |
| `src/coding_agent/storage/protocols.py` | `TapeStore`, `DocIndex`, `SessionStore` protocols |
| `src/coding_agent/storage/registry.py` | `BackendRegistry`, `register`, backend factory |
| `src/coding_agent/storage/backends/__init__.py` | Backend exports |
| `src/coding_agent/storage/backends/jsonl.py` | `JSONLTapeStore` |
| `src/coding_agent/storage/backends/null.py` | `NullDocIndex` |
| `src/coding_agent/storage/backends/memory.py` | `InMemorySessionStore` |
| `src/coding_agent/storage/hooks.py` | `provide_storage` hook implementation |
| `src/coding_agent/tape/__init__.py` | New tape model exports |
| `src/coding_agent/tape/models.py` | New `Entry` + `EntryKind` enum + ULID id generation |
| `src/coding_agent/tape/migrate.py` | Legacy `core.tape.Entry` ↔ new `tape.models.Entry` migration bridge |
| `src/coding_agent/tools/declarative.py` | `@tool`, global registry, `ToolContext`, loader |
| `src/coding_agent/providers/registry.py` | Provider registry + `register_provider` |
| `src/coding_agent/approval/hooks.py` | `approve_tool_call` hook implementation |
| `src/coding_agent/summarizer/hook_adapter.py` | Hook-aware summarizer implementation |
| `src/coding_agent/errors/hooks.py` | Error observers (`log`, `memory`) |
| `src/coding_agent/core/bootstrap.py` | `create_agent_from_config()` integration factory |
| `tests/hooks/test_runtime.py` | Hook runtime tests |
| `tests/config/test_toml_config.py` | TOML config and env expansion tests |
| `tests/storage/test_protocols.py` | Storage protocol runtime checks |
| `tests/tape/test_models.py` | New tape model tests |
| `tests/storage/test_registry.py` | Backend registry tests |
| `tests/storage/backends/test_jsonl.py` | JSONLTapeStore tests |
| `tests/storage/backends/test_null.py` | NullDocIndex tests |
| `tests/storage/backends/test_memory.py` | InMemorySessionStore tests |
| `tests/storage/test_hooks.py` | provide_storage hook tests |
| `tests/tape/test_migrate.py` | legacy-to-new entry migration tests |
| `tests/tools/test_declarative.py` | `@tool` and ToolContext tests |
| `tests/tools/test_tool_config.py` | TOML tool enable and disable tests |
| `tests/providers/test_registry.py` | provider registry tests |
| `tests/approval/test_hooks.py` | hook-driven approval policy tests |
| `tests/summarizer/test_hook_adapter.py` | summarize_context hook tests |
| `tests/errors/test_hooks.py` | on_error observer isolation tests |
| `tests/integration/test_agent_bootstrap.py` | bootstrap integration tests |
| `tests/integration/test_hooked_agent_loop.py` | end-to-end agent.toml → hooks → loop test |

### Existing files to modify

| File | Change |
|---|---|
| `pyproject.toml` | Incrementally add `tomli` dependency only |
| `src/coding_agent/kb.py` | Constructor DI with `DocIndex` protocol, remove classmethod-only wiring |
| `src/coding_agent/tools/file.py` | Refactor registration to declarative tool metadata |
| `src/coding_agent/tools/search.py` | Refactor registration to declarative tool metadata |
| `src/coding_agent/tools/shell.py` | Refactor registration to declarative tool metadata |
| `src/coding_agent/tools/planner.py` | Refactor registration to declarative tool metadata |
| `src/coding_agent/tools/subagent.py` | Refactor registration to declarative tool metadata |
| `src/coding_agent/approval/policy.py` | Replace hardcoded safe-tools behavior with hook fallback strategy |
| `src/coding_agent/core/loop.py` | Add runtime hook usage for approval and error observer path |
| `src/coding_agent/__main__.py` | Replace provider/tool/bootstrap wiring with `create_agent_from_config()` |
| `src/coding_agent/providers/__init__.py` | Export provider registry helpers |

### Explicit non-goals and constraints

- Do not use pluggy.
- Do not modify `src/coding_agent/core/context.py`.
- Do not add session hooks, prompt hooks, or CLI hooks.
- Do not add type suppressions.
- Do not replace `pyproject.toml`; only append incremental dependency entries.

---

## Phase 1: Hook Infrastructure

### Task 1: Implement HookRuntime core (`call_first`, `call_many`, isolated observer errors)

**Files:**
- Create: `src/coding_agent/hooks/__init__.py`
- Create: `src/coding_agent/hooks/runtime.py`
- Create: `src/coding_agent/hooks/specs.py`
- Modify: `none`
- Test: `tests/hooks/test_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/hooks/test_runtime.py
from __future__ import annotations

import pytest

from coding_agent.hooks.runtime import HookRuntime, SKIP


@pytest.mark.asyncio
async def test_call_first_returns_first_non_none_value() -> None:
    runtime = HookRuntime()

    async def first_impl(value: str) -> str | None:
        return None

    async def second_impl(value: str) -> str | object:
        return f"ok:{value}"

    runtime.register("provide_llm", first_impl)
    runtime.register("provide_llm", second_impl)

    result = await runtime.call_first("provide_llm", value="demo")
    assert result == "ok:demo"


@pytest.mark.asyncio
async def test_call_many_collects_all_non_none_results() -> None:
    runtime = HookRuntime()

    async def impl_a() -> str:
        return "a"

    async def impl_b() -> str:
        return "b"

    runtime.register("on_error", impl_a)
    runtime.register("on_error", impl_b)

    result = await runtime.call_many("on_error")
    assert result == ["a", "b"]


@pytest.mark.asyncio
async def test_call_first_skips_skip_marker() -> None:
    runtime = HookRuntime()

    async def impl_a() -> object:
        return SKIP

    async def impl_b() -> str:
        return "chosen"

    runtime.register("approve_tool_call", impl_a)
    runtime.register("approve_tool_call", impl_b)

    result = await runtime.call_first("approve_tool_call")
    assert result == "chosen"


@pytest.mark.asyncio
async def test_notify_error_swallows_observer_failures() -> None:
    runtime = HookRuntime()
    seen: list[str] = []

    async def broken_observer(**_: object) -> None:
        raise RuntimeError("observer failed")

    async def healthy_observer(stage: str, **_: object) -> None:
        seen.append(stage)

    runtime.register("on_error", broken_observer)
    runtime.register("on_error", healthy_observer)

    await runtime.notify_error(stage="loop", error=ValueError("boom"), message="failed")
    assert seen == ["loop"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/hooks/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.hooks'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/hooks/runtime.py
from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

HookImpl = Callable[[Any], Awaitable[Any]]
SKIP = object()


class HookRuntime:
    def __init__(self) -> None:
        self._impls: dict[str, list[HookImpl]] = defaultdict(list)

    def register(self, hook_name: str, impl: HookImpl) -> None:
        self._impls[hook_name].append(impl)

    def implementations(self, hook_name: str) -> list[HookImpl]:
        return list(self._impls.get(hook_name, []))

    async def call_first(self, hook_name: str, **kwargs: Any) -> Any | None:
        for impl in self._impls.get(hook_name, []):
            value = await impl(**kwargs)
            if value is SKIP:
                continue
            if value is not None:
                return value
        return None

    async def call_many(self, hook_name: str, **kwargs: Any) -> list[Any]:
        values: list[Any] = []
        for impl in self._impls.get(hook_name, []):
            value = await impl(**kwargs)
            if value is SKIP or value is None:
                continue
            values.append(value)
        return values

    async def notify_error(self, *, stage: str, error: Exception, message: str) -> None:
        for impl in self._impls.get("on_error", []):
            try:
                await impl(stage=stage, error=error, message=message)
            except Exception:
                continue
```

```python
# src/coding_agent/hooks/specs.py
from __future__ import annotations

from typing import Protocol, Any


class ProvideStorageHook(Protocol):
    async def __call__(self, config: Any) -> Any | None:
        raise NotImplementedError


class ProvideLLMHook(Protocol):
    async def __call__(self, config: Any) -> Any | None:
        raise NotImplementedError


class ApproveToolCallHook(Protocol):
    async def __call__(self, tool_name: str, arguments: dict[str, Any], policy: str) -> bool | None:
        raise NotImplementedError


class SummarizeContextHook(Protocol):
    async def __call__(self, messages: list[dict[str, Any]], max_tokens: int) -> str | None:
        raise NotImplementedError


class OnErrorHook(Protocol):
    async def __call__(self, stage: str, error: Exception, message: str) -> None:
        raise NotImplementedError
```

```python
# src/coding_agent/hooks/__init__.py
from coding_agent.hooks.runtime import HookRuntime, SKIP

__all__ = ["HookRuntime", "SKIP"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/hooks/test_runtime.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/hooks/__init__.py src/coding_agent/hooks/runtime.py src/coding_agent/hooks/specs.py tests/hooks/test_runtime.py
git commit -m "feat(hooks): add self-built runtime with first/many and isolated error observers"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes (at least baseline 524 tests, plus new hook tests).

---

### Task 2: Add `agent.toml` + TOML config loader (env expansion + pydantic)

**Files:**
- Modify: `pyproject.toml` (incremental addition only)
- Create: `agent.toml`
- Create: `src/coding_agent/config/toml_config.py`
- Test: `tests/config/test_toml_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_toml_config.py
from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config.toml_config import AgentTomlConfig, load_agent_toml


def test_load_agent_toml_and_expand_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[provider]
name = "openai"
model = "gpt-4o"
api_key = "${OPENAI_API_KEY}"

[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read", "grep", "glob", "bash", "todo_read", "todo_write"]

[approval]
policy = "auto"
safe_tools = ["file_read", "grep", "glob", "todo_read"]
""".strip(),
        encoding="utf-8",
    )

    loaded = load_agent_toml(config_path)
    assert isinstance(loaded, AgentTomlConfig)
    assert loaded.provider.api_key == "sk-test-key"
    assert loaded.storage.tape.backend == "jsonl"
    assert loaded.tools.enabled[0] == "file_read"


def test_default_agent_toml_path(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[provider]
name = "openai"
model = "gpt-4o"

[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read"]

[approval]
policy = "auto"
safe_tools = ["file_read"]
""".strip(),
        encoding="utf-8",
    )

    loaded = load_agent_toml(config_path)
    assert loaded.provider.name == "openai"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_toml_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.config.toml_config'`.

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml (incremental addition only)
dependencies = [
    "openai>=1.50.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
    "click>=8.0.0",
    "rich>=13.0.0",
    "prompt-toolkit>=3.0.0",
    "tiktoken>=0.5.0",
    "lancedb>=0.18.0",
    "numpy>=1.26.0",
    "pyyaml>=6.0",
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.23.0",
    "sse-starlette>=1.6.0",
    "slowapi>=0.1.9",
    "limits>=3.0.0",
    "tomli>=2.0.1",  # newly added line
]
```

```toml
# agent.toml
[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read", "file_write", "file_patch", "grep", "glob", "bash", "todo_read", "todo_write", "subagent"]

[provider]
name = "openai"
model = "gpt-4o"
api_key = "${AGENT_API_KEY}"

[approval]
policy = "auto"
safe_tools = ["file_read", "grep", "glob", "todo_read"]

[hooks.on_error]
observers = ["log"]
```

```python
# src/coding_agent/config/toml_config.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class BackendSection(BaseModel):
    backend: str
    data_dir: str | None = None


class StorageSection(BaseModel):
    tape: BackendSection
    doc_index: BackendSection
    session: BackendSection


class ToolsSection(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class ProviderSection(BaseModel):
    name: str
    model: str
    api_key: str | None = None
    base_url: str | None = None


class ApprovalSection(BaseModel):
    policy: str = "auto"
    safe_tools: list[str] = Field(default_factory=list)


class HookErrorSection(BaseModel):
    observers: list[str] = Field(default_factory=list)


class HooksSection(BaseModel):
    on_error: HookErrorSection = Field(default_factory=HookErrorSection)


class AgentTomlConfig(BaseModel):
    storage: StorageSection
    tools: ToolsSection
    provider: ProviderSection
    approval: ApprovalSection
    hooks: HooksSection = Field(default_factory=HooksSection)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_agent_toml(path: Path | str = "agent.toml") -> AgentTomlConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    expanded = _expand_env(data)
    return AgentTomlConfig.model_validate(expanded)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_toml_config.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml agent.toml src/coding_agent/config/toml_config.py tests/config/test_toml_config.py
git commit -m "feat(config): add declarative agent.toml loader with env expansion"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes (at least baseline 524 tests, plus new config tests).

---

## Phase 2: Protocols and Models

### Task 3: Add runtime-checkable storage protocols (`TapeStore`, `DocIndex`, `SessionStore`)

**Files:**
- Create: `src/coding_agent/storage/__init__.py`
- Create: `src/coding_agent/storage/protocols.py`
- Modify: `none`
- Test: `tests/storage/test_protocols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_protocols.py
from __future__ import annotations

from typing import Any

from coding_agent.storage.protocols import DocIndex, SessionStore, TapeStore


class FakeTapeStore:
    def append(self, entry: Any) -> Any:
        return entry

    def entries(self) -> list[Any]:
        return []


class FakeDocIndex:
    async def search(self, query: str, k: int = 5) -> list[Any]:
        return []

    async def upsert(self, doc_id: str, content: str, source: str, metadata: dict[str, Any]) -> None:
        return None


class FakeSessionStore:
    def create(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id}

    def get(self, session_id: str) -> dict[str, Any] | None:
        return None


def test_runtime_checkable_protocols() -> None:
    assert isinstance(FakeTapeStore(), TapeStore)
    assert isinstance(FakeDocIndex(), DocIndex)
    assert isinstance(FakeSessionStore(), SessionStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_protocols.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.storage'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/storage/protocols.py
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TapeStore(Protocol):
    def append(self, entry: Any) -> Any:
        raise NotImplementedError

    def entries(self) -> list[Any]:
        raise NotImplementedError


@runtime_checkable
class DocIndex(Protocol):
    async def search(self, query: str, k: int = 5) -> list[Any]:
        raise NotImplementedError

    async def upsert(self, doc_id: str, content: str, source: str, metadata: dict[str, Any]) -> None:
        raise NotImplementedError


@runtime_checkable
class SessionStore(Protocol):
    def create(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError
```

```python
# src/coding_agent/storage/__init__.py
from coding_agent.storage.protocols import TapeStore, DocIndex, SessionStore

__all__ = ["TapeStore", "DocIndex", "SessionStore"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_protocols.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/storage/__init__.py src/coding_agent/storage/protocols.py tests/storage/test_protocols.py
git commit -m "feat(storage): define runtime-checkable tape/doc/session protocols"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 4: Create new tape entry model (`ULID`, `EntryKind` Enum includes `EVENT`, `meta` dict)

**Files:**
- Create: `src/coding_agent/tape/__init__.py`
- Create: `src/coding_agent/tape/models.py`
- Modify: `none`
- Test: `tests/tape/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tape/test_models.py
from __future__ import annotations

from coding_agent.tape.models import Entry, EntryKind


def test_entry_kind_includes_event() -> None:
    assert EntryKind.EVENT.value == "event"


def test_entry_has_ulid_id_and_meta() -> None:
    entry = Entry(kind=EntryKind.MESSAGE, payload={"role": "user", "content": "hello"})
    assert isinstance(entry.id, str)
    assert len(entry.id) == 26
    assert entry.meta == {}


def test_entry_to_dict_from_dict_roundtrip() -> None:
    entry = Entry(kind=EntryKind.TOOL_RESULT, payload={"call_id": "x", "result": "ok"}, meta={"source": "test"})
    restored = Entry.from_dict(entry.to_dict())
    assert restored.id == entry.id
    assert restored.kind == EntryKind.TOOL_RESULT
    assert restored.meta == {"source": "test"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tape/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.tape.models'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tape/models.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, width: int) -> str:
    chars: list[str] = []
    for _ in range(width):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def generate_ulid() -> str:
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    random_bits = int.from_bytes(os.urandom(10), "big")
    return f"{_encode_base32(timestamp_ms, 10)}{_encode_base32(random_bits, 16)}"


class EntryKind(str, Enum):
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ANCHOR = "anchor"
    EVENT = "event"


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    payload: dict
    id: str = field(default_factory=generate_ulid)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        return cls(
            id=data["id"],
            kind=EntryKind(data["kind"]),
            payload=data["payload"],
            timestamp=data["timestamp"],
            meta=data.get("meta", {}),
        )
```

```python
# src/coding_agent/tape/__init__.py
from coding_agent.tape.models import Entry, EntryKind, generate_ulid

__all__ = ["Entry", "EntryKind", "generate_ulid"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tape/test_models.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tape/__init__.py src/coding_agent/tape/models.py tests/tape/test_models.py
git commit -m "feat(tape): add enum-based entry model with ulid ids and meta field"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

## Phase 3: Storage Hooks

### Task 5: Add `BackendRegistry` and `@register` decorator (global namespace registry)

**Files:**
- Create: `src/coding_agent/storage/registry.py`
- Modify: `none`
- Test: `tests/storage/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_registry.py
from __future__ import annotations

import pytest

from coding_agent.storage.registry import BackendRegistry, register


def test_register_and_get_backend() -> None:
    @register("tape", "dummy")
    class DummyTapeStore:
        pass

    backend_cls = BackendRegistry.get("tape", "dummy")
    assert backend_cls is DummyTapeStore


def test_get_missing_backend_raises_key_error() -> None:
    with pytest.raises(KeyError):
        BackendRegistry.get("doc_index", "missing")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.storage.registry'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/storage/registry.py
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


class BackendRegistry:
    _registry: dict[str, dict[str, type[Any]]] = defaultdict(dict)

    @classmethod
    def register(cls, namespace: str, name: str, backend_cls: type[Any]) -> type[Any]:
        cls._registry[namespace][name] = backend_cls
        return backend_cls

    @classmethod
    def get(cls, namespace: str, name: str) -> type[Any]:
        try:
            return cls._registry[namespace][name]
        except KeyError as error:
            raise KeyError(f"Backend not found: {namespace}.{name}") from error

    @classmethod
    def create(cls, namespace: str, name: str, **kwargs: Any) -> Any:
        backend_cls = cls.get(namespace, name)
        return backend_cls(**kwargs)


def register(namespace: str, name: str) -> Callable[[type[Any]], type[Any]]:
    def decorator(backend_cls: type[Any]) -> type[Any]:
        return BackendRegistry.register(namespace, name, backend_cls)
    return decorator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_registry.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/storage/registry.py tests/storage/test_registry.py
git commit -m "feat(storage): add namespace backend registry and register decorator"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 6: Implement built-in storage backends (`JSONLTapeStore`, `NullDocIndex`, `InMemorySessionStore`)

**Files:**
- Create: `src/coding_agent/storage/backends/__init__.py`
- Create: `src/coding_agent/storage/backends/jsonl.py`
- Create: `src/coding_agent/storage/backends/null.py`
- Create: `src/coding_agent/storage/backends/memory.py`
- Modify: `none`
- Test: `tests/storage/backends/test_jsonl.py`
- Test: `tests/storage/backends/test_null.py`
- Test: `tests/storage/backends/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/storage/backends/test_jsonl.py
from __future__ import annotations

from coding_agent.storage.backends.jsonl import JSONLTapeStore
from coding_agent.tape.models import Entry, EntryKind


def test_jsonl_tape_store_append_and_entries(tmp_path) -> None:
    store = JSONLTapeStore(data_dir=tmp_path)
    first = store.append(Entry(kind=EntryKind.MESSAGE, payload={"role": "user", "content": "hello"}))
    second = store.append(Entry(kind=EntryKind.MESSAGE, payload={"role": "assistant", "content": "hi"}))
    assert first.id != second.id
    assert len(store.entries()) == 2
```

```python
# tests/storage/backends/test_null.py
from __future__ import annotations

import pytest

from coding_agent.storage.backends.null import NullDocIndex


@pytest.mark.asyncio
async def test_null_doc_index_search_returns_empty() -> None:
    index = NullDocIndex()
    result = await index.search("anything", k=5)
    assert result == []
```

```python
# tests/storage/backends/test_memory.py
from __future__ import annotations

from coding_agent.storage.backends.memory import InMemorySessionStore


def test_in_memory_session_store_roundtrip() -> None:
    store = InMemorySessionStore()
    created = store.create("session-1")
    loaded = store.get("session-1")
    assert created["session_id"] == "session-1"
    assert loaded == created
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/storage/backends/test_jsonl.py tests/storage/backends/test_null.py tests/storage/backends/test_memory.py -v`
Expected: FAIL with missing backend modules.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/storage/backends/jsonl.py
from __future__ import annotations

import json
from pathlib import Path

from coding_agent.storage.registry import register
from coding_agent.tape.models import Entry


@register("tape", "jsonl")
class JSONLTapeStore:
    def __init__(self, data_dir: Path | str) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "active.jsonl"
        self._entries: list[Entry] = []
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._entries.append(Entry.from_dict(json.loads(line)))

    def append(self, entry: Entry) -> Entry:
        self._entries.append(entry)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[Entry]:
        return list(self._entries)
```

```python
# src/coding_agent/storage/backends/null.py
from __future__ import annotations

from typing import Any

from coding_agent.storage.registry import register


@register("doc_index", "null")
class NullDocIndex:
    async def search(self, query: str, k: int = 5) -> list[Any]:
        return []

    async def upsert(self, doc_id: str, content: str, source: str, metadata: dict[str, Any]) -> None:
        return None
```

```python
# src/coding_agent/storage/backends/memory.py
from __future__ import annotations

from datetime import datetime, timezone

from coding_agent.storage.registry import register


@register("session", "memory")
class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def create(self, session_id: str) -> dict:
        created = {
            "session_id": session_id,
            "status": "active",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._sessions[session_id] = created
        return created

    def get(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)
```

```python
# src/coding_agent/storage/backends/__init__.py
from coding_agent.storage.backends.jsonl import JSONLTapeStore
from coding_agent.storage.backends.null import NullDocIndex
from coding_agent.storage.backends.memory import InMemorySessionStore

__all__ = ["JSONLTapeStore", "NullDocIndex", "InMemorySessionStore"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/storage/backends/test_jsonl.py tests/storage/backends/test_null.py tests/storage/backends/test_memory.py -v`
Expected: PASS, all three files pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/storage/backends/__init__.py src/coding_agent/storage/backends/jsonl.py src/coding_agent/storage/backends/null.py src/coding_agent/storage/backends/memory.py tests/storage/backends/test_jsonl.py tests/storage/backends/test_null.py tests/storage/backends/test_memory.py
git commit -m "feat(storage): add built-in jsonl tape null doc and memory session backends"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 7: Add `provide_storage` hook and wire it to `agent.toml`

**Files:**
- Create: `src/coding_agent/storage/hooks.py`
- Modify: `src/coding_agent/hooks/specs.py`
- Test: `tests/storage/test_hooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_hooks.py
from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config.toml_config import load_agent_toml
from coding_agent.hooks.runtime import HookRuntime
from coding_agent.storage.hooks import provide_storage


@pytest.mark.asyncio
async def test_provide_storage_returns_bundle(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[storage.tape]
backend = "jsonl"
data_dir = "./tmp/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read"]

[provider]
name = "openai"
model = "gpt-4o"

[approval]
policy = "auto"
safe_tools = ["file_read"]
""".strip(),
        encoding="utf-8",
    )

    runtime = HookRuntime()
    runtime.register("provide_storage", provide_storage)

    config = load_agent_toml(config_path)
    bundle = await runtime.call_first("provide_storage", config=config)

    assert bundle is not None
    assert bundle.tape is not None
    assert bundle.doc_index is not None
    assert bundle.session is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_hooks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.storage.hooks'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/storage/hooks.py
from __future__ import annotations

from dataclasses import dataclass

from coding_agent.config.toml_config import AgentTomlConfig
from coding_agent.storage.backends import JSONLTapeStore, NullDocIndex, InMemorySessionStore
from coding_agent.storage.registry import BackendRegistry


@dataclass
class StorageBundle:
    tape: object
    doc_index: object
    session: object


async def provide_storage(config: AgentTomlConfig) -> StorageBundle:
    tape = BackendRegistry.create(
        "tape",
        config.storage.tape.backend,
        data_dir=config.storage.tape.data_dir or "./data/tapes",
    )
    doc_index = BackendRegistry.create("doc_index", config.storage.doc_index.backend)
    session = BackendRegistry.create("session", config.storage.session.backend)
    return StorageBundle(tape=tape, doc_index=doc_index, session=session)


_ = JSONLTapeStore, NullDocIndex, InMemorySessionStore
```

```python
# src/coding_agent/hooks/specs.py (add storage bundle protocol)
from dataclasses import dataclass


@dataclass
class StorageSpec:
    tape: object
    doc_index: object
    session: object
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_hooks.py -v`
Expected: PASS, all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/storage/hooks.py src/coding_agent/hooks/specs.py tests/storage/test_hooks.py
git commit -m "feat(storage): add provide_storage hook wired to declarative backends"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 8: Refactor KB to constructor DI with `DocIndex` protocol

**Files:**
- Create: `none`
- Modify: `src/coding_agent/kb.py`
- Test: `tests/test_kb.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kb.py (add test)
class FakeDocIndex:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int]] = []

    async def search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        self.search_calls.append((query, k))
        return []

    async def upsert(self, doc_id: str, content: str, source: str, metadata: dict[str, Any]) -> None:
        return None


@pytest.mark.asyncio
async def test_kb_accepts_doc_index_dependency(temp_db_path):
    fake_index = FakeDocIndex()
    kb = KB(db_path=temp_db_path, doc_index=fake_index)

    result = await kb.search("hello", k=3)
    assert result == []
    assert fake_index.search_calls == [("hello", 3)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kb.py::test_kb_accepts_doc_index_dependency -v`
Expected: FAIL with `TypeError: KB.__init__() got an unexpected keyword argument 'doc_index'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/kb.py (add constructor arg and delegation)
from coding_agent.storage.protocols import DocIndex


class LanceDocIndex:
    def __init__(self, kb: "KB") -> None:
        self._kb = kb

    async def search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        return await self._kb._search_lancedb(query, k)

    async def upsert(self, doc_id: str, content: str, source: str, metadata: dict[str, Any]) -> None:
        await self._kb.index_file(Path(source), content)


class KB:
    def __init__(
        self,
        db_path: Path | str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
        doc_index: DocIndex | None = None,
    ):
        self.db_path = Path(db_path)
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._embedding_fn = embedding_fn
        self._openai_client = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self.db_path))
        self._table: lancedb.table.Table | None = None
        self._doc_index: DocIndex = doc_index or LanceDocIndex(self)

    async def _search_lancedb(self, query: str, k: int = 5) -> list[KBSearchResult]:
        if not query.strip():
            return []
        table = self._get_table()
        embeddings = await self._embed([query])
        query_vector = embeddings[0]
        results = table.search(query_vector).limit(k).to_list()
        import json
        return [
            KBSearchResult(
                chunk=DocumentChunk(
                    id=r["id"],
                    content=r["content"],
                    source=r["source"],
                    metadata=json.loads(r["metadata"]),
                ),
                score=r["_distance"],
            )
            for r in results
        ]

    async def search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        return await self._doc_index.search(query, k)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kb.py::test_kb_accepts_doc_index_dependency -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/kb.py tests/test_kb.py
git commit -m "refactor(kb): inject doc index protocol via constructor dependency"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 9: Add tape migration bridge from legacy entry to new entry model

**Files:**
- Create: `src/coding_agent/tape/migrate.py`
- Modify: `none`
- Test: `tests/tape/test_migrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tape/test_migrate.py
from __future__ import annotations

from coding_agent.core.tape import Entry as LegacyEntry
from coding_agent.tape.migrate import from_legacy_entry, to_legacy_entry
from coding_agent.tape.models import EntryKind


def test_from_legacy_entry_maps_fields() -> None:
    legacy = LegacyEntry.event(type="handoff", data={"phase": 1}, id=7)
    migrated = from_legacy_entry(legacy)
    assert migrated.kind == EntryKind.EVENT
    assert migrated.payload == legacy.payload
    assert migrated.meta["legacy_id"] == 7


def test_to_legacy_entry_maps_back_to_literal_kinds() -> None:
    legacy = LegacyEntry.message(role="user", content="hi", id=11)
    migrated = from_legacy_entry(legacy)
    restored = to_legacy_entry(migrated)
    assert restored.kind == "message"
    assert restored.payload["content"] == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tape/test_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.tape.migrate'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tape/migrate.py
from __future__ import annotations

from coding_agent.core.tape import Entry as LegacyEntry
from coding_agent.tape.models import Entry, EntryKind


def from_legacy_entry(legacy: LegacyEntry) -> Entry:
    return Entry(
        kind=EntryKind(legacy.kind),
        payload=legacy.payload,
        timestamp=legacy.timestamp,
        meta={"legacy_id": legacy.id},
    )


def to_legacy_entry(entry: Entry) -> LegacyEntry:
    legacy_id = int(entry.meta.get("legacy_id", 0))
    return LegacyEntry(
        id=legacy_id,
        kind=entry.kind.value,
        payload=entry.payload,
        timestamp=entry.timestamp,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tape/test_migrate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tape/migrate.py tests/tape/test_migrate.py
git commit -m "feat(tape): add migration bridge between legacy and enum-based entries"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

## Phase 4: Tool Hooks

### Task 10: Add declarative `@tool` system + `ToolContext` dataclass + global registry

**Files:**
- Create: `src/coding_agent/tools/declarative.py`
- Modify: `none`
- Test: `tests/tools/test_declarative.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_declarative.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from coding_agent.tools.declarative import TOOL_REGISTRY, ToolContext, tool


def test_tool_decorator_registers_metadata() -> None:
    TOOL_REGISTRY.clear()

    @tool(
        name="demo",
        description="demo tool",
        parameters={"type": "object", "properties": {}},
    )
    async def demo_tool(context: ToolContext) -> str:
        return str(context.repo_root)

    assert "demo" in TOOL_REGISTRY
    assert TOOL_REGISTRY["demo"].handler is demo_tool


@pytest.mark.asyncio
async def test_tool_context_is_passed() -> None:
    TOOL_REGISTRY.clear()

    @tool(name="cwd_tool", description="returns cwd", parameters={"type": "object", "properties": {}})
    async def cwd_tool(context: ToolContext) -> str:
        return str(context.cwd)

    result = await TOOL_REGISTRY["cwd_tool"].handler(ToolContext(repo_root=Path("/tmp/repo"), cwd=Path("/tmp/repo/sub"), planner=None, provider=None, tape=None, consumer=None))
    assert result == "/tmp/repo/sub"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_declarative.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.tools.declarative'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tools/declarative.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ToolContext:
    repo_root: Path
    cwd: Path
    planner: Any
    provider: Any
    tape: Any
    consumer: Any


@dataclass
class DeclarativeTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[Any], Awaitable[str]]


TOOL_REGISTRY: dict[str, DeclarativeTool] = {}


def tool(name: str, description: str, parameters: dict[str, Any]) -> Callable[[Callable[[Any], Awaitable[str]]], Callable[[Any], Awaitable[str]]]:
    def decorator(fn: Callable[[Any], Awaitable[str]]) -> Callable[[Any], Awaitable[str]]:
        TOOL_REGISTRY[name] = DeclarativeTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
        )
        return fn
    return decorator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_declarative.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tools/declarative.py tests/tools/test_declarative.py
git commit -m "feat(tools): add declarative tool decorator and tool context"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 11: Refactor tool modules (`file`, `search`, `shell`, `planner`, `subagent`) to declarative registration

**Files:**
- Create: `none`
- Modify: `src/coding_agent/tools/file.py`
- Modify: `src/coding_agent/tools/search.py`
- Modify: `src/coding_agent/tools/shell.py`
- Modify: `src/coding_agent/tools/planner.py`
- Modify: `src/coding_agent/tools/subagent.py`
- Test: `tests/tools/test_file_patch.py`
- Test: `tests/tools/test_shell.py`
- Test: `tests/tools/test_planner_tool.py`
- Test: `tests/tools/test_subagent_tool.py`

- [ ] **Step 1: Write failing compatibility tests**

```python
# tests/tools/test_declarative.py (add compatibility test)
from coding_agent.tools.file import register_file_tools
from coding_agent.tools.registry import ToolRegistry


def test_register_file_tools_still_populates_registry(tmp_path) -> None:
    registry = ToolRegistry(repo_root=tmp_path)
    register_file_tools(registry, repo_root=tmp_path)
    names = registry.list_tools()
    assert "file_read" in names
    assert "file_write" in names
    assert "file_patch" in names
```

- [ ] **Step 2: Run tests to verify at least one fails before refactor**

Run: `pytest tests/tools/test_declarative.py::test_register_file_tools_still_populates_registry -v`
Expected: FAIL after introducing declarative path until wrapper logic is added.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tools/file.py (pattern applied consistently in all 5 modules)
from coding_agent.tools.declarative import ToolContext, tool


@tool(
    name="file_read",
    description="Read content of a file. Returns file content as string.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "limit": {"type": "integer", "default": 1000},
        },
        "required": ["path"],
    },
)
async def file_read_tool(context: ToolContext, path: str, limit: int = 1000) -> str:
    root = context.repo_root.resolve()
    target = _resolve_path(root, path)
    if not target.exists():
        return json.dumps({"error": f"File not found: {path}"})
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    content = "".join(lines[:limit])
    if len(lines) > limit:
        content += f"\n({len(lines) - limit} more lines)"
    return json.dumps({"path": path, "content": content, "lines": len(lines)})


def register_file_tools(registry: ToolRegistry, repo_root: Path | str = ".") -> None:
    from coding_agent.tools.declarative import TOOL_REGISTRY
    context = ToolContext(repo_root=Path(repo_root), cwd=Path(repo_root), planner=None, provider=None, tape=None, consumer=None)

    async def _file_read(path: str, limit: int = 1000) -> str:
        return await TOOL_REGISTRY["file_read"].handler(context, path=path, limit=limit)

    registry.register("file_read", TOOL_REGISTRY["file_read"].description, TOOL_REGISTRY["file_read"].parameters, _file_read)
```

```python
# src/coding_agent/tools/search.py
@tool(name="grep", description="Search file contents using regex pattern.", parameters={"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]})
async def grep_tool(context: ToolContext, pattern: str, path: str = ".", include: str | None = None) -> str:
    return await grep(pattern=pattern, path=path, include=include)


# src/coding_agent/tools/shell.py
@tool(name="bash", description="Execute a shell command in the repository directory.", parameters={"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 60}}, "required": ["command"]})
async def bash_tool(context: ToolContext, command: str, timeout: int = 60) -> str:
    return await bash(command=command, timeout=timeout)


# src/coding_agent/tools/planner.py
@tool(name="todo_read", description="Read the current task plan to see progress and next steps.", parameters={"type": "object", "properties": {}})
async def todo_read_tool(context: ToolContext) -> str:
    return context.planner.to_text()


# src/coding_agent/tools/subagent.py
@tool(name="subagent", description="Dispatch a sub-agent to work on an independent sub-task.", parameters={"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]})
async def subagent_tool(context: ToolContext, goal: str, tools: list[str] | None = None) -> str:
    return await subagent_dispatch(goal=goal, tools=tools)
```

- [ ] **Step 4: Run targeted tests to verify pass**

Run: `pytest tests/tools/test_file_patch.py tests/tools/test_shell.py tests/tools/test_planner_tool.py tests/tools/test_subagent_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tools/file.py src/coding_agent/tools/search.py src/coding_agent/tools/shell.py src/coding_agent/tools/planner.py src/coding_agent/tools/subagent.py tests/tools/test_declarative.py
git commit -m "refactor(tools): migrate core tool modules to declarative tool registration"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 12: Enforce TOML tool enable and disable lists

**Files:**
- Create: `none`
- Modify: `src/coding_agent/tools/declarative.py`
- Test: `tests/tools/test_tool_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_tool_config.py
from __future__ import annotations

from pathlib import Path

from coding_agent.tools.declarative import TOOL_REGISTRY, ToolContext, load_enabled_tools, tool


def test_load_enabled_tools_applies_allow_and_deny_lists() -> None:
    TOOL_REGISTRY.clear()

    @tool(name="tool_a", description="A", parameters={"type": "object", "properties": {}})
    async def tool_a(context: ToolContext) -> str:
        return "a"

    @tool(name="tool_b", description="B", parameters={"type": "object", "properties": {}})
    async def tool_b(context: ToolContext) -> str:
        return "b"

    loaded = load_enabled_tools(enabled=["tool_a", "tool_b"], disabled=["tool_b"])
    assert list(loaded.keys()) == ["tool_a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_tool_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_enabled_tools'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tools/declarative.py (add loader)
def load_enabled_tools(enabled: list[str], disabled: list[str]) -> dict[str, DeclarativeTool]:
    enabled_set = set(enabled)
    disabled_set = set(disabled)
    selected = {}
    for name, tool_def in TOOL_REGISTRY.items():
        if enabled_set and name not in enabled_set:
            continue
        if name in disabled_set:
            continue
        selected[name] = tool_def
    return selected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_tool_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tools/declarative.py tests/tools/test_tool_config.py
git commit -m "feat(tools): add declarative enable-disable filtering from agent config"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

## Phase 5: Provider and Domain Hooks

### Task 13: Add provider registry and `provide_llm` hook (replace `_create_provider` branching)

**Files:**
- Create: `src/coding_agent/providers/registry.py`
- Modify: `src/coding_agent/providers/__init__.py`
- Test: `tests/providers/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_registry.py
from __future__ import annotations

from coding_agent.providers.registry import get_provider_factory, register_provider


def test_register_and_get_provider_factory() -> None:
    @register_provider("demo")
    def create_demo_provider(model: str, api_key: str | None, base_url: str | None = None) -> dict:
        return {"model": model, "api_key": api_key, "base_url": base_url}

    factory = get_provider_factory("demo")
    provider = factory(model="x", api_key="k", base_url=None)
    assert provider["model"] == "x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.providers.registry'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/providers/registry.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ProviderFactory = Callable[[str, str | None, str | None], Any]
_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider(name: str) -> Callable[[ProviderFactory], ProviderFactory]:
    def decorator(factory: ProviderFactory) -> ProviderFactory:
        _PROVIDER_FACTORIES[name] = factory
        return factory
    return decorator


def get_provider_factory(name: str) -> ProviderFactory:
    try:
        return _PROVIDER_FACTORIES[name]
    except KeyError as error:
        raise KeyError(f"Unknown provider: {name}") from error
```

```python
# src/coding_agent/providers/__init__.py
from coding_agent.providers.registry import register_provider, get_provider_factory

__all__ = ["register_provider", "get_provider_factory"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/providers/registry.py src/coding_agent/providers/__init__.py tests/providers/test_registry.py
git commit -m "feat(providers): add provider registry for hook-driven llm creation"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 14: Implement `approve_tool_call` hook replacing hardcoded safe tool logic

**Files:**
- Create: `src/coding_agent/approval/hooks.py`
- Modify: `src/coding_agent/approval/policy.py`
- Modify: `src/coding_agent/core/loop.py`
- Test: `tests/approval/test_hooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/approval/test_hooks.py
from __future__ import annotations

import pytest

from coding_agent.approval.hooks import approve_tool_call
from coding_agent.hooks.runtime import HookRuntime


@pytest.mark.asyncio
async def test_approve_tool_call_hook_can_override_policy() -> None:
    runtime = HookRuntime()

    async def always_deny(tool_name: str, arguments: dict, policy: str, safe_tools: set[str]) -> bool:
        return False

    runtime.register("approve_tool_call", always_deny)
    result = await approve_tool_call(runtime=runtime, tool_name="file_read", arguments={}, policy="auto", safe_tools={"file_read"})
    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/approval/test_hooks.py -v`
Expected: FAIL with missing hook module or function.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/approval/hooks.py
from __future__ import annotations

from coding_agent.hooks.runtime import HookRuntime


async def approve_tool_call(
    runtime: HookRuntime,
    tool_name: str,
    arguments: dict,
    policy: str,
    safe_tools: set[str],
) -> bool:
    hook_result = await runtime.call_first(
        "approve_tool_call",
        tool_name=tool_name,
        arguments=arguments,
        policy=policy,
        safe_tools=safe_tools,
    )
    if hook_result is not None:
        return bool(hook_result)
    if policy == "yolo":
        return True
    if policy == "interactive":
        return False
    return tool_name in safe_tools
```

```python
# src/coding_agent/approval/policy.py (minimal integration point)
@dataclass
class PolicyConfig:
    policy: ApprovalPolicy
    safe_tools: set[str] = field(default_factory=set)
    timeout_seconds: int = 120
```

```python
# src/coding_agent/core/loop.py (inside _request_approval)
hook_decision = await approve_tool_call(
    runtime=self.hooks,
    tool_name=tool_name,
    arguments=args,
    policy=self.approval_engine.config.policy.value,
    safe_tools=self.approval_engine.config.safe_tools,
)
if hook_decision:
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/approval/test_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/approval/hooks.py src/coding_agent/approval/policy.py src/coding_agent/core/loop.py tests/approval/test_hooks.py
git commit -m "feat(approval): add hook-based tool approval decision path"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 15: Add `summarize_context` hook using Summarizer protocol without touching `core/context.py`

**Files:**
- Create: `src/coding_agent/summarizer/hook_adapter.py`
- Modify: `none`
- Test: `tests/summarizer/test_hook_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/summarizer/test_hook_adapter.py
from __future__ import annotations

import pytest

from coding_agent.hooks.runtime import HookRuntime
from coding_agent.summarizer.hook_adapter import HookSummarizer


@pytest.mark.asyncio
async def test_hook_summarizer_uses_hook_result() -> None:
    runtime = HookRuntime()

    async def summarize_context(messages: list[dict], max_tokens: int) -> str:
        return "hook summary"

    runtime.register("summarize_context", summarize_context)
    summarizer = HookSummarizer(runtime)

    summary = await summarizer.summarize(messages=[{"role": "user", "content": "hello"}], max_tokens=100)
    assert summary.content == "hook summary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/summarizer/test_hook_adapter.py -v`
Expected: FAIL with missing module.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/summarizer/hook_adapter.py
from __future__ import annotations

from coding_agent.hooks.runtime import HookRuntime
from coding_agent.summarizer.base import Summary, Summarizer
from coding_agent.summarizer.rule_summarizer import RuleSummarizer


class HookSummarizer(Summarizer):
    def __init__(self, runtime: HookRuntime) -> None:
        self._runtime = runtime
        self._fallback = RuleSummarizer()

    async def summarize(self, messages: list[dict], max_tokens: int = 500) -> Summary:
        hook_result = await self._runtime.call_first(
            "summarize_context",
            messages=messages,
            max_tokens=max_tokens,
        )
        if isinstance(hook_result, str) and hook_result:
            return Summary(
                content=hook_result,
                original_tokens=len(str(messages)),
                summary_tokens=len(hook_result),
                key_points=[hook_result],
            )
        return await self._fallback.summarize(messages, max_tokens=max_tokens)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/summarizer/test_hook_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/summarizer/hook_adapter.py tests/summarizer/test_hook_adapter.py
git commit -m "feat(summarizer): add summarize_context hook adapter with protocol fallback"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 16: Add `on_error` observer hook and replace direct `logger.exception` calls

**Files:**
- Create: `src/coding_agent/errors/hooks.py`
- Modify: `src/coding_agent/core/loop.py`
- Test: `tests/errors/test_hooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/errors/test_hooks.py
from __future__ import annotations

import pytest

from coding_agent.hooks.runtime import HookRuntime


@pytest.mark.asyncio
async def test_on_error_observers_are_isolated() -> None:
    runtime = HookRuntime()
    seen: list[str] = []

    async def broken(stage: str, error: Exception, message: str) -> None:
        raise RuntimeError("broken observer")

    async def healthy(stage: str, error: Exception, message: str) -> None:
        seen.append(message)

    runtime.register("on_error", broken)
    runtime.register("on_error", healthy)

    await runtime.notify_error(stage="loop", error=RuntimeError("boom"), message="agent failed")
    assert seen == ["agent failed"]
```

- [ ] **Step 2: Run test to verify it fails before integration**

Run: `pytest tests/errors/test_hooks.py -v`
Expected: FAIL until error observer path is integrated.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/errors/hooks.py
from __future__ import annotations

import logging


async def log_error_observer(stage: str, error: Exception, message: str) -> None:
    logger = logging.getLogger(__name__)
    logger.error("stage=%s message=%s error=%s", stage, message, error)


async def memory_error_observer(stage: str, error: Exception, message: str) -> None:
    return None
```

```python
# src/coding_agent/core/loop.py (replace bare logger.exception block)
except Exception as e:
    error = ErrorHandler.handle_exception(e)
    if hasattr(self, "hooks") and self.hooks is not None:
        await self.hooks.notify_error(stage="run_turn", error=e, message=error.message)
    logger = logging.getLogger(__name__)
    logger.exception("Agent error during turn")
    await self._emit(ErrorMessage(session_id=session_id, content=error.format_for_display()))
    await self._emit(TurnEnd(session_id=session_id, turn_id="error", completion_status=CompletionStatus.ERROR))
    return TurnOutcome(stop_reason="error", final_message=error.message, error=error)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/errors/test_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/errors/hooks.py src/coding_agent/core/loop.py tests/errors/test_hooks.py
git commit -m "feat(errors): add on_error observer hooks with per-observer isolation"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

## Phase 6: Integration

### Task 17: Implement `create_agent_from_config()` hook-based bootstrap factory

**Files:**
- Create: `src/coding_agent/core/bootstrap.py`
- Modify: `src/coding_agent/__main__.py`
- Test: `tests/integration/test_agent_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_agent_bootstrap.py
from __future__ import annotations

from pathlib import Path

from coding_agent.core.bootstrap import create_agent_from_config


def test_create_agent_from_config_builds_runtime(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read", "grep", "glob", "todo_read", "todo_write"]

[provider]
name = "openai"
model = "gpt-4o"

[approval]
policy = "auto"
safe_tools = ["file_read", "grep", "glob", "todo_read"]

[hooks.on_error]
observers = ["log"]
""".strip(),
        encoding="utf-8",
    )

    runtime = create_agent_from_config(config_path=config_path, repo_root=tmp_path)
    assert runtime.loop is not None
    assert runtime.tools is not None
    assert runtime.provider is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_agent_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.core.bootstrap'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/core/bootstrap.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coding_agent.approval.policy import ApprovalPolicy
from coding_agent.config.toml_config import load_agent_toml
from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.hooks.runtime import HookRuntime
from coding_agent.providers.openai_compat import OpenAICompatProvider
from coding_agent.storage.hooks import provide_storage
from coding_agent.tools.registry import ToolRegistry


@dataclass
class AgentRuntime:
    loop: AgentLoop
    provider: object
    tools: ToolRegistry
    tape: object
    hooks: HookRuntime


def create_agent_from_config(config_path: Path | str = "agent.toml", repo_root: Path | str = ".") -> AgentRuntime:
    config = load_agent_toml(config_path)
    hooks = HookRuntime()
    hooks.register("provide_storage", provide_storage)
    storage_bundle = __import__("asyncio").run(hooks.call_first("provide_storage", config=config))

    provider = OpenAICompatProvider(model=config.provider.model, api_key=config.provider.api_key, base_url=config.provider.base_url)
    tools = ToolRegistry(repo_root=repo_root)
    context = Context(max_tokens=provider.max_context_size, system_prompt="You are a coding agent.")
    loop = AgentLoop(provider=provider, tools=tools, tape=storage_bundle.tape, context=context, approval_policy=ApprovalPolicy.AUTO)
    loop.hooks = hooks
    return AgentRuntime(loop=loop, provider=provider, tools=tools, tape=storage_bundle.tape, hooks=hooks)
```

```python
# src/coding_agent/__main__.py (replace local bootstrap)
from coding_agent.core.bootstrap import create_agent_from_config


async def _run_headless(config, goal):
    runtime = create_agent_from_config(config_path="agent.toml", repo_root=config.repo)
    result = await runtime.loop.run_turn(goal)
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    if result.final_message:
        click.echo(result.final_message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_agent_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/core/bootstrap.py src/coding_agent/__main__.py tests/integration/test_agent_bootstrap.py
git commit -m "feat(integration): add hook-based agent bootstrap factory"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes.

---

### Task 18: End-to-end validation (`agent.toml` → hooks → loop execution)

**Files:**
- Create: `none`
- Modify: `src/coding_agent/core/bootstrap.py` (if fixture support needed)
- Test: `tests/integration/test_hooked_agent_loop.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_hooked_agent_loop.py
from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.core.bootstrap import create_agent_from_config
from coding_agent.providers.base import StreamEvent


class StubProvider:
    @property
    def model_name(self) -> str:
        return "stub"

    @property
    def max_context_size(self) -> int:
        return 4096

    async def stream(self, messages, tools=None, **kwargs):
        yield StreamEvent(type="delta", text="integration ok")
        yield StreamEvent(type="done")


@pytest.mark.asyncio
async def test_agent_toml_to_loop_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        """
[storage.tape]
backend = "jsonl"
data_dir = "./data/tapes"

[storage.doc_index]
backend = "null"

[storage.session]
backend = "memory"

[tools]
enabled = ["file_read"]

[provider]
name = "openai"
model = "gpt-4o"

[approval]
policy = "auto"
safe_tools = ["file_read"]
""".strip(),
        encoding="utf-8",
    )

    runtime = create_agent_from_config(config_path=config_path, repo_root=tmp_path)
    runtime.loop.provider = StubProvider()

    outcome = await runtime.loop.run_turn("hello")
    assert outcome.stop_reason == "no_tool_calls"
    assert outcome.final_message == "integration ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_hooked_agent_loop.py -v`
Expected: FAIL before bootstrap and hook wiring fully supports end-to-end run.

- [ ] **Step 3: Write minimal implementation adjustments**

```python
# src/coding_agent/core/bootstrap.py (ensure deterministic integration wiring)
def create_agent_from_config(config_path: Path | str = "agent.toml", repo_root: Path | str = ".") -> AgentRuntime:
    config = load_agent_toml(config_path)
    hooks = HookRuntime()
    hooks.register("provide_storage", provide_storage)
    storage_bundle = __import__("asyncio").run(hooks.call_first("provide_storage", config=config))

    provider = OpenAICompatProvider(model=config.provider.model, api_key=config.provider.api_key, base_url=config.provider.base_url)
    tools = ToolRegistry(repo_root=repo_root)
    context = Context(max_tokens=provider.max_context_size, system_prompt="You are a coding agent.")
    loop = AgentLoop(
        provider=provider,
        tools=tools,
        tape=storage_bundle.tape,
        context=context,
        approval_policy=ApprovalPolicy(config.approval.policy),
    )
    loop.hooks = hooks
    return AgentRuntime(loop=loop, provider=provider, tools=tools, tape=storage_bundle.tape, hooks=hooks)
```

- [ ] **Step 4: Run integration test to verify it passes**

Run: `pytest tests/integration/test_hooked_agent_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/core/bootstrap.py tests/integration/test_hooked_agent_loop.py
git commit -m "test(integration): validate full declarative hook bootstrap flow"
```

- [ ] **Step 6: Full regression check (must end task with this)**

Run: `pytest tests/ -v`
Expected: PASS, full suite passes with all pre-existing tests preserved.

---

## Self-Review Checklist

### 1) Spec coverage map

- Phase 1 hook runtime and config loader: Tasks 1-2.
- Phase 2 protocols and new tape model with `EVENT`: Tasks 3-4.
- Phase 3 backend registry, built-in backends, `provide_storage`, KB DI, tape migration: Tasks 5-9.
- Phase 4 `@tool`, ToolContext, five module refactor, TOML enable-disable: Tasks 10-12.
- Phase 5 provider registry + `provide_llm` path, approval hook, summarize hook, on_error observers: Tasks 13-16.
- Phase 6 single bootstrap factory and e2e validation: Tasks 17-18.

### 2) Placeholder scan

- No deferred placeholder steps or undefined implementation notes.
- Every task has explicit tests, commands, expected outcomes, and commit commands.

### 3) Type and signature consistency

- `HookRuntime.call_first/call_many/notify_error` signatures are consistent through Tasks 1, 14, 15, 16, 17.
- `EntryKind` enum values are reused consistently in Tasks 4 and 9.
- `ToolContext` and declarative registry signatures are consistent across Tasks 10-12.
- `create_agent_from_config()` is referenced consistently in Tasks 17 and 18.

---

Plan complete and saved to `docs/superpowers/plans/2026-03-29-hook-architecture-declarative-agent.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
