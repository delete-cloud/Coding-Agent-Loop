# Batch 1: Multi-Pod Production Readiness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable `replicaCount: 2+` K8S deployment with shared PG + Redis, proper health checks, graceful shutdown, and secret management.

**Architecture:** Stateless worker Pods share PostgreSQL (tape + session storage) and Redis (rate limiting + UI session). Session-level `pg_advisory_lock` prevents concurrent writes. Lifecycle Protocol in agentkit provides startup/shutdown hooks consumed by coding-agent's HTTP server. StoragePlugin auto-selects backend via config.

**Tech Stack:** asyncpg, PostgreSQL 15+, Redis 7+, FastAPI lifespan, Helm 3

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/agentkit/runtime/lifecycle.py` | CREATE | Lifecycle Protocol (on_startup, on_shutdown, health_check, readiness_check) |
| `src/agentkit/__init__.py` | MODIFY:53 | Export `Lifecycle` |
| `tests/agentkit/runtime/test_lifecycle.py` | CREATE | Lifecycle Protocol conformance tests |
| `src/agentkit/storage/pg.py` | CREATE | PGPool, PGTapeStore, PGSessionStore, PGSessionLock |
| `src/agentkit/storage/__init__.py` | MODIFY:1-4 | Export PG classes |
| `tests/agentkit/storage/test_pg.py` | CREATE | PG storage tests (mocked asyncpg) |
| `src/coding_agent/plugins/storage.py` | MODIFY:118-162 | Backend factory (jsonl/pg), session lock integration |
| `tests/coding_agent/plugins/test_storage_factory.py` | CREATE | StoragePlugin factory tests |
| `src/coding_agent/ui/rate_limit.py` | MODIFY:11-16 | Redis-backed storage_uri |
| `tests/ui/test_rate_limit.py` | CREATE | Rate limit storage_uri tests |
| `src/coding_agent/ui/http_server.py` | MODIFY:59-79,320-326 | /healthz, /readyz, graceful shutdown, lifespan |
| `tests/ui/test_health_endpoints.py` | CREATE | Health check endpoint tests |
| `src/coding_agent/__main__.py` | MODIFY:310-318 | Pass PGPool to serve command |
| `src/coding_agent/agent.toml` | MODIFY:27-31 | Storage backend + DSN config |
| `pyproject.toml` | MODIFY:26-33 | asyncpg optional dependency |
| `Dockerfile` | MODIFY:15 | Install asyncpg |
| `helm/values.yaml` | MODIFY | secretRef, secretEnv, HPA, PDB, terminationGracePeriodSeconds |
| `helm/templates/deployment.yaml` | MODIFY | secretRef/secretEnv injection, probe paths, gracePeriod |
| `helm/templates/hpa.yaml` | CREATE | HorizontalPodAutoscaler |
| `helm/templates/pdb.yaml` | CREATE | PodDisruptionBudget |

---

## Task 1: Lifecycle Protocol

**Files:**
- Create: `src/agentkit/runtime/lifecycle.py`
- Modify: `src/agentkit/__init__.py:53`
- Test: `tests/agentkit/runtime/test_lifecycle.py`

- [ ] **Step 1: Write the test file**

```python
# tests/agentkit/runtime/test_lifecycle.py
import pytest
from agentkit.runtime.lifecycle import Lifecycle


class ConcreteLifecycle:
    """Minimal Lifecycle implementation for protocol testing."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def on_startup(self) -> None:
        self.started = True

    async def on_shutdown(self, timeout: float = 30.0) -> None:
        self.stopped = True

    async def health_check(self) -> dict:
        return {"status": "ok"}

    async def readiness_check(self) -> bool:
        return self.started and not self.stopped


class TestLifecycleProtocol:
    def test_concrete_satisfies_protocol(self):
        lc = ConcreteLifecycle()
        assert isinstance(lc, Lifecycle)

    def test_object_does_not_satisfy_protocol(self):
        assert not isinstance(object(), Lifecycle)

    @pytest.mark.asyncio
    async def test_startup_shutdown_sequence(self):
        lc = ConcreteLifecycle()
        assert not lc.started
        await lc.on_startup()
        assert lc.started
        assert await lc.readiness_check() is True
        await lc.on_shutdown(timeout=5.0)
        assert lc.stopped
        assert await lc.readiness_check() is False

    @pytest.mark.asyncio
    async def test_health_check_returns_dict(self):
        lc = ConcreteLifecycle()
        result = await lc.health_check()
        assert isinstance(result, dict)
        assert "status" in result

    @pytest.mark.asyncio
    async def test_shutdown_default_timeout(self):
        lc = ConcreteLifecycle()
        await lc.on_shutdown()  # Uses default timeout=30.0
        assert lc.stopped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/runtime/test_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentkit.runtime.lifecycle'`

- [ ] **Step 3: Write the Lifecycle Protocol**

```python
# src/agentkit/runtime/lifecycle.py
"""Lifecycle Protocol — startup/shutdown hooks for agentkit runtimes."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Lifecycle(Protocol):
    """Lifecycle hooks for agentkit runtimes.

    Implement this protocol in your application layer (e.g. HTTP server)
    to get clean startup/shutdown semantics that work with K8S probes
    and graceful drain.
    """

    async def on_startup(self) -> None:
        """Called once when the process starts.

        Responsibilities:
        - Initialize DB connection pools
        - Verify external service connectivity
        - Register with service discovery (if any)
        """
        ...

    async def on_shutdown(self, timeout: float = 30.0) -> None:
        """Called on SIGTERM / process exit.

        Args:
            timeout: Max seconds to wait for in-flight work to drain.
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """Deep health check for /healthz.

        Returns:
            {"status": "ok"} or {"status": "degraded", "details": {...}}
        """
        ...

    async def readiness_check(self) -> bool:
        """Shallow readiness check for /readyz.

        Returns:
            True if this instance can accept new work.
            False during startup warmup or shutdown drain.
        """
        ...
```

- [ ] **Step 4: Export Lifecycle from agentkit**

Add to `src/agentkit/__init__.py`:

In the imports section, add:
```python
from agentkit.runtime.lifecycle import Lifecycle
```

In the `__all__` list, under the `# Runtime` section, add:
```python
    "Lifecycle",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/runtime/test_lifecycle.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/agentkit/runtime/lifecycle.py tests/agentkit/runtime/test_lifecycle.py src/agentkit/__init__.py
git commit -m "feat(agentkit): add Lifecycle Protocol for startup/shutdown hooks"
```

---

## Task 2: PGPool — Connection Pool Manager

**Files:**
- Create: `src/agentkit/storage/pg.py`
- Test: `tests/agentkit/storage/test_pg.py`

- [ ] **Step 1: Write PGPool tests**

```python
# tests/agentkit/storage/test_pg.py
"""Tests for PG storage backend.

