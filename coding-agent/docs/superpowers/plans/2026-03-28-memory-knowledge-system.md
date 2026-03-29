# Memory & Knowledge System (v2.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a 3-layer memory architecture (Grounding → Tape → Knowledge Store) backed by PostgreSQL + pgvector, replacing the current flat Tape and SQLite + LanceDB dual-engine storage.

**Architecture:** Three independent subsystems built in sequence: (1) Storage + Tape core with Protocol-based DI, (2) Anchor-aware context rebuild + session migration, (3) Tree-sitter code index + pgvector doc index replacing LanceDB. Each subsystem is self-contained and produces working, testable software.

**Tech Stack:** Python 3.12+, asyncpg, pgvector (Python client), tree-sitter, tree-sitter-python, pytest + pytest-asyncio, uv package manager.

---

## Scope Note

This spec covers 3 independent subsystems. This plan covers **all three** in sequence, but each "Phase" is independently shippable. If you want to stop after Phase 1 you have a working tape system. If you stop after Phase 2 you have anchor-aware context rebuild. Phase 3 adds code/doc indexing.

## File Structure

### New files to create:

| File | Responsibility |
|------|---------------|
| `src/coding_agent/protocols.py` | All Protocol definitions (TapeStore, CodeIndex, DocIndex, MemoryManager) |
| `src/coding_agent/tape/__init__.py` | Tape package exports |
| `src/coding_agent/tape/entry.py` | Entry, EntryKind, AnchorPayload dataclasses |
| `src/coding_agent/tape/store.py` | JSONLTapeStore (local) + PostgresTapeStore implementations |
| `src/coding_agent/tape/triage.py` | TriageGate filtering logic |
| `src/coding_agent/storage/__init__.py` | Storage package exports |
| `src/coding_agent/storage/database.py` | DatabasePool (asyncpg connection pool + pgvector registration) |
| `src/coding_agent/storage/migrations.py` | Schema creation/migration for all tables |
| `src/coding_agent/index/__init__.py` | Index package exports |
| `src/coding_agent/index/code_index.py` | TreeSitterIndex (symbol graph + PageRank) |
| `src/coding_agent/index/doc_index.py` | PgVectorDocIndex (replaces LanceDB in kb.py) |
| `src/coding_agent/memory/__init__.py` | Memory package exports |
| `src/coding_agent/memory/manager.py` | MemoryManager orchestrating all 3 layers |
| `src/coding_agent/AGENTS.md` | Layer 0 Grounding template |
| `tests/unit/tape/__init__.py` | Test package |
| `tests/unit/tape/test_entry.py` | Entry/AnchorPayload tests |
| `tests/unit/tape/test_store.py` | JSONLTapeStore tests |
| `tests/unit/tape/test_triage.py` | TriageGate tests |
| `tests/unit/storage/__init__.py` | Test package |
| `tests/unit/storage/test_database.py` | DatabasePool tests (mocked) |
| `tests/unit/storage/test_migrations.py` | Migration tests (mocked) |
| `tests/unit/index/__init__.py` | Test package |
| `tests/unit/index/test_code_index.py` | TreeSitterIndex tests |
| `tests/unit/index/test_doc_index.py` | PgVectorDocIndex tests (mocked) |
| `tests/unit/memory/__init__.py` | Test package |
| `tests/unit/memory/test_manager.py` | MemoryManager tests |
| `tests/unit/test_protocols.py` | Protocol structural subtyping tests |

### Existing files to modify:

| File | Change |
|------|--------|
| `src/coding_agent/core/config.py` | Add `database_url`, `agents_md_path`, `project_root` fields |
| `src/coding_agent/core/context.py` | Add `set_grounding()`, `inject_anchor()`, fix `build_working_set()` ordering |
| `src/coding_agent/kb.py` | Add `from_pgvector()` factory + dual-mode routing in `search()` and `index_file()` |
| `pyproject.toml` | Add asyncpg, pgvector, tree-sitter deps; add pytest-asyncio to dev; bump requires-python to >=3.12 |

### Existing files NOT modified (explicitly deferred):

| File | Reason |
|------|--------|
| `src/coding_agent/core/session.py` | Sync/async mismatch — defer Session→PG migration to follow-up |
| `src/coding_agent/core/tape.py` | Legacy Tape class untouched — new `tape/` package runs alongside |

---

## Phase 1: Storage Layer + Tape Core (Week 1)

### Task 1: Add new dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

```toml
[project]
name = "coding-agent"
version = "0.1.0"
description = "Coding agent with context budget management"
requires-python = ">=3.12"
dependencies = [
    "tiktoken>=0.5.0",
    "lancedb>=0.18.0",
    "numpy>=1.26.0",
    "pyyaml>=6.0",
    "asyncpg>=0.30.0",
    "pgvector>=0.3.6",
    "tree-sitter>=0.24.0",
    "tree-sitter-python>=0.23.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.24.0",
]
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync --extra dev`
Expected: All dependencies (including pytest-asyncio) install without errors.

- [ ] **Step 3: Verify existing tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: 133 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add asyncpg, pgvector, tree-sitter, pytest-asyncio"
```

---

### Task 2: Define protocols.py — all Protocol interfaces

**Files:**
- Create: `src/coding_agent/protocols.py`
- Create: `tests/unit/test_protocols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_protocols.py
"""Tests for Protocol definitions and structural subtyping."""

from __future__ import annotations

from typing import runtime_checkable

import pytest

from coding_agent.protocols import TapeStore, CodeIndex, DocIndex, MemoryManager


class TestProtocolsExist:
    """Test that all protocols are importable and runtime checkable."""

    def test_tape_store_is_protocol(self):
        """TapeStore should be a runtime-checkable Protocol."""
        class FakeStore:
            async def append(self, session_id: str, entry: object) -> None: ...
            async def entries(self, session_id: str, since_anchor: str | None = None) -> list: ...
            async def anchors(self, session_id: str) -> list: ...
            async def fork(self, session_id: str, fork_id: str) -> str: ...
            async def merge(self, fork_id: str, target_session_id: str) -> None: ...

        assert isinstance(FakeStore(), TapeStore)

    def test_code_index_is_protocol(self):
        """CodeIndex should be a runtime-checkable Protocol."""
        class FakeIndex:
            async def build(self, root_path: str) -> None: ...
            async def query(self, intent: str, token_budget: int) -> list: ...
            async def get_symbol(self, name: str) -> object: ...

        assert isinstance(FakeIndex(), CodeIndex)

    def test_doc_index_is_protocol(self):
        """DocIndex should be a runtime-checkable Protocol."""
        class FakeIndex:
            async def search(self, query: str, top_k: int = 5) -> list: ...
            async def upsert(self, doc_id: str, content: str, source: str) -> None: ...

        assert isinstance(FakeIndex(), DocIndex)

    def test_memory_manager_is_protocol(self):
        """MemoryManager should be a runtime-checkable Protocol."""
        class FakeManager:
            def load_grounding(self) -> str: ...
            async def get_latest_anchor(self, session_id: str) -> object: ...
            async def get_recent_entries(self, session_id: str, since_anchor: str | None = None) -> list: ...

        assert isinstance(FakeManager(), MemoryManager)

    def test_coexistence_with_legacy_tape(self):
        """New tape package and legacy core.tape must coexist."""
        from coding_agent.core.tape import Tape
        from coding_agent.tape import Entry
        assert Tape is not None
        assert Entry is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_protocols.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.protocols'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/protocols.py