All tests use a mock asyncpg pool — no real database needed.
The mock is a simple class that tracks calls and returns canned results.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class MockConnection:
    """Fake asyncpg connection."""

    def __init__(self):
        self.fetchval = AsyncMock(return_value=1)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock()
        self.executemany = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockPool:
    """Fake asyncpg.Pool."""

    def __init__(self):
        self._conn = MockConnection()
        self.close = AsyncMock()
        self.acquire_calls = 0
        self.release = AsyncMock()

    def acquire(self):
        self.acquire_calls += 1
        return self  # context manager returns MockConnection

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass

    async def acquire_raw(self):
        """Non-context-manager acquire for session lock."""
        self.acquire_calls += 1
        return self._conn


# --- PGPool tests ---

class TestPGPool:
    @pytest.mark.asyncio
    async def test_open_creates_pool(self):
        with patch("agentkit.storage.pg.asyncpg") as mock_asyncpg:
            mock_pool = MockPool()
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            from agentkit.storage.pg import PGPool
            pg = PGPool(dsn="postgres://localhost/test")
            await pg.open()

            mock_asyncpg.create_pool.assert_awaited_once_with(
                "postgres://localhost/test",
                min_size=2,
                max_size=10,
            )
            assert pg.pool is mock_pool

    @pytest.mark.asyncio
    async def test_pool_raises_before_open(self):
        from agentkit.storage.pg import PGPool
        pg = PGPool(dsn="postgres://localhost/test")
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = pg.pool

    @pytest.mark.asyncio
    async def test_close_shuts_down_pool(self):
        with patch("agentkit.storage.pg.asyncpg") as mock_asyncpg:
            mock_pool = MockPool()
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            from agentkit.storage.pg import PGPool
            pg = PGPool(dsn="postgres://localhost/test")
            await pg.open()
            await pg.close()

            mock_pool.close.assert_awaited_once()
            with pytest.raises(RuntimeError, match="not initialized"):
                _ = pg.pool

    @pytest.mark.asyncio
    async def test_ping_success(self):
        with patch("agentkit.storage.pg.asyncpg") as mock_asyncpg:
            mock_pool = MockPool()
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            from agentkit.storage.pg import PGPool
            pg = PGPool(dsn="postgres://localhost/test")
            await pg.open()

            result = await pg.ping()
            assert result is True
            mock_pool._conn.fetchval.assert_awaited_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_ping_failure(self):
        with patch("agentkit.storage.pg.asyncpg") as mock_asyncpg:
            mock_pool = MockPool()
            mock_pool._conn.fetchval = AsyncMock(side_effect=ConnectionError("down"))
            mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)

            from agentkit.storage.pg import PGPool
            pg = PGPool(dsn="postgres://localhost/test")
            await pg.open()

            result = await pg.ping()
            assert result is False

    @pytest.mark.asyncio
    async def test_custom_pool_sizes(self):
        with patch("agentkit.storage.pg.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=MockPool())

            from agentkit.storage.pg import PGPool
            pg = PGPool(dsn="postgres://localhost/test", min_size=5, max_size=20)
            await pg.open()

            mock_asyncpg.create_pool.assert_awaited_once_with(
                "postgres://localhost/test",
                min_size=5,
                max_size=20,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGPool -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentkit.storage.pg'`

- [ ] **Step 3: Write PGPool implementation**

```python
# src/agentkit/storage/pg.py
"""PostgreSQL storage backend — PGPool, PGTapeStore, PGSessionStore, PGSessionLock."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


class PGPool:
    """Manages asyncpg connection pool lifecycle.

    Created during on_startup(), closed during on_shutdown().
    """

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        """Create the connection pool."""
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Get the pool. Raises if not opened."""
        if self._pool is None:
            raise RuntimeError("PGPool not initialized -- call open() first")
        return self._pool

    async def ping(self) -> bool:
        """Health check: execute SELECT 1."""
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGPool -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/agentkit/storage/pg.py tests/agentkit/storage/test_pg.py
git commit -m "feat(agentkit): add PGPool connection pool manager"
```

---

## Task 3: PGTapeStore

**Files:**
- Modify: `src/agentkit/storage/pg.py` (append)
- Test: `tests/agentkit/storage/test_pg.py` (append)

- [ ] **Step 1: Write PGTapeStore tests**

Append to `tests/agentkit/storage/test_pg.py`:

```python
# --- PGTapeStore tests ---

class TestPGTapeStore:
    @pytest.fixture
    def mock_pool(self):
        return MockPool()

    @pytest.fixture
    def store(self, mock_pool):
        from agentkit.storage.pg import PGTapeStore
        return PGTapeStore(pool=mock_pool)

    def test_satisfies_tape_store_protocol(self, store):
        from agentkit.storage.protocols import TapeStore
        assert isinstance(store, TapeStore)

    @pytest.mark.asyncio
    async def test_save_computes_seq_from_zero(self, store, mock_pool):
        # fetchval returns None (no existing entries)
        mock_pool._conn.fetchval = AsyncMock(return_value=None)

        entries = [{"kind": "message", "payload": {"role": "user", "content": "hi"}}]
        await store.save("tape-1", entries)

        # Should have called fetchval for max seq, then executemany
        mock_pool._conn.fetchval.assert_awaited_once()
        mock_pool._conn.executemany.assert_awaited_once()
        call_args = mock_pool._conn.executemany.call_args
        # Verify the rows: (tape_id, seq, entry_json)
        rows = call_args[0][1]
        assert len(rows) == 1
        assert rows[0][0] == "tape-1"  # tape_id
        assert rows[0][1] == 0  # seq starts at 0

    @pytest.mark.asyncio
    async def test_save_appends_after_existing(self, store, mock_pool):
        # fetchval returns 2 (existing max seq)
        mock_pool._conn.fetchval = AsyncMock(return_value=2)

        entries = [
            {"kind": "message", "payload": {"role": "user", "content": "a"}},
            {"kind": "message", "payload": {"role": "assistant", "content": "b"}},
        ]
        await store.save("tape-1", entries)

        call_args = mock_pool._conn.executemany.call_args
        rows = call_args[0][1]
        assert len(rows) == 2
        assert rows[0][1] == 3  # seq = max(2) + 1
        assert rows[1][1] == 4

    @pytest.mark.asyncio
    async def test_save_empty_entries_is_noop(self, store, mock_pool):
        await store.save("tape-1", [])
        mock_pool._conn.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_returns_entries_ordered(self, store, mock_pool):
        mock_pool._conn.fetch = AsyncMock(return_value=[
            {"entry": '{"kind": "message", "payload": {"role": "user"}}'},
            {"entry": '{"kind": "tool_call", "payload": {"name": "read"}}'},
        ])

        result = await store.load("tape-1")
        assert len(result) == 2
        assert result[0]["kind"] == "message"
        assert result[1]["kind"] == "tool_call"

    @pytest.mark.asyncio
    async def test_load_empty_tape(self, store, mock_pool):
        mock_pool._conn.fetch = AsyncMock(return_value=[])
        result = await store.load("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_ids(self, store, mock_pool):
        mock_pool._conn.fetch = AsyncMock(return_value=[
            {"tape_id": "tape-a"},
            {"tape_id": "tape-b"},
        ])
        result = await store.list_ids()
        assert result == ["tape-a", "tape-b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGTapeStore -v`
Expected: FAIL — `ImportError: cannot import name 'PGTapeStore'`

- [ ] **Step 3: Write PGTapeStore implementation**

Append to `src/agentkit/storage/pg.py`:

```python
class PGTapeStore:
    """TapeStore Protocol implementation backed by PostgreSQL.

    Table schema:
        tape_entries (tape_id TEXT, seq INTEGER, entry JSONB, created_at TIMESTAMPTZ)
        PRIMARY KEY (tape_id, seq)
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        """Append entries to tape_entries table."""
        if not entries:
            return

        async with self._pool.acquire() as conn:
            max_seq = await conn.fetchval(
                "SELECT MAX(seq) FROM tape_entries WHERE tape_id = $1",
                tape_id,
            )
            start_seq = (max_seq + 1) if max_seq is not None else 0

            rows = [
                (tape_id, start_seq + i, json.dumps(entry))
                for i, entry in enumerate(entries)
            ]
            await conn.executemany(
                "INSERT INTO tape_entries (tape_id, seq, entry) VALUES ($1, $2, $3::jsonb)",
                rows,
            )

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        """Load all entries for a tape, ordered by seq ASC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT entry FROM tape_entries WHERE tape_id = $1 ORDER BY seq ASC",
                tape_id,
            )
        return [json.loads(row["entry"]) for row in rows]

    async def list_ids(self) -> list[str]:
        """Return distinct tape_ids."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT tape_id FROM tape_entries ORDER BY tape_id"
            )
        return [row["tape_id"] for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGTapeStore -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/agentkit/storage/pg.py tests/agentkit/storage/test_pg.py
git commit -m "feat(agentkit): add PGTapeStore implementing TapeStore Protocol"
```

---

## Task 4: PGSessionStore

**Files:**
- Modify: `src/agentkit/storage/pg.py` (append)
- Test: `tests/agentkit/storage/test_pg.py` (append)

- [ ] **Step 1: Write PGSessionStore tests**

Append to `tests/agentkit/storage/test_pg.py`:

```python
# --- PGSessionStore tests ---

class TestPGSessionStore:
    @pytest.fixture
    def mock_pool(self):
        return MockPool()

    @pytest.fixture
    def store(self, mock_pool):
        from agentkit.storage.pg import PGSessionStore
        return PGSessionStore(pool=mock_pool)

    def test_satisfies_session_store_protocol(self, store):
        from agentkit.storage.protocols import SessionStore
        assert isinstance(store, SessionStore)

    @pytest.mark.asyncio
    async def test_save_session_executes_upsert(self, store, mock_pool):
        await store.save_session("ses-1", {"model": "gpt-4", "turns": 5})
        mock_pool._conn.execute.assert_awaited_once()
        sql = mock_pool._conn.execute.call_args[0][0]
        assert "INSERT INTO sessions" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_load_session_found(self, store, mock_pool):
        mock_pool._conn.fetchrow = AsyncMock(return_value={
            "data": '{"model": "gpt-4", "turns": 5}'
        })
        result = await store.load_session("ses-1")
        assert result is not None
        assert result["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_load_session_not_found(self, store, mock_pool):
        mock_pool._conn.fetchrow = AsyncMock(return_value=None)
        result = await store.load_session("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, store, mock_pool):
        mock_pool._conn.fetch = AsyncMock(return_value=[
            {"session_id": "a"},
            {"session_id": "b"},
        ])
        result = await store.list_sessions()
        assert result == ["a", "b"]

    @pytest.mark.asyncio
    async def test_delete_session(self, store, mock_pool):
        await store.delete_session("ses-1")
        mock_pool._conn.execute.assert_awaited_once()
        sql = mock_pool._conn.execute.call_args[0][0]
        assert "DELETE FROM sessions" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGSessionStore -v`
Expected: FAIL — `ImportError: cannot import name 'PGSessionStore'`

- [ ] **Step 3: Write PGSessionStore implementation**

Append to `src/agentkit/storage/pg.py`:

```python
class PGSessionStore:
    """SessionStore Protocol implementation backed by PostgreSQL.

    Table schema:
        sessions (session_id TEXT PK, data JSONB, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        """UPSERT session data. Updates updated_at on conflict."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO sessions (session_id, data)
                   VALUES ($1, $2::jsonb)
                   ON CONFLICT (session_id) DO UPDATE
                   SET data = EXCLUDED.data, updated_at = now()""",
                session_id,
                json.dumps(data),
            )

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        """Load session by ID. Returns None if not found."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM sessions WHERE session_id = $1",
                session_id,
            )
        if row is None:
            return None
        return json.loads(row["data"])

    async def list_sessions(self) -> list[str]:
        """List all session IDs, ordered by updated_at DESC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id FROM sessions ORDER BY updated_at DESC"
            )
        return [row["session_id"] for row in rows]

    async def delete_session(self, session_id: str) -> None:
        """Delete a session by ID."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM sessions WHERE session_id = $1",
                session_id,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGSessionStore -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/agentkit/storage/pg.py tests/agentkit/storage/test_pg.py
git commit -m "feat(agentkit): add PGSessionStore implementing SessionStore Protocol"
```

---

## Task 5: PGSessionLock

**Files:**
- Modify: `src/agentkit/storage/pg.py` (append)
- Test: `tests/agentkit/storage/test_pg.py` (append)

- [ ] **Step 1: Write PGSessionLock tests**

Append to `tests/agentkit/storage/test_pg.py`:

```python
# --- PGSessionLock tests ---

class MockPoolForLock:
    """Mock pool that supports non-context-manager acquire() for advisory locks.

    asyncpg.Pool.acquire() can be used two ways:
    1. `async with pool.acquire() as conn:` (context manager — used by TapeStore/SessionStore)
    2. `conn = await pool.acquire()` (direct — used by SessionLock to hold connection)

    This mock handles case 2.
    """

    def __init__(self):
        self._conn = MockConnection()
        self.release = AsyncMock()

    async def acquire(self):
        return self._conn


class TestPGSessionLock:
    @pytest.fixture
    def mock_pool(self):
        return MockPoolForLock()

    @pytest.fixture
    def lock(self, mock_pool):
        from agentkit.storage.pg import PGSessionLock
        return PGSessionLock(pool=mock_pool)

    @pytest.mark.asyncio
    async def test_acquire_calls_advisory_lock(self, lock, mock_pool):
        await lock.acquire("session-abc")
        # Should have acquired a connection and called pg_advisory_lock
        calls = mock_pool._conn.execute.call_args_list
        assert len(calls) == 1
        sql = calls[0][0][0]
        assert "pg_advisory_lock" in sql
        assert "hashtext" in sql

    @pytest.mark.asyncio
    async def test_release_unlocks_and_returns_connection(self, lock, mock_pool):
        await lock.acquire("session-abc")
        mock_pool._conn.execute.reset_mock()

        await lock.release()
        calls = mock_pool._conn.execute.call_args_list
        assert len(calls) == 1
        sql = calls[0][0][0]
        assert "pg_advisory_unlock_all" in sql
        mock_pool.release.assert_awaited_once_with(mock_pool._conn)

    @pytest.mark.asyncio
    async def test_release_without_acquire_is_noop(self, lock):
        await lock.release()  # Should not raise

    @pytest.mark.asyncio
    async def test_release_returns_connection_even_on_error(self, lock, mock_pool):
        await lock.acquire("session-abc")
        mock_pool._conn.execute = AsyncMock(side_effect=Exception("unlock failed"))

        with pytest.raises(Exception, match="unlock failed"):
            await lock.release()

        # Connection should still be released back to pool
        mock_pool.release.assert_awaited_once_with(mock_pool._conn)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGSessionLock -v`
Expected: FAIL — `ImportError: cannot import name 'PGSessionLock'`

- [ ] **Step 3: Write PGSessionLock implementation**

Append to `src/agentkit/storage/pg.py`:

```python
class PGSessionLock:
    """Session-level advisory lock using PostgreSQL.

    Uses pg_advisory_lock (session-level, NOT transaction-level) because
    a single turn spans multiple DB transactions (tool calls, save_state).
    Lock is acquired at turn start, released at turn end.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None

    async def acquire(self, session_id: str) -> None:
        """Acquire advisory lock. Blocks until lock is available."""
        self._conn = await self._pool.acquire()
        await self._conn.execute(
            "SELECT pg_advisory_lock(hashtext($1))", session_id
        )

    async def release(self) -> None:
        """Release the advisory lock and return connection to pool."""
        if self._conn is None:
            return
        try:
            await self._conn.execute("SELECT pg_advisory_unlock_all()")
        finally:
            await self._pool.release(self._conn)
            self._conn = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py::TestPGSessionLock -v`
Expected: 4 passed

- [ ] **Step 5: Export PG classes from storage package**

Replace `src/agentkit/storage/__init__.py` with:

```python
from agentkit.storage.protocols import DocIndex, SessionStore, TapeStore
from agentkit.storage.session import FileSessionStore

__all__ = [
    "DocIndex",
    "FileSessionStore",
    "SessionStore",
    "TapeStore",
]

# Conditional PG exports — only available when asyncpg is installed
try:
    from agentkit.storage.pg import PGPool, PGSessionLock, PGSessionStore, PGTapeStore

    __all__ += ["PGPool", "PGSessionLock", "PGSessionStore", "PGTapeStore"]
except ImportError:
    pass
```

- [ ] **Step 6: Run all PG tests together**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/storage/test_pg.py -v`
Expected: All 23 passed

- [ ] **Step 7: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/agentkit/storage/pg.py src/agentkit/storage/__init__.py tests/agentkit/storage/test_pg.py
git commit -m "feat(agentkit): add PGSessionLock and export PG storage classes"
```

---

## Task 6: StoragePlugin Backend Factory

**Files:**
- Modify: `src/coding_agent/plugins/storage.py:118-162`
- Test: `tests/coding_agent/plugins/test_storage_factory.py`

- [ ] **Step 1: Write StoragePlugin factory tests**

```python
# tests/coding_agent/plugins/test_storage_factory.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestStoragePluginFactory:
    """Test StoragePlugin backend selection."""

    def test_default_backend_is_jsonl(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        plugin = StoragePlugin(data_dir=tmp_path)
        assert plugin._backend == "jsonl"

    def test_jsonl_backend_creates_jsonl_store(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin, JSONLTapeStore
        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        store = plugin._create_tape_store()
        assert isinstance(store, JSONLTapeStore)

    def test_pg_backend_without_pool_raises(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        plugin = StoragePlugin(data_dir=tmp_path, backend="pg")
        with pytest.raises(RuntimeError, match="pg_pool"):
            plugin._create_tape_store()

    def test_pg_backend_with_pool_creates_pg_store(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        mock_pg_pool = MagicMock()
        mock_pg_pool.pool = MagicMock()

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)

        with patch("agentkit.storage.pg.PGTapeStore") as MockPGTapeStore:
            store = plugin._create_tape_store()
            MockPGTapeStore.assert_called_once_with(pool=mock_pg_pool.pool)

    def test_pg_session_store_with_pool(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        mock_pg_pool = MagicMock()
        mock_pg_pool.pool = MagicMock()

        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)

        with patch("agentkit.storage.pg.PGSessionStore") as MockPGSessionStore:
            store = plugin._create_session_store()
            MockPGSessionStore.assert_called_once_with(pool=mock_pg_pool.pool)

    def test_jsonl_session_store(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        from agentkit.storage.session import FileSessionStore
        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        store = plugin._create_session_store()
        assert isinstance(store, FileSessionStore)

    def test_provide_storage_uses_factory(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        plugin = StoragePlugin(data_dir=tmp_path, backend="jsonl")
        storage = plugin.provide_storage()
        assert storage is not None  # ForkTapeStore wrapping JSONLTapeStore

    def test_pg_lock_stored_when_pg_backend(self, tmp_path):
        from coding_agent.plugins.storage import StoragePlugin
        mock_pg_pool = MagicMock()
        mock_pg_pool.pool = MagicMock()
        plugin = StoragePlugin(data_dir=tmp_path, backend="pg", pg_pool=mock_pg_pool)
        assert plugin._session_lock is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_storage_factory.py -v`
Expected: FAIL — `StoragePlugin() got an unexpected keyword argument 'backend'`

- [ ] **Step 3: Modify StoragePlugin**

Edit `src/coding_agent/plugins/storage.py`. Replace the `StoragePlugin` class (lines 118-162) with:

```python
class StoragePlugin:
    """Plugin providing storage backends.

    Supports two backends:
    - "jsonl": Local JSONL files (default, backward compatible)
    - "pg": PostgreSQL via asyncpg (requires pg_pool)
    """

    state_key = "storage"

    def __init__(
        self,
        data_dir: Path | None = None,
        backend: str = "jsonl",
        pg_pool: Any | None = None,
    ) -> None:
        self._data_dir = data_dir or Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._backend = backend
        self._pg_pool = pg_pool
        self._fork_store: ForkTapeStore | None = None
        self._session_store: FileSessionStore | Any | None = None
        self._jsonl_store: JSONLTapeStore | None = None
        self._session_lock: Any | None = None

        if self._backend == "pg" and self._pg_pool is not None:
            from agentkit.storage.pg import PGSessionLock
            self._session_lock = PGSessionLock(pool=self._pg_pool.pool)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "provide_storage": self.provide_storage,
            "mount": self.do_mount,
        }

    def _create_tape_store(self) -> Any:
        """Create tape store based on configured backend."""
        if self._backend == "pg":
            if self._pg_pool is None:
                raise RuntimeError("PG backend requires pg_pool")
            from agentkit.storage.pg import PGTapeStore
            return PGTapeStore(pool=self._pg_pool.pool)
        return JSONLTapeStore(self._data_dir / "tapes")

    def _create_session_store(self) -> Any:
        """Create session store based on configured backend."""
        if self._backend == "pg":
            if self._pg_pool is None:
                raise RuntimeError("PG backend requires pg_pool")
            from agentkit.storage.pg import PGSessionStore
            return PGSessionStore(pool=self._pg_pool.pool)
        return FileSessionStore(self._data_dir / "sessions")

    def provide_storage(self, **kwargs: Any) -> ForkTapeStore:
        if self._fork_store is None:
            backing = self._create_tape_store()
            self._fork_store = ForkTapeStore(backing)
        return self._fork_store

    def _get_jsonl_store(self) -> JSONLTapeStore:
        if self._jsonl_store is None:
            self._jsonl_store = JSONLTapeStore(self._data_dir / "tapes")
        return self._jsonl_store

    def load_memory_records(self, session_id: str) -> list[dict[str, Any]]:
        return self._get_jsonl_store().load_memory_records(session_id)

    def append_memory_record(self, session_id: str, record: dict[str, Any]) -> None:
        self._get_jsonl_store().append_memory_record(session_id, record)

    def replace_memory_records(
        self, session_id: str, records: list[dict[str, Any]]
    ) -> None:
        self._get_jsonl_store().replace_memory_records(session_id, records)

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        if self._session_store is None:
            self._session_store = self._create_session_store()
        return {"session_store": self._session_store, "plugin": self}

    @property
    def session_lock(self) -> Any | None:
        """Expose session lock for pipeline to acquire/release."""
        return self._session_lock
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_storage_factory.py -v`
Expected: 8 passed

- [ ] **Step 5: Run existing storage tests to verify backward compat**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_storage*.py tests/agentkit/storage/ -v`
Expected: All pass (no regression)

- [ ] **Step 6: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/plugins/storage.py tests/coding_agent/plugins/test_storage_factory.py
git commit -m "feat(coding-agent): StoragePlugin backend factory (jsonl/pg)"
```

---

## Task 7: Rate Limiter Redis Backend

**Files:**
- Modify: `src/coding_agent/ui/rate_limit.py:9-16`
- Test: `tests/ui/test_rate_limit.py`

- [ ] **Step 1: Write rate limit tests**

```python
# tests/ui/test_rate_limit.py
import os
import pytest
from unittest.mock import patch


class TestRateLimitStorageUri:
    def test_default_is_memory(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove any existing env var
            os.environ.pop("AGENT_SESSION_REDIS_URL", None)
            from coding_agent.ui.rate_limit import _get_storage_uri
            assert _get_storage_uri() == "memory://"

    def test_redis_url_from_env(self):
        with patch.dict(os.environ, {"AGENT_SESSION_REDIS_URL": "redis://redis:6379/0"}):
            from coding_agent.ui.rate_limit import _get_storage_uri
            assert _get_storage_uri() == "redis://redis:6379/0"

    def test_empty_redis_url_falls_back_to_memory(self):
        with patch.dict(os.environ, {"AGENT_SESSION_REDIS_URL": ""}):
            from coding_agent.ui.rate_limit import _get_storage_uri
            assert _get_storage_uri() == "memory://"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ui/test_rate_limit.py -v`
Expected: FAIL — `ImportError: cannot import name '_get_storage_uri'`

- [ ] **Step 3: Modify rate_limit.py**

Replace the entire content of `src/coding_agent/ui/rate_limit.py`:

```python
"""Rate limiting configuration for HTTP API."""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_storage_uri() -> str:
    """Resolve rate limit storage URI.

    Uses Redis if AGENT_SESSION_REDIS_URL is set, otherwise in-memory.
    In K8S multi-Pod deployment, Redis is required for consistent rate limiting.
    """
    redis_url = os.environ.get("AGENT_SESSION_REDIS_URL", "")
    if redis_url:
        return redis_url
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=_get_storage_uri(),
)


class RateLimits:
    """Predefined rate limits for different endpoint types."""

    CREATE_SESSION = "10/minute"
    SEND_PROMPT = "20/minute"
    APPROVE = "30/minute"
    GET_SESSION = "60/minute"
    CLOSE_SESSION = "20/minute"
    HEALTH = "100/minute"
    EVENTS = "30/minute"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ui/test_rate_limit.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/rate_limit.py tests/ui/test_rate_limit.py
git commit -m "feat(coding-agent): rate limiter uses Redis when AGENT_SESSION_REDIS_URL is set"
```

---

## Task 8: Health Check Endpoints + Graceful Shutdown

**Files:**
- Modify: `src/coding_agent/ui/http_server.py:59-79,320-326`
- Test: `tests/ui/test_health_endpoints.py`

- [ ] **Step 1: Write health endpoint tests**

```python
# tests/ui/test_health_endpoints.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked session manager."""
    from coding_agent.ui.http_server import app
    return TestClient(app)


class TestHealthzEndpoint:
    def test_healthz_no_dependencies(self, client):
        """When no PG/Redis configured, healthz returns ok with empty checks."""
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_legacy_health_still_works(self, client):
        """The old /health endpoint should still respond."""
        response = client.get("/health")
        assert response.status_code == 200


class TestReadyzEndpoint:
    def test_readyz_normal(self, client):
        """When not draining, readyz returns ready."""
        response = client.get("/readyz")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True

    def test_readyz_draining(self, client):
        """When draining, readyz returns 503."""
        from coding_agent.ui.http_server import app
        app.state.draining = True
        try:
            response = client.get("/readyz")
            assert response.status_code == 503
            data = response.json()
            assert data["ready"] is False
        finally:
            app.state.draining = False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ui/test_health_endpoints.py -v`
Expected: FAIL — `404 Not Found` for `/healthz` and `/readyz`

- [ ] **Step 3: Modify http_server.py — add healthz/readyz and graceful shutdown**

Add after the existing imports at the top of `src/coding_agent/ui/http_server.py`:

```python
import os
import signal
```

Replace the `lifespan` function (lines 59-79) with:

```python
_shutdown_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    # Startup
    app.state.draining = False

    # Initialize PG pool if configured
    pg_pool = getattr(app.state, "pg_pool", None)
    if pg_pool is not None:
        await pg_pool.open()

    cleanup_task = asyncio.create_task(_cleanup_idle_sessions())
    logger.info("HTTP server starting up")

    # Register SIGTERM handler for graceful drain
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, _initiate_shutdown, app)
    except NotImplementedError:
        pass  # Windows does not support add_signal_handler

    yield  # Server runs here

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Close all sessions
    for session_id in list(session_manager.list_sessions()):
        await session_manager.close_session(session_id)

    # Close PG pool
    if pg_pool is not None:
        await pg_pool.close()

    logger.info("HTTP server shut down")


def _initiate_shutdown(app: FastAPI) -> None:
    """SIGTERM handler: mark as draining, let current work finish."""
    app.state.draining = True
    _shutdown_event.set()
    logger.info("SIGTERM received, draining...")
```

Add the `/healthz` and `/readyz` endpoints after the existing `/health` endpoint (after line 326):

```python
@app.get("/healthz")
async def healthz():
    """Deep health check -- verifies all dependencies."""
    checks: dict[str, str] = {}

    # Check PG pool
    pg_pool = getattr(app.state, "pg_pool", None)
    if pg_pool is not None:
        checks["postgres"] = "ok" if await pg_pool.ping() else "fail"

    # Check Redis
    redis_url = os.environ.get("AGENT_SESSION_REDIS_URL", "")
    if redis_url:
        try:
            store = getattr(session_manager, "_store", None)
            if store is not None and hasattr(store, "_client"):
                store._client.ping()
                checks["redis"] = "ok"
            else:
                checks["redis"] = "ok"  # Redis URL set but no store yet
        except Exception:
            checks["redis"] = "fail"

    all_ok = all(v == "ok" for v in checks.values()) if checks else True
    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=status_code,
    )


@app.get("/readyz")
async def readyz():
    """Readiness check -- can this instance accept work?"""
    from fastapi.responses import JSONResponse
    if getattr(app.state, "draining", False):
        return JSONResponse({"ready": False}, status_code=503)
    return {"ready": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ui/test_health_endpoints.py -v`
Expected: 4 passed

- [ ] **Step 5: Run existing HTTP server tests to verify no regression**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ui/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/http_server.py tests/ui/test_health_endpoints.py
git commit -m "feat(coding-agent): add /healthz, /readyz endpoints and graceful shutdown"
```

---

## Task 9: Configuration + Dependencies

**Files:**
- Modify: `src/coding_agent/agent.toml:27-31`
- Modify: `pyproject.toml:26-33`
- Modify: `Dockerfile:15`

- [ ] **Step 1: Update agent.toml storage config**

In `src/coding_agent/agent.toml`, replace the `[storage]` section (lines 27-31) with:

```toml
[storage]
backend = "jsonl"                           # "jsonl" | "pg"
# dsn = "${DATABASE_URL}"                   # uncomment for PG backend
# pool_min_size = 2
# pool_max_size = 10
tape_backend = "jsonl"
doc_backend = "lancedb"

[storage.paths]
tapes = "./data/tapes"
docs = "./data/docs"
sessions = "./data/sessions"
```

- [ ] **Step 2: Add asyncpg optional dependency**

In `pyproject.toml`, add after the existing `[project.optional-dependencies]` `dev` section:

```toml
pg = [
    "asyncpg>=0.29.0",
]
```

- [ ] **Step 3: Update Dockerfile to install asyncpg**

In `Dockerfile`, change line 15 from:
```dockerfile
RUN uv pip install --python /app/.venv/bin/python redis
```
to:
```dockerfile
RUN uv pip install --python /app/.venv/bin/python redis asyncpg
```

- [ ] **Step 4: Verify asyncpg can be resolved**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv pip install asyncpg --dry-run 2>&1 | head -5`
Expected: Shows asyncpg would be installed (no conflict)

- [ ] **Step 5: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/agent.toml pyproject.toml Dockerfile
git commit -m "chore: add asyncpg optional dependency and PG storage config"
```

---

## Task 10: Helm — Secrets, Probes, Grace Period

**Files:**
- Modify: `helm/values.yaml`
- Modify: `helm/templates/deployment.yaml`

- [ ] **Step 1: Update helm/values.yaml**

Append to the end of `helm/values.yaml` (after the `sandbox` section):

```yaml

# --- Production Features ---

agent:
  # ...existing agent config above...
  secretRef: ""          # Bulk-inject all keys from a K8S Secret
  secretEnv: []          # Selective secret injection
  # Example:
  # secretEnv:
  #   - name: DATABASE_URL
  #     secretKeyRef:
  #       name: coding-agent-secrets
  #       key: database-url
  #   - name: AGENT_SESSION_REDIS_URL
  #     secretKeyRef:
  #       name: coding-agent-secrets
  #       key: redis-url

terminationGracePeriodSeconds: 600
```

Also add `secretRef` and `secretEnv` to the existing `agent:` block at the appropriate location. The final `agent` block should include:

```yaml
agent:
  dataDir: /var/lib/coding-agent/data
  workspaceDir: /workspace
  extraEnv: []
  secretRef: ""
  secretEnv: []
  config:
    # ... existing config unchanged ...
```

- [ ] **Step 2: Update deployment.yaml — secrets injection**

In `helm/templates/deployment.yaml`, add after the `env:` block (after line 49, before `volumeMounts:`):

```yaml
          {{- if .Values.agent.secretRef }}
          envFrom:
            - secretRef:
                name: {{ .Values.agent.secretRef }}
          {{- end }}
          {{- range .Values.agent.secretEnv }}
            - name: {{ .name }}
              valueFrom:
                secretKeyRef:
                  name: {{ .secretKeyRef.name }}
                  key: {{ .secretKeyRef.key }}
          {{- end }}
```

- [ ] **Step 3: Update deployment.yaml — probe paths**

Change the readiness probe path (line 71) from `/health` to `/readyz`:
```yaml
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
```

Change the liveness probe path (line 78) from `/health` to `/healthz`:
```yaml
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
```

- [ ] **Step 4: Add terminationGracePeriodSeconds**

In `helm/templates/deployment.yaml`, add under `spec.template.spec` (after `securityContext`, before `containers`):

```yaml
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds | default 600 }}
```

- [ ] **Step 5: Verify Helm template renders**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && helm template test-release helm/ 2>&1 | head -60`
Expected: Renders without error, shows the updated deployment

- [ ] **Step 6: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add helm/values.yaml helm/templates/deployment.yaml
git commit -m "feat(helm): secretRef/secretEnv, healthz/readyz probes, grace period"
```

---

## Task 11: Helm — HPA + PDB

**Files:**
- Create: `helm/templates/hpa.yaml`
- Create: `helm/templates/pdb.yaml`
- Modify: `helm/values.yaml` (append)

- [ ] **Step 1: Add autoscaling and PDB values**

Append to `helm/values.yaml`:

```yaml

autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

podDisruptionBudget:
  enabled: false
  minAvailable: 1
```

- [ ] **Step 2: Create HPA template**

```yaml
# helm/templates/hpa.yaml
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "coding-agent.fullname" . }}
  labels:
    {{- include "coding-agent.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "coding-agent.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
{{- end }}
```

- [ ] **Step 3: Create PDB template**

```yaml
# helm/templates/pdb.yaml
{{- if .Values.podDisruptionBudget.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "coding-agent.fullname" . }}
  labels:
    {{- include "coding-agent.labels" . | nindent 4 }}
spec:
  minAvailable: {{ .Values.podDisruptionBudget.minAvailable }}
  selector:
    matchLabels:
      {{- include "coding-agent.selectorLabels" . | nindent 6 }}
{{- end }}
```

- [ ] **Step 4: Verify templates render correctly**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && helm template test-release helm/ --set autoscaling.enabled=true --set podDisruptionBudget.enabled=true 2>&1 | grep -A 20 "kind: HorizontalPodAutoscaler"`
Expected: Shows HPA manifest with correct values

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && helm template test-release helm/ --set autoscaling.enabled=true --set podDisruptionBudget.enabled=true 2>&1 | grep -A 10 "kind: PodDisruptionBudget"`
Expected: Shows PDB manifest with minAvailable: 1

- [ ] **Step 5: Verify disabled by default (no output for HPA/PDB)**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && helm template test-release helm/ 2>&1 | grep "HorizontalPodAutoscaler\|PodDisruptionBudget"`
Expected: No output (both disabled by default)

- [ ] **Step 6: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add helm/templates/hpa.yaml helm/templates/pdb.yaml helm/values.yaml
git commit -m "feat(helm): add HPA and PodDisruptionBudget templates"
```

---

## Task 12: Integration Wiring + Final Verification

**Files:**
- Modify: `src/coding_agent/app.py:153-162`
- Modify: `src/coding_agent/__main__.py:310-318`

- [ ] **Step 1: Wire PG pool into StoragePlugin in app.py**

In `src/coding_agent/app.py`, modify the `plugin_factories` dict. Change the `"storage"` entry (around line 161) from:

```python
"storage": lambda: StoragePlugin(data_dir=data_dir),
```

to:

```python
"storage": lambda: StoragePlugin(
    data_dir=data_dir,
    backend=storage_cfg.get("backend", "jsonl"),
    pg_pool=pg_pool,
),
```

And add before `plugin_factories` definition (around line 148), add:

```python
storage_cfg = cfg.extra.get("storage", {})

# Create PG pool if backend is "pg"
pg_pool = None
if storage_cfg.get("backend") == "pg":
    dsn = os.environ.get("DATABASE_URL", storage_cfg.get("dsn", ""))
    if dsn:
        from agentkit.storage.pg import PGPool
        pool_min = int(storage_cfg.get("pool_min_size", 2))
        pool_max = int(storage_cfg.get("pool_max_size", 10))
        pg_pool = PGPool(dsn=dsn, min_size=pool_min, max_size=pool_max)
```

- [ ] **Step 2: Wire PG pool into HTTP server**

In `src/coding_agent/__main__.py`, modify the `serve` command (lines 310-318):

```python
@main.command()
@click.option("--port", default=8080, help="Server port")
@click.option("--host", default="127.0.0.1", help="Server host")
def serve(port: int, host: str):
    """Start HTTP API server."""
    import uvicorn
    from coding_agent.ui.http_server import app

    # Wire PG pool into app state if DATABASE_URL is set
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        from agentkit.storage.pg import PGPool
        app.state.pg_pool = PGPool(dsn=database_url)

    click.echo(f"Starting Coding Agent HTTP server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
```

Add `import os` to the imports at the top of `__main__.py` if not already present.

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ -v --ignore=tests/integration 2>&1 | tail -30`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/app.py src/coding_agent/__main__.py
git commit -m "feat(coding-agent): wire PG pool into StoragePlugin and HTTP server"
```

---

## Task 13: SQL Schema File

**Files:**
- Create: `sql/001_init.sql`

- [ ] **Step 1: Create the schema file**

```sql
-- sql/001_init.sql
-- Batch 1: Core tables for multi-Pod deployment
-- Apply manually or via future Alembic migration (Batch 4)

CREATE TABLE IF NOT EXISTS tape_entries (
    tape_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    entry      JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tape_id, seq)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at DESC);
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
mkdir -p sql
git add sql/001_init.sql
git commit -m "chore: add SQL schema for PG storage backend"
```

---

## Verification Checklist

After all tasks are complete, verify each spec criterion:

- [ ] `replicaCount: 2` renders in `helm template` output
- [ ] `PGSessionLock.acquire()` calls `pg_advisory_lock(hashtext($1))`
- [ ] `PGTapeStore.save()` uses `INSERT` (append-only, no version column)
- [ ] `/healthz` returns `{"status": "degraded"}` when PG ping fails
- [ ] `/readyz` returns 503 when `app.state.draining = True`
- [ ] Rate limiter reads `AGENT_SESSION_REDIS_URL` at startup
- [ ] No secrets in `helm/values.yaml` defaults
- [ ] HPA + PDB disabled by default, enabled with `--set`
- [ ] `backend = "jsonl"` still works (StoragePlugin default)
- [ ] All existing tests still pass