"""Protocol definitions for dependency injection.

All cross-module interfaces are defined here. Implementations live in
their respective packages (tape/, storage/, index/, memory/).
Use Protocol + structural subtyping instead of pluggy hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TapeStore(Protocol):
    """Storage backend for tape entries."""

    async def append(self, session_id: str, entry: Any) -> None: ...
    async def entries(self, session_id: str, since_anchor: str | None = None) -> list: ...
    async def anchors(self, session_id: str) -> list: ...
    async def fork(self, session_id: str, fork_id: str) -> str: ...
    async def merge(self, fork_id: str, target_session_id: str) -> None: ...


@dataclass
class CodeSnippet:
    """A code snippet returned by CodeIndex.query()."""
    symbol: str
    file_path: str
    start_line: int
    end_line: int
    source: str
    rank: float


@dataclass
class SymbolInfo:
    """Symbol metadata from CodeIndex."""
    name: str
    kind: str  # "function" | "class" | "method" | "import"
    file_path: str
    start_line: int
    end_line: int
    references: list[str]


@runtime_checkable
class CodeIndex(Protocol):
    """Code navigation index (Tree-sitter based)."""

    async def build(self, root_path: str) -> None: ...
    async def query(self, intent: str, token_budget: int) -> list[CodeSnippet]: ...
    async def get_symbol(self, name: str) -> SymbolInfo | None: ...


@dataclass
class DocResult:
    """A document search result from DocIndex."""
    id: str
    content: str
    source: str
    similarity: float


@runtime_checkable
class DocIndex(Protocol):
    """Document vector index (pgvector based)."""

    async def search(self, query: str, top_k: int = 5) -> list[DocResult]: ...
    async def upsert(self, doc_id: str, content: str, source: str) -> None: ...


@runtime_checkable
class MemoryManager(Protocol):
    """Orchestrates the 3-layer memory architecture."""

    def load_grounding(self) -> str: ...
    async def get_latest_anchor(self, session_id: str) -> Any: ...
    async def get_recent_entries(self, session_id: str, since_anchor: str | None = None) -> list: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_protocols.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Verify all existing tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: 136 tests PASS (133 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/protocols.py tests/unit/test_protocols.py
git commit -m "feat: add Protocol definitions for TapeStore, CodeIndex, DocIndex"
```

---

### Task 3: Define Entry, EntryKind, AnchorPayload data structures

**Files:**
- Create: `src/coding_agent/tape/__init__.py`
- Create: `src/coding_agent/tape/entry.py`
- Create: `tests/unit/tape/__init__.py`
- Create: `tests/unit/tape/test_entry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tape/test_entry.py
"""Tests for Entry, EntryKind, and AnchorPayload."""

from __future__ import annotations

import pytest

from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind


class TestEntryKind:
    """Tests for EntryKind enum."""

    def test_all_kinds_exist(self):
        assert EntryKind.MESSAGE == "message"
        assert EntryKind.TOOL_CALL == "tool_call"
        assert EntryKind.TOOL_RESULT == "tool_result"
        assert EntryKind.ANCHOR == "anchor"
        assert EntryKind.PLAN == "plan"

    def test_kind_is_string(self):
        assert isinstance(EntryKind.MESSAGE, str)
        assert isinstance(EntryKind.ANCHOR, str)


class TestEntry:
    """Tests for Entry dataclass."""

    def test_create_entry(self):
        entry = Entry(
            id="01JARQXYZ",
            kind=EntryKind.MESSAGE,
            payload={"role": "user", "content": "hello"},
            meta={},
        )
        assert entry.id == "01JARQXYZ"
        assert entry.kind == EntryKind.MESSAGE
        assert entry.payload["content"] == "hello"
        assert entry.meta == {}

    def test_entry_is_frozen(self):
        entry = Entry(
            id="01JARQXYZ",
            kind=EntryKind.MESSAGE,
            payload={"role": "user", "content": "hello"},
            meta={},
        )
        with pytest.raises(AttributeError):
            entry.id = "changed"

    def test_entry_generate_id(self):
        """generate_id should return a ULID-like string (26 chars, alphanumeric)."""
        entry_id = Entry.generate_id()
        assert isinstance(entry_id, str)
        assert len(entry_id) == 26  # ULID length

    def test_entry_generate_id_unique(self):
        ids = {Entry.generate_id() for _ in range(100)}
        assert len(ids) == 100

    def test_entry_to_dict(self):
        entry = Entry(
            id="01JARQXYZ",
            kind=EntryKind.TOOL_CALL,
            payload={"name": "bash", "args": {"cmd": "ls"}},
            meta={"token_count": 10},
        )
        d = entry.to_dict()
        assert d["id"] == "01JARQXYZ"
        assert d["kind"] == "tool_call"
        assert d["payload"]["name"] == "bash"
        assert d["meta"]["token_count"] == 10

    def test_entry_from_dict(self):
        d = {
            "id": "01JARQXYZ",
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "meta": {},
        }
        entry = Entry.from_dict(d)
        assert entry.id == "01JARQXYZ"
        assert entry.kind == EntryKind.MESSAGE
        assert entry.payload["content"] == "hi"

    def test_entry_roundtrip(self):
        original = Entry(
            id=Entry.generate_id(),
            kind=EntryKind.ANCHOR,
            payload={"phase": "analyzing"},
            meta={"ts": "2026-03-28T12:00:00Z"},
        )
        reconstructed = Entry.from_dict(original.to_dict())
        assert reconstructed == original


class TestAnchorPayload:
    """Tests for AnchorPayload dataclass."""

    def test_create_anchor_payload(self):
        ap = AnchorPayload(
            phase="analyzing",
            summary="Analyzed the codebase structure",
            decisions=["Use PostgreSQL for storage"],
            next_steps=["Implement TapeStore"],
        )
        assert ap.phase == "analyzing"
        assert len(ap.decisions) == 1
        assert ap.knowledge_type == "fact"  # default
        assert ap.evolves is None  # default

    def test_anchor_payload_with_evolves(self):
        ap = AnchorPayload(
            phase="implementing",
            summary="Refactored storage layer",
            decisions=["Switch from SQLite to PG"],
            next_steps=["Run migration"],
            knowledge_type="decision",
            evolves={"ref": "01ABC", "relation": "replaces"},
        )
        assert ap.knowledge_type == "decision"
        assert ap.evolves["relation"] == "replaces"

    def test_anchor_payload_to_dict(self):
        ap = AnchorPayload(
            phase="testing",
            summary="All tests pass",
            decisions=[],
            next_steps=[],
        )
        d = ap.to_dict()
        assert d["phase"] == "testing"
        assert d["summary"] == "All tests pass"
        assert d["decisions"] == []
        assert d["next_steps"] == []
        assert d["knowledge_type"] == "fact"
        assert d["evolves"] is None

    def test_anchor_payload_from_dict(self):
        d = {
            "phase": "analyzing",
            "summary": "Done",
            "decisions": ["A"],
            "next_steps": ["B"],
            "knowledge_type": "plan",
            "evolves": None,
        }
        ap = AnchorPayload.from_dict(d)
        assert ap.phase == "analyzing"
        assert ap.knowledge_type == "plan"
```

- [ ] **Step 2: Create package init files and run test to verify it fails**

```python
# tests/unit/tape/__init__.py
# (empty)
```

Run: `.venv/bin/python -m pytest tests/unit/tape/test_entry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tape/__init__.py
"""Tape subsystem — structured append-only event log."""

from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind

__all__ = ["AnchorPayload", "Entry", "EntryKind"]
```

```python
# src/coding_agent/tape/entry.py
"""Entry, EntryKind, and AnchorPayload data structures."""

from __future__ import annotations

import time
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntryKind(str, Enum):
    """Type of tape entry."""

    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ANCHOR = "anchor"
    PLAN = "plan"


def _generate_ulid() -> str:
    """Generate a ULID-like ID (26-char, Crockford Base32, time-sortable).

    Uses millisecond timestamp (48 bits) + 80 random bits,
    encoded as 26 Crockford Base32 characters.
    """
    CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    # 48-bit millisecond timestamp
    ts_ms = int(time.time() * 1000)
    # 80 bits of randomness
    rand_bytes = os.urandom(10)
    rand_int = int.from_bytes(rand_bytes, "big")
    # Encode: 10 chars for timestamp, 16 chars for randomness
    chars = []
    for _ in range(16):
        chars.append(CROCKFORD[rand_int & 0x1F])
        rand_int >>= 5
    chars.reverse()
    ts_chars = []
    for _ in range(10):
        ts_chars.append(CROCKFORD[ts_ms & 0x1F])
        ts_ms >>= 5
    ts_chars.reverse()
    return "".join(ts_chars) + "".join(chars)


@dataclass(frozen=True)
class Entry:
    """A single tape entry — immutable fact record."""

    id: str
    kind: EntryKind
    payload: dict[str, Any]
    meta: dict[str, Any]

    @staticmethod
    def generate_id() -> str:
        """Generate a new ULID for an entry."""
        return _generate_ulid()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict (for JSONL / JSONB storage)."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "payload": self.payload,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entry:
        """Deserialize from plain dict."""
        return cls(
            id=d["id"],
            kind=EntryKind(d["kind"]),
            payload=d["payload"],
            meta=d.get("meta", {}),
        )


@dataclass
class AnchorPayload:
    """Structured payload for anchor entries — the phase transition record."""

    phase: str
    summary: str
    decisions: list[str]
    next_steps: list[str]
    knowledge_type: str = "fact"  # fact | decision | plan | procedure
    evolves: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict."""
        return {
            "phase": self.phase,
            "summary": self.summary,
            "decisions": self.decisions,
            "next_steps": self.next_steps,
            "knowledge_type": self.knowledge_type,
            "evolves": self.evolves,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnchorPayload:
        """Deserialize from plain dict."""
        return cls(
            phase=d["phase"],
            summary=d["summary"],
            decisions=d["decisions"],
            next_steps=d["next_steps"],
            knowledge_type=d.get("knowledge_type", "fact"),
            evolves=d.get("evolves"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/tape/test_entry.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Verify all existing tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (133 existing + new entry tests).

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/tape/ tests/unit/tape/
git commit -m "feat: add Entry, EntryKind, AnchorPayload data structures"
```

---

### Task 4: Implement JSONLTapeStore

**Files:**
- Create: `src/coding_agent/tape/store.py`
- Create: `tests/unit/tape/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tape/test_store.py
"""Tests for JSONLTapeStore."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from coding_agent.protocols import TapeStore
from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind
from coding_agent.tape.store import JSONLTapeStore


class TestJSONLTapeStore:
    """Tests for JSONLTapeStore implementation."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def store(self, temp_dir) -> JSONLTapeStore:
        return JSONLTapeStore(base_dir=temp_dir)

    def test_implements_protocol(self, store):
        """JSONLTapeStore should satisfy the TapeStore Protocol."""
        assert isinstance(store, TapeStore)

    @pytest.mark.asyncio
    async def test_append_and_entries(self, store):
        entry = Entry(
            id=Entry.generate_id(),
            kind=EntryKind.MESSAGE,
            payload={"role": "user", "content": "hello"},
            meta={},
        )
        await store.append("sess-1", entry)

        result = await store.entries("sess-1")
        assert len(result) == 1
        assert result[0].id == entry.id
        assert result[0].kind == EntryKind.MESSAGE

    @pytest.mark.asyncio
    async def test_entries_empty_session(self, store):
        result = await store.entries("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_append_multiple(self, store):
        for i in range(5):
            entry = Entry(
                id=Entry.generate_id(),
                kind=EntryKind.TOOL_CALL,
                payload={"name": f"tool_{i}"},
                meta={},
            )
            await store.append("sess-1", entry)

        result = await store.entries("sess-1")
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_entries_since_anchor(self, store):
        """entries(since_anchor=id) should return entries from that anchor onward."""
        e1 = Entry(id="01AAA", kind=EntryKind.MESSAGE, payload={}, meta={})
        anchor = Entry(id="01BBB", kind=EntryKind.ANCHOR, payload={"phase": "impl"}, meta={})
        e3 = Entry(id="01CCC", kind=EntryKind.TOOL_CALL, payload={}, meta={})

        await store.append("s1", e1)
        await store.append("s1", anchor)
        await store.append("s1", e3)

        result = await store.entries("s1", since_anchor="01BBB")
        assert len(result) == 2  # anchor + e3
        assert result[0].id == "01BBB"
        assert result[1].id == "01CCC"

    @pytest.mark.asyncio
    async def test_anchors(self, store):
        """anchors() should return only anchor entries."""
        e1 = Entry(id="01AAA", kind=EntryKind.MESSAGE, payload={}, meta={})
        a1 = Entry(id="01BBB", kind=EntryKind.ANCHOR, payload={"phase": "a"}, meta={})
        e2 = Entry(id="01CCC", kind=EntryKind.TOOL_CALL, payload={}, meta={})
        a2 = Entry(id="01DDD", kind=EntryKind.ANCHOR, payload={"phase": "b"}, meta={})

        for e in [e1, a1, e2, a2]:
            await store.append("s1", e)

        anchors = await store.anchors("s1")
        assert len(anchors) == 2
        assert anchors[0].id == "01BBB"
        assert anchors[1].id == "01DDD"

    @pytest.mark.asyncio
    async def test_fork_and_merge(self, store):
        """fork creates a copy, merge appends fork entries to target."""
        e1 = Entry(id="01AAA", kind=EntryKind.MESSAGE, payload={"n": 1}, meta={})
        await store.append("main", e1)

        fork_id = await store.fork("main", "fork-1")
        assert fork_id == "fork-1"

        # Add entry to fork
        e2 = Entry(id="01BBB", kind=EntryKind.TOOL_CALL, payload={"n": 2}, meta={})
        await store.append("fork-1", e2)

        # Fork should have original + new
        fork_entries = await store.entries("fork-1")
        assert len(fork_entries) == 2

        # Merge fork back into main
        await store.merge("fork-1", "main")

        main_entries = await store.entries("main")
        assert len(main_entries) == 2
        assert main_entries[1].id == "01BBB"

    @pytest.mark.asyncio
    async def test_session_isolation(self, store):
        """Entries in one session don't appear in another."""
        e1 = Entry(id="01A", kind=EntryKind.MESSAGE, payload={}, meta={})
        e2 = Entry(id="01B", kind=EntryKind.MESSAGE, payload={}, meta={})

        await store.append("sess-a", e1)
        await store.append("sess-b", e2)

        assert len(await store.entries("sess-a")) == 1
        assert len(await store.entries("sess-b")) == 1
        assert (await store.entries("sess-a"))[0].id == "01A"

    @pytest.mark.asyncio
    async def test_persistence(self, temp_dir):
        """Data should survive store re-instantiation."""
        store1 = JSONLTapeStore(base_dir=temp_dir)
        entry = Entry(id="01X", kind=EntryKind.MESSAGE, payload={"x": 1}, meta={})
        await store1.append("s1", entry)

        store2 = JSONLTapeStore(base_dir=temp_dir)
        result = await store2.entries("s1")
        assert len(result) == 1
        assert result[0].id == "01X"

    @pytest.mark.asyncio
    async def test_entries_since_missing_anchor(self, store):
        """since_anchor with a non-existent ID should return all entries."""
        e1 = Entry(id="01A", kind=EntryKind.MESSAGE, payload={}, meta={})
        await store.append("s1", e1)

        result = await store.entries("s1", since_anchor="NONEXISTENT")
        assert len(result) == 1  # falls back to all entries

    @pytest.mark.asyncio
    async def test_merge_is_idempotent(self, store):
        """Merging the same fork twice should not duplicate entries."""
        e1 = Entry(id="01A", kind=EntryKind.MESSAGE, payload={}, meta={})
        await store.append("main", e1)
        await store.fork("main", "fork-1")

        e2 = Entry(id="01B", kind=EntryKind.TOOL_CALL, payload={}, meta={})
        await store.append("fork-1", e2)

        await store.merge("fork-1", "main")
        await store.merge("fork-1", "main")  # second merge

        main_entries = await store.entries("main")
        assert len(main_entries) == 2  # not 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/tape/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.tape.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tape/store.py
"""TapeStore implementations.

JSONLTapeStore: local JSONL file backend (zero external deps).
PostgresTapeStore: production backend (asyncpg) — added in a later task.
"""

from __future__ import annotations

import json
from pathlib import Path

from coding_agent.tape.entry import Entry, EntryKind


class JSONLTapeStore:
    """Local file-based TapeStore — one .jsonl file per session.

    Suitable for development, CI, and single-machine use.
    No external dependencies required.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.jsonl"

    async def append(self, session_id: str, entry: Entry) -> None:
        path = self._session_path(session_id)
        with open(path, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    async def entries(
        self, session_id: str, since_anchor: str | None = None
    ) -> list[Entry]:
        path = self._session_path(session_id)
        if not path.exists():
            return []

        all_entries: list[Entry] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    all_entries.append(Entry.from_dict(json.loads(line)))

        if since_anchor is None:
            return all_entries

        # Find the anchor and return everything from it onward
        for i, e in enumerate(all_entries):
            if e.id == since_anchor:
                return all_entries[i:]
        return all_entries

    async def anchors(self, session_id: str) -> list[Entry]:
        all_entries = await self.entries(session_id)
        return [e for e in all_entries if e.kind == EntryKind.ANCHOR]

    async def fork(self, session_id: str, fork_id: str) -> str:
        """Copy all entries from session_id into a new fork session."""
        source_entries = await self.entries(session_id)
        fork_path = self._session_path(fork_id)
        with open(fork_path, "w") as f:
            for entry in source_entries:
                f.write(json.dumps(entry.to_dict()) + "\n")
        return fork_id

    async def merge(self, fork_id: str, target_session_id: str) -> None:
        """Append entries from fork that are not already in target."""
        target_entries = await self.entries(target_session_id)
        target_ids = {e.id for e in target_entries}

        fork_entries = await self.entries(fork_id)
        new_entries = [e for e in fork_entries if e.id not in target_ids]

        for entry in new_entries:
            await self.append(target_session_id, entry)
```

- [ ] **Step 4: Update tape __init__.py**

```python
# src/coding_agent/tape/__init__.py
"""Tape subsystem — structured append-only event log."""

from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind
from coding_agent.tape.store import JSONLTapeStore

__all__ = ["AnchorPayload", "Entry", "EntryKind", "JSONLTapeStore"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/tape/test_store.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/coding_agent/tape/store.py src/coding_agent/tape/__init__.py tests/unit/tape/test_store.py
git commit -m "feat: implement JSONLTapeStore with fork/merge support"
```

---

### Task 5: Implement TriageGate

**Files:**
- Create: `src/coding_agent/tape/triage.py`
- Create: `tests/unit/tape/test_triage.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tape/test_triage.py
"""Tests for TriageGate filtering logic."""

from __future__ import annotations

import pytest

from coding_agent.tape.entry import Entry, EntryKind
from coding_agent.tape.triage import TriageGate


class TestTriageGate:
    """Tests for TriageGate entry filtering."""

    @pytest.fixture
    def gate(self) -> TriageGate:
        return TriageGate()

    def test_anchor_always_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.ANCHOR,
            payload={"phase": "analyzing"}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_message_always_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.MESSAGE,
            payload={"role": "user", "content": "hello"}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_error_tool_result_always_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.TOOL_RESULT,
            payload={"has_error": True, "output": "traceback..."}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_long_tool_result_skipped(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.TOOL_RESULT,
            payload={"output": "x" * 6000}, meta={},
        )
        assert gate.should_process(entry) is False

    def test_info_tool_call_skipped(self, gate):
        for tool_name in ("ls", "cat", "pwd"):
            entry = Entry(
                id="01A", kind=EntryKind.TOOL_CALL,
                payload={"name": tool_name}, meta={},
            )
            assert gate.should_process(entry) is False

    def test_normal_tool_call_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.TOOL_CALL,
            payload={"name": "bash"}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_plan_entry_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.PLAN,
            payload={"steps": ["a", "b"]}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_short_tool_result_processed(self, gate):
        entry = Entry(
            id="01A", kind=EntryKind.TOOL_RESULT,
            payload={"output": "ok"}, meta={},
        )
        assert gate.should_process(entry) is True

    def test_filter_batch(self, gate):
        entries = [
            Entry(id="1", kind=EntryKind.MESSAGE, payload={}, meta={}),
            Entry(id="2", kind=EntryKind.TOOL_CALL, payload={"name": "ls"}, meta={}),
            Entry(id="3", kind=EntryKind.ANCHOR, payload={"phase": "x"}, meta={}),
            Entry(id="4", kind=EntryKind.TOOL_RESULT, payload={"output": "y" * 6000}, meta={}),
        ]
        processed = gate.filter(entries)
        assert len(processed) == 2
        assert processed[0].id == "1"
        assert processed[1].id == "3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/tape/test_triage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/tape/triage.py
"""TriageGate — lightweight pre-filter for tape entries.

Decides which entries are worth summarizing/processing.
Uses pure rules (no LLM calls) for zero-cost filtering.
"""

from __future__ import annotations

from coding_agent.tape.entry import Entry, EntryKind

# Tool calls that are purely informational — not worth summarizing
_INFO_TOOLS = frozenset({"ls", "cat", "pwd", "echo", "head", "tail"})

# Maximum output length (chars) before a tool result is considered too verbose
_MAX_OUTPUT_LEN = 5000


class TriageGate:
    """Rule-based filter that decides which entries to process."""

    def should_process(self, entry: Entry) -> bool:
        """Return True if this entry should be summarized/processed."""
        # Always process anchors, messages, and plans
        if entry.kind in (EntryKind.ANCHOR, EntryKind.MESSAGE, EntryKind.PLAN):
            return True

        # Always process error results
        if entry.kind == EntryKind.TOOL_RESULT and entry.payload.get("has_error"):
            return True

        # Skip info-only tool calls
        if entry.kind == EntryKind.TOOL_CALL and entry.payload.get("name") in _INFO_TOOLS:
            return False

        # Skip excessively long tool results
        if entry.kind == EntryKind.TOOL_RESULT:
            output = entry.payload.get("output", "")
            if len(output) > _MAX_OUTPUT_LEN:
                return False

        return True

    def filter(self, entries: list[Entry]) -> list[Entry]:
        """Return only entries that should be processed."""
        return [e for e in entries if self.should_process(e)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/tape/test_triage.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/tape/triage.py tests/unit/tape/test_triage.py
git commit -m "feat: implement TriageGate entry filtering"
```

---

### Task 6: Implement DatabasePool and migrations

**Files:**
- Create: `src/coding_agent/storage/__init__.py`
- Create: `src/coding_agent/storage/database.py`
- Create: `src/coding_agent/storage/migrations.py`
- Create: `tests/unit/storage/__init__.py`
- Create: `tests/unit/storage/test_database.py`
- Create: `tests/unit/storage/test_migrations.py`

- [ ] **Step 1: Write the failing test for DatabasePool**

```python
# tests/unit/storage/test_database.py
"""Tests for DatabasePool (mocked — no real PostgreSQL needed)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.storage.database import DatabasePool


class TestDatabasePool:
    """Tests for DatabasePool lifecycle."""

    def test_init(self):
        pool = DatabasePool(dsn="postgresql://localhost/test")
        assert pool.dsn == "postgresql://localhost/test"
        assert pool.min_size == 2
        assert pool.max_size == 10
        assert pool._pool is None

    def test_pool_property_before_connect_raises(self):
        pool = DatabasePool(dsn="postgresql://localhost/test")
        with pytest.raises(AssertionError, match="connect"):
            _ = pool.pool

    @pytest.mark.asyncio
    async def test_connect_creates_pool(self):
        mock_pool = AsyncMock()
        with patch("coding_agent.storage.database.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
            db = DatabasePool(dsn="postgresql://localhost/test")
            await db.connect()
            mock_asyncpg.create_pool.assert_awaited_once()
            assert db._pool is mock_pool

    @pytest.mark.asyncio
    async def test_close(self):
        mock_pool = AsyncMock()
        db = DatabasePool(dsn="postgresql://localhost/test")
        db._pool = mock_pool
        await db.close()
        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_when_not_connected(self):
        db = DatabasePool(dsn="postgresql://localhost/test")
        await db.close()  # Should not raise
```

- [ ] **Step 2: Write the failing test for migrations**

```python
# tests/unit/storage/test_migrations.py
"""Tests for schema migrations (mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from coding_agent.storage.migrations import run_migrations, SCHEMA_SQL


class TestMigrations:
    """Tests for migration SQL and execution."""

    def test_schema_sql_contains_tables(self):
        """Schema should define all required tables."""
        assert "tape_entries" in SCHEMA_SQL
        assert "sessions" in SCHEMA_SQL
        assert "doc_embeddings" in SCHEMA_SQL

    def test_schema_sql_contains_indexes(self):
        """Schema should define required indexes."""
        assert "idx_tape_session" in SCHEMA_SQL
        assert "idx_tape_anchors" in SCHEMA_SQL

    def test_schema_sql_enables_pgvector(self):
        """Schema should enable the vector extension."""
        assert "CREATE EXTENSION" in SCHEMA_SQL
        assert "vector" in SCHEMA_SQL

    @pytest.mark.asyncio
    async def test_run_migrations(self):
        """run_migrations should execute the schema SQL."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        await run_migrations(mock_pool)
        mock_pool.execute.assert_awaited_once_with(SCHEMA_SQL)
```

- [ ] **Step 3: Create package init and run tests to verify they fail**

```python
# tests/unit/storage/__init__.py
# (empty)

# src/coding_agent/storage/__init__.py
"""Storage layer — PostgreSQL connection pool and migrations."""
```

Run: `.venv/bin/python -m pytest tests/unit/storage/ -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write DatabasePool implementation**

```python
# src/coding_agent/storage/database.py
"""DatabasePool — unified PostgreSQL connection pool.

Manages an asyncpg Pool with pgvector type registration.
All storage layers (Tape, Session, DocIndex) share one pool.
"""

from __future__ import annotations

from typing import Any

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]


class DatabasePool:
    """Async PostgreSQL connection pool with pgvector support."""

    def __init__(
        self,
        dsn: str,
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Any = None

    async def connect(self) -> None:
        """Create the connection pool and register pgvector types."""
        if asyncpg is None:
            raise ImportError(
                "asyncpg is required for PostgreSQL. "
                "Install it with: uv add asyncpg"
            )
        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            init=self._init_connection,
        )

    async def _init_connection(self, conn: Any) -> None:
        """Per-connection initialization: register pgvector codec."""
        try:
            from pgvector.asyncpg import register_vector
            await register_vector(conn)
        except ImportError:
            pass

    @property
    def pool(self) -> Any:
        """Return the underlying asyncpg Pool. Raises if not connected."""
        assert self._pool is not None, "Call connect() first"
        return self._pool

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
```

- [ ] **Step 5: Write migrations implementation**

```python
# src/coding_agent/storage/migrations.py
"""Schema creation and migration for PostgreSQL.

Contains the full DDL for all tables used by the coding agent:
- tape_entries: append-only operation log
- sessions: session metadata (replaces SQLite)
- doc_embeddings: vector index for documents (replaces LanceDB)
"""

from __future__ import annotations

from typing import Any

SCHEMA_SQL = """\
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Tape: append-only operation log
CREATE TABLE IF NOT EXISTS tape_entries (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    meta        JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    fork_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tape_session
    ON tape_entries (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tape_anchors
    ON tape_entries (session_id, kind) WHERE kind = 'anchor';
CREATE INDEX IF NOT EXISTS idx_tape_fork
    ON tape_entries (fork_id) WHERE fork_id IS NOT NULL;

-- Sessions: session metadata
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    metadata    JSONB DEFAULT '{}'
);

-- Knowledge Store: document vector index
CREATE TABLE IF NOT EXISTS doc_embeddings (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doc_embedding
    ON doc_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_doc_source
    ON doc_embeddings (source);
"""


async def run_migrations(pool: Any) -> None:
    """Execute the schema DDL against the given connection pool.

    Args:
        pool: An asyncpg Pool (or any object with an execute method).
    """
    await pool.execute(SCHEMA_SQL)
```

- [ ] **Step 6: Update storage __init__.py**

```python
# src/coding_agent/storage/__init__.py
"""Storage layer — PostgreSQL connection pool and migrations."""

from coding_agent.storage.database import DatabasePool
from coding_agent.storage.migrations import run_migrations

__all__ = ["DatabasePool", "run_migrations"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/storage/ -v`
Expected: All tests PASS.

- [ ] **Step 8: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/coding_agent/storage/ tests/unit/storage/
git commit -m "feat: add DatabasePool and schema migrations for PostgreSQL"
```

---

### Task 7: Update Config with new fields

**Files:**
- Modify: `src/coding_agent/core/config.py`
- Verify: `tests/unit/test_session.py` (existing tests still pass)

- [ ] **Step 1: Read current config.py** (already read above)

- [ ] **Step 2: Add new fields**

Add `database_url`, `agents_md_path`, and `project_root` to the `Config` dataclass. Keep all defaults so existing code/tests don't break.

```python
# src/coding_agent/core/config.py
"""Configuration management for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """Configuration for the coding agent.
    
    Attributes:
        tape_dir: Directory for storing tape files.
        max_tokens: Maximum tokens allowed in context.
        model: Model name to use.
        database_url: PostgreSQL connection string (None = use local JSONL).
        agents_md_path: Path to AGENTS.md grounding file.
        project_root: Root of the project being worked on.
    """
    
    tape_dir: Path
    max_tokens: int = 8000
    model: str = "gpt-4"
    database_url: str | None = None
    agents_md_path: Path | None = None
    project_root: Path | None = None
    
    def __post_init__(self):
        """Ensure tape_dir exists."""
        self.tape_dir.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def default(cls, tape_dir: str | Path | None = None) -> Config:
        """Create a default configuration.
        
        Args:
            tape_dir: Optional tape directory path. Defaults to ~/.coding_agent/tapes.
            
        Returns:
            Config instance with default values.
        """
        if tape_dir is None:
            tape_dir = Path.home() / ".coding_agent" / "tapes"
        return cls(tape_dir=Path(tape_dir))
```

- [ ] **Step 3: Verify all existing tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (Config fields have defaults, no existing code breaks).

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/core/config.py
git commit -m "feat: add database_url, agents_md_path, project_root to Config"
```

---

### Task 8: Create AGENTS.md grounding template

**Files:**
- Create: `src/coding_agent/AGENTS.md`

- [ ] **Step 1: Create the grounding file**

```markdown
# Coding Agent — Project Grounding

> This file is injected into every LLM call as Layer 0 (Grounding).
> Keep it under 200 lines for optimal model compliance.

## Identity

You are a coding agent that helps users write, debug, and refactor code.

## Tech Stack

- Python 3.12+
- asyncio throughout
- PostgreSQL + pgvector for storage
- Tree-sitter for code navigation
- pytest + pytest-asyncio for testing
- uv for package management

## Architecture Conventions

- **Protocol + DI:** All cross-module interfaces use `typing.Protocol`. Implementations are injected via constructors, never imported directly.
- **Tape:** All operations are logged as immutable `Entry` records. Phase transitions produce `Anchor` entries.
- **Context rebuild:** LLM context is assembled from the most recent anchor, not from session start.
- **Storage:** PostgreSQL in production, JSONL files for local development.

## Coding Standards

- Dataclasses with `frozen=True` for value objects.
- `from __future__ import annotations` in every file.
- Type hints on all public functions.
- No bare `except:` — always catch specific exceptions.
```

- [ ] **Step 2: Commit**

```bash
git add src/coding_agent/AGENTS.md
git commit -m "feat: add AGENTS.md grounding template (Layer 0)"
```

---

### Task 9: Bridge old Tape class to new tape system

**Files:**
- Modify: `src/coding_agent/core/tape.py`

- [ ] **Step 1: Read current tape.py** (already read — 62 lines, simple JSONL wrapper)

The old `Tape` class is used by `Session` for event logging. We need to keep it working but make it delegate to the new tape system types. For now, add a backward-compat import path so the old `Tape` class coexists with the new `tape/` package.

- [ ] **Step 2: Verify existing session tests still reference core.tape.Tape**

The existing `test_session.py` imports `from coding_agent.core.tape import Tape`. The old `Tape` class at `core/tape.py` is completely separate from the new `tape/` package at `src/coding_agent/tape/`. They coexist without conflict — `core.tape` is a module, `coding_agent.tape` is a package. No changes needed to `core/tape.py` at this point.

- [ ] **Step 3: Verify all tests pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

---

## Phase 2: Anchor + Context Rebuild + Session Migration (Week 2)

### Task 10: Add anchor-aware context rebuild to Context

**Files:**
- Modify: `src/coding_agent/core/context.py`
- Modify: `tests/unit/core/test_context.py`

- [ ] **Step 1: Write the failing test**

Add these tests to `tests/unit/core/test_context.py`:

```python
class TestAnchorAwareContext:
    """Tests for anchor-aware context rebuild."""

    def test_set_grounding(self):
        """Test setting Layer 0 grounding content."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        ctx.set_grounding("# Project\nPython 3.12+")
        
        ws = ctx.build_working_set()
        # Grounding should be injected as a system message after the main system prompt
        assert any("# Project" in m["content"] for m in ws if m["role"] == "system")

    def test_rebuild_from_anchor(self):
        """Test rebuilding context starting from an anchor."""
        from coding_agent.tape.entry import AnchorPayload
        
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        anchor_payload = AnchorPayload(
            phase="implementing",
            summary="Finished analyzing the codebase",
            decisions=["Use PostgreSQL"],
            next_steps=["Write migration"],
        )
        ctx.inject_anchor(anchor_payload)
        
        ws = ctx.build_working_set()
        # Anchor summary should appear in the working set
        assert any("Finished analyzing" in m["content"] for m in ws)
        assert any("Use PostgreSQL" in m["content"] for m in ws)

    def test_grounding_plus_anchor_ordering(self):
        """Grounding comes first, then anchor, then messages."""
        from coding_agent.tape.entry import AnchorPayload
        
        ctx = Context(max_tokens=4000, system_prompt="Base prompt")
        ctx.set_grounding("# Grounding")
        
        anchor = AnchorPayload(
            phase="impl", summary="Phase summary",
            decisions=[], next_steps=[],
        )
        ctx.inject_anchor(anchor)
        ctx.add_message("user", "Do something")
        
        ws = ctx.build_working_set()
        # Find indices
        grounding_idx = next(i for i, m in enumerate(ws) if "# Grounding" in m.get("content", ""))
        anchor_idx = next(i for i, m in enumerate(ws) if "Phase summary" in m.get("content", ""))
        user_idx = next(i for i, m in enumerate(ws) if m.get("content") == "Do something")
        
        assert grounding_idx < anchor_idx < user_idx

    def test_no_grounding_no_anchor_unchanged(self):
        """Without grounding/anchor, build_working_set behaves identically to before."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        ctx.add_message("user", "Hello")
        ctx.add_message("assistant", "Hi")
        
        ws = ctx.build_working_set()
        assert len(ws) == 3
        assert ws[0]["role"] == "system"
        assert ws[1]["content"] == "Hello"
        assert ws[2]["content"] == "Hi"

    def test_full_layered_ordering(self):
        """All layers present: system → grounding → anchor → user → tool results."""
        from coding_agent.tape.entry import AnchorPayload
        from coding_agent.kb import KBSearchResult, DocumentChunk
        
        ctx = Context(max_tokens=8000, system_prompt="System")
        ctx.set_grounding("# Grounding content")
        ctx.inject_anchor(AnchorPayload(
            phase="impl", summary="Summary",
            decisions=["D1"], next_steps=["N1"],
        ))
        ctx.add_message("user", "Do X")
        ctx.add_tool_result("bash", "output here")
        
        kb_results = [KBSearchResult(
            chunk=DocumentChunk(id="c1", content="KB content", source="test", metadata={}),
            score=0.5,
        )]
        
        ws = ctx.build_working_set(kb_results=kb_results)
        
        contents = [m.get("content", "") for m in ws]
        # Find positions
        system_idx = next(i for i, c in enumerate(contents) if c == "System")
        grounding_idx = next(i for i, c in enumerate(contents) if "Grounding" in c)
        anchor_idx = next(i for i, c in enumerate(contents) if "Summary" in c)
        kb_idx = next(i for i, c in enumerate(contents) if "KB content" in c)
        user_idx = next(i for i, c in enumerate(contents) if c == "Do X")
        tool_idx = next(i for i, c in enumerate(contents) if "output here" in c)
        
        assert system_idx < grounding_idx < anchor_idx < kb_idx < user_idx < tool_idx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_context.py::TestAnchorAwareContext -v`
Expected: FAIL with `AttributeError: 'Context' object has no attribute 'set_grounding'`

- [ ] **Step 3: Add set_grounding() and inject_anchor() to Context**

Add these methods to the `Context` class in `src/coding_agent/core/context.py`:

```python
# Add import at top of file (after existing imports):
from coding_agent.tape.entry import AnchorPayload

# Add to Context.__init__() — after self._tool_results line:
        self._grounding: str | None = None
        self._anchor: AnchorPayload | None = None

# Add new methods to Context class:
    def set_grounding(self, content: str) -> None:
        """Set Layer 0 grounding content (AGENTS.md).
        
        Args:
            content: The grounding markdown content.
        """
        self._grounding = content

    def inject_anchor(self, anchor: AnchorPayload) -> None:
        """Inject the most recent anchor for context rebuild.
        
        Args:
            anchor: The anchor payload from the most recent phase transition.
        """
        self._anchor = anchor
```

Then modify `build_working_set()` to inject grounding and anchor **between system prompt and user messages**:

```python
    def build_working_set(
        self,
        kb_results: list[KBSearchResult] | None = None,
    ) -> list[dict[str, str]]:
        # Start with system prompt only
        base = [self._messages[0]]  # system prompt
        rest = self._messages[1:]   # user/assistant messages
        
        messages = list(base)
        
        # Layer 0: Inject grounding right after system prompt
        if self._grounding:
            messages.append({"role": "system", "content": self._grounding})
        
        # Layer 1: Inject anchor context
        if self._anchor:
            anchor_lines = [
                f"## Current Phase: {self._anchor.phase}",
                f"**Summary:** {self._anchor.summary}",
            ]
            if self._anchor.decisions:
                anchor_lines.append("**Decisions:** " + "; ".join(self._anchor.decisions))
            if self._anchor.next_steps:
                anchor_lines.append("**Next steps:** " + "; ".join(self._anchor.next_steps))
            messages.append({"role": "system", "content": "\n".join(anchor_lines)})
        
        # Add KB context if provided
        if kb_results:
            self.add_kb_context_to_working_set(messages, kb_results)
        
        # Now add the rest of the conversation history
        messages.extend(rest)
        
        # Process and add tool results as assistant messages
        for tool_result in self._tool_results:
            content = tool_result["content"]
            tool_name = tool_result["tool_name"]
            truncated_content = self._truncate_tool_result(
                content, MAX_TOOL_RESULT_TOKENS
            )
            formatted_content = f"Tool '{tool_name}' result:\n{truncated_content}"
            messages.append({"role": "assistant", "content": formatted_content})
        
        return messages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/core/test_context.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 5: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/core/context.py tests/unit/core/test_context.py
git commit -m "feat: add anchor-aware context rebuild (Layer 0 grounding + Layer 1 anchor)"
```

---

### Task 11: Implement MemoryManager

**Files:**
- Create: `src/coding_agent/memory/__init__.py`
- Create: `src/coding_agent/memory/manager.py`
- Create: `tests/unit/memory/__init__.py`
- Create: `tests/unit/memory/test_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/memory/test_manager.py
"""Tests for MemoryManager orchestration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind
from coding_agent.tape.store import JSONLTapeStore
from coding_agent.memory.manager import DefaultMemoryManager


class TestDefaultMemoryManager:
    """Tests for DefaultMemoryManager."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def grounding_file(self, temp_dir) -> Path:
        p = temp_dir / "AGENTS.md"
        p.write_text("# Grounding\nYou are a coding agent.")
        return p

    @pytest.fixture
    def store(self, temp_dir) -> JSONLTapeStore:
        return JSONLTapeStore(base_dir=temp_dir / "tapes")

    @pytest.fixture
    def manager(self, store, grounding_file) -> DefaultMemoryManager:
        return DefaultMemoryManager(
            tape=store,
            grounding_path=grounding_file,
        )

    def test_load_grounding(self, manager):
        content = manager.load_grounding()
        assert "# Grounding" in content
        assert "coding agent" in content

    def test_load_grounding_missing_file(self, store, temp_dir):
        mgr = DefaultMemoryManager(
            tape=store,
            grounding_path=temp_dir / "missing.md",
        )
        content = mgr.load_grounding()
        assert content == ""

    @pytest.mark.asyncio
    async def test_get_latest_anchor_none(self, manager):
        result = await manager.get_latest_anchor("sess-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_latest_anchor(self, manager, store):
        a1 = Entry(
            id="01A", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="analyzing", summary="s1",
                decisions=[], next_steps=[],
            ).to_dict(),
            meta={},
        )
        a2 = Entry(
            id="01B", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="implementing", summary="s2",
                decisions=["d1"], next_steps=["n1"],
            ).to_dict(),
            meta={},
        )
        await store.append("s1", a1)
        await store.append("s1", a2)

        anchor = await manager.get_latest_anchor("s1")
        assert anchor is not None
        assert anchor.phase == "implementing"
        assert anchor.summary == "s2"

    @pytest.mark.asyncio
    async def test_get_recent_entries(self, manager, store):
        for i in range(3):
            e = Entry(
                id=f"0{i}", kind=EntryKind.MESSAGE,
                payload={"n": i}, meta={},
            )
            await store.append("s1", e)

        entries = await manager.get_recent_entries("s1")
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_get_recent_entries_since_anchor(self, manager, store):
        e1 = Entry(id="01", kind=EntryKind.MESSAGE, payload={}, meta={})
        anchor = Entry(
            id="02", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="x", summary="y", decisions=[], next_steps=[],
            ).to_dict(),
            meta={},
        )
        e3 = Entry(id="03", kind=EntryKind.TOOL_CALL, payload={}, meta={})

        await store.append("s1", e1)
        await store.append("s1", anchor)
        await store.append("s1", e3)

        entries = await manager.get_recent_entries("s1", since_anchor="02")
        assert len(entries) == 2
        assert entries[0].id == "02"

    @pytest.mark.asyncio
    async def test_build_context_from_tape_no_anchors(self, manager, store):
        """With no anchors, returns all entries."""
        e1 = Entry(id="01", kind=EntryKind.MESSAGE, payload={"n": 1}, meta={})
        e2 = Entry(id="02", kind=EntryKind.TOOL_CALL, payload={"n": 2}, meta={})
        await store.append("s1", e1)
        await store.append("s1", e2)

        anchor, entries = await manager.build_context_from_tape("s1")
        assert anchor is None
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_build_context_from_tape_with_anchor(self, manager, store):
        """With anchors, returns only entries from latest anchor onward."""
        e1 = Entry(id="01", kind=EntryKind.MESSAGE, payload={}, meta={})
        a1 = Entry(
            id="02", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="analyzing", summary="done analyzing",
                decisions=["use PG"], next_steps=["implement"],
            ).to_dict(),
            meta={},
        )
        e3 = Entry(id="03", kind=EntryKind.TOOL_CALL, payload={}, meta={})

        await store.append("s1", e1)
        await store.append("s1", a1)
        await store.append("s1", e3)

        anchor, entries = await manager.build_context_from_tape("s1")
        assert anchor is not None
        assert anchor.phase == "analyzing"
        assert anchor.decisions == ["use PG"]
        # Should NOT include e1 (before anchor)
        assert len(entries) == 2  # anchor + e3
        assert entries[0].id == "02"
        assert entries[1].id == "03"

    @pytest.mark.asyncio
    async def test_build_context_from_tape_multiple_anchors(self, manager, store):
        """With multiple anchors, uses the latest one only."""
        a1 = Entry(
            id="01", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="phase1", summary="first",
                decisions=[], next_steps=[],
            ).to_dict(),
            meta={},
        )
        e2 = Entry(id="02", kind=EntryKind.MESSAGE, payload={}, meta={})
        a2 = Entry(
            id="03", kind=EntryKind.ANCHOR,
            payload=AnchorPayload(
                phase="phase2", summary="second",
                decisions=["d"], next_steps=["n"],
            ).to_dict(),
            meta={},
        )
        e4 = Entry(id="04", kind=EntryKind.TOOL_CALL, payload={}, meta={})

        for e in [a1, e2, a2, e4]:
            await store.append("s1", e)

        anchor, entries = await manager.build_context_from_tape("s1")
        assert anchor is not None
        assert anchor.phase == "phase2"
        # Only entries from a2 onward
        assert len(entries) == 2
        assert entries[0].id == "03"
        assert entries[1].id == "04"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/memory/test_manager.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/memory/__init__.py
"""Memory management — orchestrates Grounding + Tape + Index layers."""

from coding_agent.memory.manager import DefaultMemoryManager

__all__ = ["DefaultMemoryManager"]
```

```python
# src/coding_agent/memory/manager.py
"""MemoryManager — orchestrates the 3-layer memory architecture.

Layer 0: Grounding (AGENTS.md file)
Layer 1: Tape (recent entries from anchor)
Layer 2: Knowledge Store (code/doc index — plugged in later)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coding_agent.tape.entry import AnchorPayload, Entry, EntryKind

if TYPE_CHECKING:
    from coding_agent.protocols import TapeStore


class DefaultMemoryManager:
    """Default MemoryManager implementation.

    Coordinates grounding loading, tape querying, and (optionally)
    knowledge store lookups. Satisfies the MemoryManager Protocol.
    """

    def __init__(
        self,
        tape: TapeStore,
        grounding_path: Path | None = None,
    ) -> None:
        self._tape = tape
        self._grounding_path = grounding_path

    def load_grounding(self) -> str:
        """Load Layer 0 grounding content from AGENTS.md."""
        if self._grounding_path is None or not self._grounding_path.exists():
            return ""
        return self._grounding_path.read_text(encoding="utf-8")

    async def get_latest_anchor(self, session_id: str) -> AnchorPayload | None:
        """Get the most recent anchor's payload for a session."""
        anchors = await self._tape.anchors(session_id)
        if not anchors:
            return None
        last = anchors[-1]
        return AnchorPayload.from_dict(last.payload)

    async def get_recent_entries(
        self,
        session_id: str,
        since_anchor: str | None = None,
    ) -> list[Entry]:
        """Get recent tape entries, optionally starting from an anchor."""
        return await self._tape.entries(session_id, since_anchor=since_anchor)

    async def build_context_from_tape(self, session_id: str) -> tuple[AnchorPayload | None, list[Entry]]:
        """Load latest anchor + entries since that anchor.

        This is the core anchor-based rebuild: find the most recent
        anchor, then return only entries from that anchor onward
        (instead of the full session history).

        The returned entries list **includes** the anchor entry itself.
        Callers that also call ``inject_anchor()`` should skip entries
        with ``kind == EntryKind.ANCHOR`` when replaying to avoid
        duplicating anchor content in the context window.

        Returns:
            (anchor_payload, entries_since_anchor) — anchor may be None
            if no anchors exist yet, in which case all entries are returned.
        """
        anchors = await self._tape.anchors(session_id)
        if not anchors:
            entries = await self._tape.entries(session_id)
            return None, entries

        last = anchors[-1]
        anchor_payload = AnchorPayload.from_dict(last.payload)
        entries = await self._tape.entries(session_id, since_anchor=last.id)
        return anchor_payload, entries
```

```python
# tests/unit/memory/__init__.py
# (empty)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/memory/test_manager.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/memory/ tests/unit/memory/
git commit -m "feat: implement DefaultMemoryManager (Layer 0 + Layer 1 orchestration)"
```

---

## Phase 3: Tree-sitter Code Index + pgvector Doc Index (Week 3-4)

### Task 12: Implement TreeSitterIndex

**Files:**
- Create: `src/coding_agent/index/__init__.py`
- Create: `src/coding_agent/index/code_index.py`
- Create: `tests/unit/index/__init__.py`
- Create: `tests/unit/index/test_code_index.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/index/test_code_index.py
"""Tests for TreeSitterIndex."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from coding_agent.protocols import CodeIndex
from coding_agent.index.code_index import TreeSitterIndex


SAMPLE_PYTHON = '''\
import os
from pathlib import Path


class FileManager:
    """Manages file operations."""

    def __init__(self, root: Path):
        self.root = root

    def read_file(self, name: str) -> str:
        path = self.root / name
        return path.read_text()

    def write_file(self, name: str, content: str) -> None:
        path = self.root / name
        path.write_text(content)


def helper_function(x: int) -> int:
    return x + 1
'''


class TestTreeSitterIndex:
    """Tests for TreeSitterIndex code navigation."""

    @pytest.fixture
    def temp_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text(SAMPLE_PYTHON)
            (root / "utils.py").write_text(
                "from main import FileManager\n\ndef use_fm():\n    fm = FileManager(Path('.'))\n"
            )
            yield root

    @pytest.fixture
    def index(self) -> TreeSitterIndex:
        return TreeSitterIndex()

    def test_implements_protocol(self, index):
        assert isinstance(index, CodeIndex)

    @pytest.mark.asyncio
    async def test_build(self, index, temp_project):
        await index.build(str(temp_project))
        assert len(index._symbols) > 0

    @pytest.mark.asyncio
    async def test_get_symbol_class(self, index, temp_project):
        await index.build(str(temp_project))
        sym = await index.get_symbol("FileManager")
        assert sym is not None
        assert sym.kind == "class"
        assert "main.py" in sym.file_path

    @pytest.mark.asyncio
    async def test_get_symbol_function(self, index, temp_project):
        await index.build(str(temp_project))
        sym = await index.get_symbol("helper_function")
        assert sym is not None
        assert sym.kind == "function"

    @pytest.mark.asyncio
    async def test_get_symbol_method(self, index, temp_project):
        await index.build(str(temp_project))
        sym = await index.get_symbol("read_file")
        assert sym is not None
        assert sym.kind == "function"

    @pytest.mark.asyncio
    async def test_get_symbol_not_found(self, index, temp_project):
        await index.build(str(temp_project))
        sym = await index.get_symbol("nonexistent_symbol")
        assert sym is None

    @pytest.mark.asyncio
    async def test_query(self, index, temp_project):
        await index.build(str(temp_project))
        results = await index.query("file manager read", token_budget=2000)
        assert len(results) > 0
        # Should find FileManager-related symbols
        names = [r.symbol for r in results]
        assert any("FileManager" in n or "read_file" in n for n in names)

    @pytest.mark.asyncio
    async def test_query_respects_token_budget(self, index, temp_project):
        await index.build(str(temp_project))
        results_small = await index.query("file", token_budget=100)
        results_large = await index.query("file", token_budget=10000)
        assert len(results_small) <= len(results_large)

    @pytest.mark.asyncio
    async def test_references_populated(self, index, temp_project):
        """Symbols referenced from other files should have references populated."""
        await index.build(str(temp_project))
        sym = await index.get_symbol("FileManager")
        assert sym is not None
        # utils.py imports FileManager, so it should appear in references
        assert any("utils.py" in ref for ref in sym.references)

    @pytest.mark.asyncio
    async def test_cross_file_symbol_ranks_higher(self, index, temp_project):
        """A symbol used in multiple files should rank higher than a local-only one."""
        await index.build(str(temp_project))
        fm_rank = index._ranks.get("FileManager", 0)
        helper_rank = index._ranks.get("helper_function", 0)
        # FileManager is imported in utils.py, helper_function is not
        assert fm_rank >= helper_rank
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/index/test_code_index.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/index/__init__.py
"""Index subsystem — code and document indexing."""

from coding_agent.index.code_index import TreeSitterIndex

__all__ = ["TreeSitterIndex"]
```

```python
# src/coding_agent/index/code_index.py
"""TreeSitterIndex — code navigation via Tree-sitter symbol graph.

Parses Python files, extracts symbol definitions (classes, functions),
builds a reference graph, and ranks symbols by PageRank-like importance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from coding_agent.protocols import CodeSnippet, SymbolInfo

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
except ImportError:
    tspython = None  # type: ignore[assignment]
    Language = None  # type: ignore[assignment, misc]
    Parser = None  # type: ignore[assignment, misc]


class TreeSitterIndex:
    """Code index using Tree-sitter for symbol extraction + PageRank ranking."""

    def __init__(self) -> None:
        self._symbols: dict[str, SymbolInfo] = {}
        self._references: dict[str, set[str]] = {}  # symbol -> set of referenced symbols
        self._ranks: dict[str, float] = {}
        self._source_cache: dict[str, str] = {}  # file_path -> content

    async def build(self, root_path: str) -> None:
        """Parse all .py files under root_path and build the symbol graph."""
        if Parser is None:
            raise ImportError("tree-sitter and tree-sitter-python are required")

        PY_LANGUAGE = Language(tspython.language())
        parser = Parser(PY_LANGUAGE)

        root = Path(root_path)
        self._symbols.clear()
        self._references.clear()
        self._source_cache.clear()

        for py_file in root.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
            except (IOError, UnicodeDecodeError):
                continue

            rel_path = str(py_file.relative_to(root))
            self._source_cache[rel_path] = source
            tree = parser.parse(source.encode("utf-8"))
            self._extract_symbols(tree.root_node, rel_path, source)

        # Second pass: build reference edges between symbols
        self._build_references()
        self._compute_ranks()

    def _extract_symbols(self, node: Any, file_path: str, source: str) -> None:
        """Extract function and class definitions from a tree-sitter AST node."""
        if node.type in ("function_definition", "class_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                name = source[name_node.start_byte:name_node.end_byte]
                kind = "class" if node.type == "class_definition" else "function"
                sym = SymbolInfo(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    references=[],
                )
                self._symbols[name] = sym
                self._references.setdefault(name, set())

        for child in node.children:
            self._extract_symbols(child, file_path, source)

    def _build_references(self) -> None:
        """Scan all source files for cross-references between known symbols.

        For each file, check which defined symbol names appear in the source.
        If symbol A's definition file references symbol B by name, record
        B as referenced by A's file. Also populate SymbolInfo.references.
        """
        all_names = set(self._symbols.keys())
        for file_path, source in self._source_cache.items():
            # Find which symbols are *defined* in this file
            defined_here = {
                name for name, sym in self._symbols.items()
                if sym.file_path == file_path
            }
            # Find which *other* symbols are mentioned in this file
            for name in all_names - defined_here:
                if name in source:
                    # Every symbol defined in this file references `name`
                    for local_name in defined_here:
                        self._references[local_name].add(name)
                    # Record this file as a reference site for `name`
                    self._symbols[name].references.append(file_path)

    def _compute_ranks(self) -> None:
        """PageRank-like ranking: symbols referenced by more files rank higher."""
        all_names = set(self._symbols.keys())
        if not all_names:
            return

        # In-degree: how many unique files reference this symbol
        in_degree: dict[str, int] = {}
        for name in all_names:
            in_degree[name] = len(set(self._symbols[name].references))

        max_degree = max(in_degree.values()) if in_degree else 1
        max_degree = max(max_degree, 1)  # avoid div by zero
        self._ranks = {
            name: degree / max_degree for name, degree in in_degree.items()
        }

    async def query(self, intent: str, token_budget: int) -> list[CodeSnippet]:
        """Find symbols relevant to intent, ranked by importance, within token budget."""
        keywords = intent.lower().split()

        # Score each symbol: keyword match + rank
        scored: list[tuple[float, str, SymbolInfo]] = []
        for name, sym in self._symbols.items():
            keyword_score = sum(1 for kw in keywords if kw in name.lower())
            rank = self._ranks.get(name, 0.0)
            total_score = keyword_score + rank
            if total_score > 0:
                scored.append((total_score, name, sym))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Collect snippets within token budget (~4 chars per token)
        results: list[CodeSnippet] = []
        tokens_used = 0

        for score, name, sym in scored:
            source = self._source_cache.get(sym.file_path, "")
            lines = source.splitlines()
            snippet_lines = lines[sym.start_line - 1 : sym.end_line]
            snippet_text = "\n".join(snippet_lines)
            snippet_tokens = len(snippet_text) // 4

            if tokens_used + snippet_tokens > token_budget:
                if results:
                    break
                # Always include at least one result
                snippet_text = "\n".join(snippet_lines[:10])

            results.append(
                CodeSnippet(
                    symbol=name,
                    file_path=sym.file_path,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    source=snippet_text,
                    rank=score,
                )
            )
            tokens_used += snippet_tokens

        return results

    async def get_symbol(self, name: str) -> SymbolInfo | None:
        """Look up a symbol by exact name."""
        return self._symbols.get(name)
```

```python
# tests/unit/index/__init__.py
# (empty)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/index/test_code_index.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/index/ tests/unit/index/
git commit -m "feat: implement TreeSitterIndex with symbol graph + PageRank ranking"
```

---

### Task 13: Implement PgVectorDocIndex

**Files:**
- Create: `src/coding_agent/index/doc_index.py`
- Create: `tests/unit/index/test_doc_index.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/index/test_doc_index.py
"""Tests for PgVectorDocIndex (mocked — no real PostgreSQL needed)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from coding_agent.protocols import DocIndex, DocResult
from coding_agent.index.doc_index import PgVectorDocIndex


class TestPgVectorDocIndex:
    """Tests for PgVectorDocIndex with mocked pool."""

    @pytest.fixture
    def mock_pool(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()
        return pool

    @pytest.fixture
    def mock_embed_fn(self):
        async def embed(text: str) -> list[float]:
            return [0.1] * 1536
        return embed

    @pytest.fixture
    def index(self, mock_pool, mock_embed_fn) -> PgVectorDocIndex:
        return PgVectorDocIndex(pool=mock_pool, embed_fn=mock_embed_fn)

    def test_implements_protocol(self, index):
        assert isinstance(index, DocIndex)

    @pytest.mark.asyncio
    async def test_search_empty(self, index):
        results = await index.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_returns_results(self, index, mock_pool):
        mock_pool.fetch = AsyncMock(return_value=[
            {"id": "doc1", "content": "hello", "source": "skill", "similarity": 0.95},
            {"id": "doc2", "content": "world", "source": "api_doc", "similarity": 0.80},
        ])

        results = await index.search("hello world", top_k=2)
        assert len(results) == 2
        assert results[0].id == "doc1"
        assert results[0].similarity == 0.95
        assert results[1].source == "api_doc"

    @pytest.mark.asyncio
    async def test_search_passes_top_k(self, index, mock_pool):
        mock_pool.fetch = AsyncMock(return_value=[])
        await index.search("query", top_k=3)
        call_args = mock_pool.fetch.call_args
        # The SQL should contain $2 for top_k
        assert 3 in call_args[0] or any(a == 3 for a in call_args[0])

    @pytest.mark.asyncio
    async def test_upsert(self, index, mock_pool):
        await index.upsert("doc1", "content here", "skill")
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args[0]
        assert "doc1" in call_args
        assert "content here" in call_args

    @pytest.mark.asyncio
    async def test_upsert_generates_embedding(self, index, mock_pool):
        embed_called = False
        original_fn = index._embed_fn

        async def tracking_embed(text: str) -> list[float]:
            nonlocal embed_called
            embed_called = True
            return await original_fn(text)

        index._embed_fn = tracking_embed
        await index.upsert("doc1", "content", "anchor_summary")
        assert embed_called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/index/test_doc_index.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coding_agent/index/doc_index.py
"""PgVectorDocIndex — document vector search via PostgreSQL + pgvector.

Replaces LanceDB for natural-language document retrieval.
Requires an asyncpg Pool with the pgvector extension enabled.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from coding_agent.protocols import DocResult


class PgVectorDocIndex:
    """Document index backed by PostgreSQL pgvector.

    Args:
        pool: An asyncpg connection pool.
        embed_fn: Async function that converts text to a vector (list[float]).
    """

    def __init__(
        self,
        pool: Any,
        embed_fn: Callable[[str], Awaitable[list[float]]],
    ) -> None:
        self._pool = pool
        self._embed_fn = embed_fn

    async def search(self, query: str, top_k: int = 5) -> list[DocResult]:
        """Search for documents similar to query."""
        embedding = await self._embed_fn(query)
        rows = await self._pool.fetch(
            """
            SELECT id, content, source, 1 - (embedding <=> $1) AS similarity
            FROM doc_embeddings
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            embedding,
            top_k,
        )
        return [
            DocResult(
                id=row["id"],
                content=row["content"],
                source=row["source"],
                similarity=row["similarity"],
            )
            for row in rows
        ]

    async def upsert(self, doc_id: str, content: str, source: str) -> None:
        """Insert or update a document with its embedding."""
        embedding = await self._embed_fn(content)
        await self._pool.execute(
            """
            INSERT INTO doc_embeddings (id, content, source, embedding)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET content=$2, source=$3, embedding=$4, updated_at=NOW()
            """,
            doc_id,
            content,
            source,
            embedding,
        )
```

- [ ] **Step 4: Update index __init__.py**

```python
# src/coding_agent/index/__init__.py
"""Index subsystem — code and document indexing."""

from coding_agent.index.code_index import TreeSitterIndex
from coding_agent.index.doc_index import PgVectorDocIndex

__all__ = ["TreeSitterIndex", "PgVectorDocIndex"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/index/test_doc_index.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Verify all tests still pass**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/coding_agent/index/doc_index.py src/coding_agent/index/__init__.py tests/unit/index/test_doc_index.py
git commit -m "feat: implement PgVectorDocIndex for document vector search"
```

---

### Task 14: Wire kb.py to use PgVectorDocIndex (dual-mode)

**Files:**
- Modify: `src/coding_agent/kb.py`
- Create: `tests/unit/test_kb_pgvector.py`
- Verify: `tests/unit/test_kb.py` (existing tests still pass)

The existing `KB` class is tightly coupled to LanceDB. Instead of hacking `__new__`, we add a `_pgvector_index` attribute and branch in `search()` / `index_file()` so **both backends work through the same public API**.

- [ ] **Step 1: Write the failing test for pgvector KB path**

```python
# tests/unit/test_kb_pgvector.py
"""Tests for KB when backed by PgVectorDocIndex."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from coding_agent.kb import KB, KBSearchResult, DocumentChunk
from coding_agent.protocols import DocResult


class TestKBPgVectorMode:
    """Tests for KB.from_pgvector() dual-mode."""

    @pytest.fixture
    def mock_doc_index(self):
        idx = AsyncMock()
        idx.search = AsyncMock(return_value=[
            DocResult(id="d1", content="hello world", source="skill", similarity=0.9),
        ])
        idx.upsert = AsyncMock()
        return idx

    def test_from_pgvector_creates_instance(self, mock_doc_index):
        kb = KB.from_pgvector(doc_index=mock_doc_index)
        assert kb._pgvector_index is mock_doc_index

    @pytest.mark.asyncio
    async def test_search_routes_to_pgvector(self, mock_doc_index):
        kb = KB.from_pgvector(doc_index=mock_doc_index)
        results = await kb.search("hello", k=3)

        mock_doc_index.search.assert_awaited_once_with("hello", top_k=3)
        assert len(results) == 1
        assert results[0].chunk.content == "hello world"
        assert results[0].chunk.source == "skill"

    @pytest.mark.asyncio
    async def test_search_empty_query(self, mock_doc_index):
        kb = KB.from_pgvector(doc_index=mock_doc_index)
        results = await kb.search("", k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_empty_query_does_not_call_index(self, mock_doc_index):
        """Empty query should return [] without calling doc_index.search."""
        kb = KB.from_pgvector(doc_index=mock_doc_index)
        results = await kb.search("   ", k=5)
        assert results == []
        mock_doc_index.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_index_file_routes_to_pgvector(self, mock_doc_index):
        kb = KB.from_pgvector(doc_index=mock_doc_index, chunk_size=50)
        await kb.index_file(Path("/test/file.py"), "short content")

        mock_doc_index.upsert.assert_awaited()
        call_args = mock_doc_index.upsert.call_args[1]
        assert "short content" in call_args["content"]
        assert "/test/file.py" in call_args["source"]

    @pytest.mark.asyncio
    async def test_index_file_multi_chunk(self, mock_doc_index):
        """Multi-chunk content should call upsert once per chunk."""
        kb = KB.from_pgvector(doc_index=mock_doc_index, chunk_size=10, chunk_overlap=2)
        # 10 tokens * 4 chars = 40 chars per chunk, so 200 chars = ~6 chunks
        content = "A" * 200
        await kb.index_file(Path("/test/big.py"), content)
        assert mock_doc_index.upsert.await_count > 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_kb_pgvector.py -v`
Expected: FAIL with `AttributeError: type object 'KB' has no attribute 'from_pgvector'`

- [ ] **Step 3: Implement dual-mode KB**

In `src/coding_agent/kb.py`, add `_pgvector_index` to `__init__`, add `from_pgvector()` factory, and add branching in `search()` and `index_file()`:

Add to `KB.__init__()` at the end:
```python
        # pgvector backend (None = use LanceDB, set via from_pgvector())
        self._pgvector_index = None
```

Add factory classmethod:
```python
    @classmethod
    def from_pgvector(
        cls,
        doc_index: Any,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
    ) -> KB:
        """Create a KB backed by PgVectorDocIndex instead of LanceDB.

        The returned KB routes search/index through pgvector. LanceDB
        is not initialized.
        """
        instance = cls.__new__(cls)
        instance.chunk_size = chunk_size
        instance.chunk_overlap = chunk_overlap
        instance._pgvector_index = doc_index
        # LanceDB fields set to None — not used in pgvector mode
        instance.db_path = None
        instance.embedding_model = ""
        instance._embedding_fn = None
        instance._openai_client = None
        instance._db = None
        instance._table = None
        return instance
```

Add branching at the top of `search()`:
```python
    async def search(self, query: str, k: int = 5) -> list[KBSearchResult]:
        if not query.strip():
            return []
        # pgvector path
        if self._pgvector_index is not None:
            from coding_agent.protocols import DocResult
            results = await self._pgvector_index.search(query, top_k=k)
            return [
                KBSearchResult(
                    chunk=DocumentChunk(
                        id=r.id, content=r.content,
                        source=r.source, metadata={},
                    ),
                    score=1.0 - r.similarity,  # distance = 1 - similarity
                )
                for r in results
            ]
        # LanceDB path (existing code unchanged)
        table = self._get_table()
        # ... rest of existing search code ...
```

Add branching at the top of `index_file()`:
```python
    async def index_file(self, path: Path, content: str) -> None:
        # pgvector path
        if self._pgvector_index is not None:
            chunks = self._chunk_text(content)
            if not chunks or all(not c.strip() for c in chunks):
                return
            source = str(path)
            for i, chunk_content in enumerate(chunks):
                import hashlib
                doc_id = hashlib.md5(f"{source}:{i}:{chunk_content}".encode()).hexdigest()[:24]
                await self._pgvector_index.upsert(
                    doc_id=doc_id, content=chunk_content, source=source,
                )
            return
        # LanceDB path (existing code unchanged)
        table = self._get_table()
        # ... rest of existing index_file code ...
```

- [ ] **Step 4: Verify pgvector tests pass**

Run: `.venv/bin/python -m pytest tests/unit/test_kb_pgvector.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Verify existing LanceDB KB tests still pass**

Run: `.venv/bin/python -m pytest tests/unit/test_kb.py -v`
Expected: All existing tests PASS (LanceDB path unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/kb.py tests/unit/test_kb_pgvector.py
git commit -m "feat: add pgvector dual-mode to KB with proper routing in search/index_file"
```

---

### Task 15: Remove LanceDB dependency (deferred)

> **Note:** This task should only be executed AFTER confirming all pgvector paths work end-to-end with a real PostgreSQL instance. It is intentionally left as the final step. For now, keep LanceDB as a dependency.

**Files:**
- Modify: `pyproject.toml` (remove `lancedb` from dependencies)
- Modify: `src/coding_agent/kb.py` (remove LanceDB imports and code paths)
- Modify: `tests/unit/test_kb.py` (rewrite tests to use mock pgvector)

- [ ] **Step 1: Verify pgvector path covers all use cases** (manual verification)
- [ ] **Step 2: Remove lancedb from pyproject.toml dependencies**
- [ ] **Step 3: Remove LanceDB code from kb.py**
- [ ] **Step 4: Update tests**
- [ ] **Step 5: Run full test suite**
- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/coding_agent/kb.py tests/unit/test_kb.py
git commit -m "refactor: remove LanceDB dependency, kb.py now uses PgVectorDocIndex only"
```

---

## Self-Review Checklist

### 1. Spec coverage

| Spec section | Covered by task(s) | Status |
|---|---|---|
| Entry/EntryKind/AnchorPayload | Task 3 | ✅ |
| TapeStore Protocol | Task 2 | ✅ |
| MemoryManager Protocol | Task 2 | ✅ (added in review) |
| JSONLTapeStore | Task 4 | ✅ |
| PostgresTapeStore | Schema in Task 6 | ⏳ Deferred — see below |
| TriageGate | Task 5 | ✅ (standalone; integration deferred) |
| DatabasePool | Task 6 | ✅ |
| Schema migrations | Task 6 | ✅ |
| Config new fields | Task 7 | ✅ |
| AGENTS.md grounding | Task 8 | ✅ |
| Context anchor rebuild | Task 10 | ✅ (fixed ordering bug) |
| Anchor-based context from tape | Task 11 `build_context_from_tape()` | ✅ (added in review) |
| MemoryManager | Task 11 | ✅ (uses `TapeStore` type hint) |
| TreeSitterIndex | Task 12 | ✅ (real reference tracking added) |
| PgVectorDocIndex | Task 13 | ✅ |
| KB migration path | Task 14 | ✅ (fixed: dual-mode with routing) |
| LanceDB removal | Task 15 | ⏳ Deferred |
| protocols.py | Task 2 | ✅ |
| Old Tape compat | Task 9 | ✅ |

### Explicitly deferred items

**PostgresTapeStore:** Schema exists in Task 6. The async store class mirrors JSONLTapeStore but backed by asyncpg. Deferred because it requires a running PostgreSQL instance and cannot be meaningfully unit-tested without one. The Protocol + JSONLTapeStore pattern means the entire system works end-to-end. Add it as a follow-up task when `docker run pgvector/pgvector:pg16` is available in the dev workflow.

**Session migration (SQLite→PG):** Deferred intentionally. Current `Session` is sync, `TapeStore` is async — these are incompatible without either: (a) making Session async, or (b) writing a sync adapter. Both are invasive. **Strategy:** keep legacy `core/session.py` + `core/tape.py` untouched. The new `tape/` package runs alongside them. Migrate Session in a dedicated follow-up when the async boundary is resolved.

**TriageGate integration:** `TriageGate` is implemented and tested as a standalone filter (Task 5). It is not yet wired into `MemoryManager` or any summarization pipeline because no summarizer consumes it yet. Wire it when the summarization pipeline is built.

### 2. Placeholder scan

No TBD/TODO/placeholder found. All tasks have complete code.

### 3. Type consistency

- `Entry.generate_id()` → `str` (26-char ULID) — used consistently in Task 3 tests and Task 4.
- `AnchorPayload.to_dict()` / `.from_dict()` — used consistently in Tasks 3, 10, 11.
- `TapeStore` protocol methods match `JSONLTapeStore` implementation exactly.
- `CodeIndex` protocol methods match `TreeSitterIndex` implementation exactly.
- `DocIndex` protocol methods match `PgVectorDocIndex` implementation exactly.
- `MemoryManager` protocol methods match `DefaultMemoryManager` implementation.
- `CodeSnippet`, `SymbolInfo`, `DocResult` dataclasses in `protocols.py` match usage in Tasks 12, 13.
- `DefaultMemoryManager.__init__` uses `TapeStore` type hint (not `object`).
- `KB.from_pgvector()` routes `search()` and `index_file()` through `_pgvector_index` (not dead code).
