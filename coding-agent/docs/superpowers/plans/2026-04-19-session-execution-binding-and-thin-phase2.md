# Session Execution Binding and Thin Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class execution binding abstraction that separates WHERE a session runs from WHO owns it, then build the thin Phase 2 substrate (owners, leases, fencing) on top of Phase 1 PostgreSQL persistence.

**Architecture:** Introduce `ExecutionBinding` as a typed dataclass hierarchy in `coding_agent.ui.execution_binding`, with a `BindingResolver` that converts bindings into `workspace_root` for `create_agent_for_session`. Layer ADR-0013's owner/lease/fencing on top via `SessionOwnerStore` in `agentkit.storage.pg`. Keep runtime state local-authoritative and do not attempt cross-owner serialization.

**Tech Stack:** Python 3.12, `uv`, `pytest`, PostgreSQL (via `asyncpg`), FastAPI, existing `agentkit` pipeline.

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/coding_agent/ui/execution_binding.py` | `ExecutionBinding` base class, `LocalExecutionBinding`, `CloudWorkspaceBinding`, serialization helpers |
| `src/coding_agent/ui/binding_resolver.py` | `BindingResolver` protocol and concrete resolver that turns bindings into `workspace_root` and tool config |
| `src/coding_agent/ui/session_owner_store.py` | App-layer `SessionOwnerStore` with acquire/renew/release and fencing token primitives |
| `src/agentkit/storage/pg.py` | PostgreSQL `session_owners` table schema, SQL for atomic acquire/renew/release |
| `src/coding_agent/ui/session_manager.py` | Integrate binding resolution into `run_agent`, `ensure_session_runtime`, checkpoint restore; add owner checks |
| `src/coding_agent/ui/http_server.py` | Add owner checks to prompt/approve/restore endpoints; sticky routing guards |
| `tests/ui/test_execution_binding.py` | Binding serialization, deserialization, resolver behavior |
| `tests/ui/test_session_owner_store.py` | Owner acquire/renew/release, fencing token validation, lease expiry |
| `tests/ui/test_session_manager_owner_checks.py` | Owner checks reject stale owners in `run_agent`, restore, close |
| `tests/ui/test_http_server_failover.py` | Sticky routing rejection, stale-owner 409/403 responses |
| `tests/agentkit/storage/test_pg.py` | `PGSessionOwnerStore` round-trip, atomic conflict behavior |

---

## Scope Exclusions

- **In-flight turn resume is out of scope.** After owner loss, a new owner rebuilds cold state from persisted data but does not resume partially executed runtime state. This matches ADR-0013's at-most-once contract.
- **Cross-instance event forwarding is out of scope for the first slice.** This plan still includes local HTTP binding integration plus stale-owner/failover boundary checks, but not brokered routing between instances.
- **Cloud workspace tool implementations are out of scope.** We define `CloudWorkspaceBinding` but do not implement cloud file/shell tools.

---

## Current Risks in `SessionManager` and `http_server.py`

Before implementing, the engineer should understand these local-only assumptions that make multi-instance deployment unsafe today:

1. **Local approval state:** `session.approval_event` is an `asyncio.Event` in local memory. If the owner changes, the old event is lost and the new owner has no signal.
2. **Local event queues:** `session.event_queues` is a list of `asyncio.Queue` objects. They do not survive owner change and cannot be reached from another instance.
3. **Local task/runtime ownership:** `session.task`, `session.runtime_pipeline`, `session.runtime_ctx`, and `session.runtime_adapter` are all local objects. They are not serializable across owners.
4. **`repo_path` semantic mismatch:** `repo_path` is a `Path` on the `Session` object. It is passed to `create_agent_for_session` as `workspace_root`. In a cloud workspace scenario, `repo_path` cannot represent a remote URL, and conditional string parsing is fragile.

The binding abstraction fixes risk 4. The owner/lease/fencing layer fixes risks 1-3 by making owner-sensitive actions fail fast when the local instance is no longer the owner.

---

## Task 1: Introduce Execution Binding Abstraction

**Files:**
- Create: `src/coding_agent/ui/execution_binding.py`
- Create: `tests/ui/test_execution_binding.py`

- [ ] **Step 1: Write the binding dataclasses**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class ExecutionBinding:
    kind: ClassVar[Literal["local", "cloud"]]

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionBinding:
        kind = data.get("kind")
        if kind == "local":
            return LocalExecutionBinding.from_dict(data)
        if kind == "cloud":
            return CloudWorkspaceBinding.from_dict(data)
        raise ValueError(f"unknown binding kind: {kind}")


@dataclass(frozen=True)
class LocalExecutionBinding(ExecutionBinding):
    workspace_root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", "local")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "local", "workspace_root": self.workspace_root}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalExecutionBinding:
        root = data.get("workspace_root")
        if not isinstance(root, str):
            raise TypeError("local binding requires string workspace_root")
        return cls(workspace_root=root)


@dataclass(frozen=True)
class CloudWorkspaceBinding(ExecutionBinding):
    workspace_url: str
    workspace_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", "cloud")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "cloud",
            "workspace_url": self.workspace_url,
            "workspace_id": self.workspace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloudWorkspaceBinding:
        url = data.get("workspace_url")
        wid = data.get("workspace_id")
        if not isinstance(url, str) or not isinstance(wid, str):
            raise TypeError("cloud binding requires string workspace_url and workspace_id")
        return cls(workspace_url=url, workspace_id=wid)
```

- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path

import pytest

from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


def test_local_binding_round_trip() -> None:
    binding = LocalExecutionBinding(workspace_root="/tmp/repo")
    restored = ExecutionBinding.from_dict(binding.to_dict())
    assert isinstance(restored, LocalExecutionBinding)
    assert restored.workspace_root == "/tmp/repo"


def test_cloud_binding_round_trip() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    restored = ExecutionBinding.from_dict(binding.to_dict())
    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.workspace_url == "https://workspace.example.com"
    assert restored.workspace_id == "ws-123"


def test_unknown_binding_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown binding kind"):
        ExecutionBinding.from_dict({"kind": "unknown"})


def test_local_binding_requires_string_workspace_root() -> None:
    with pytest.raises(TypeError, match="string workspace_root"):
        LocalExecutionBinding.from_dict({"kind": "local", "workspace_root": 123})
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_execution_binding.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/ui/execution_binding.py tests/ui/test_execution_binding.py
git commit -m "feat: add ExecutionBinding abstraction for local and cloud workspaces"
```

---

## Task 2: Add Binding Resolver

**Files:**
- Create: `src/coding_agent/ui/binding_resolver.py`
- Modify: `tests/ui/test_execution_binding.py`

- [ ] **Step 1: Write the resolver**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


class BindingResolver(Protocol):
    def resolve_workspace_root(self, binding: ExecutionBinding) -> Path: ...

    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, Any]: ...


class DefaultBindingResolver:
    def resolve_workspace_root(self, binding: ExecutionBinding) -> Path:
        if isinstance(binding, LocalExecutionBinding):
            return Path(binding.workspace_root).resolve()
        if isinstance(binding, CloudWorkspaceBinding):
            raise NotImplementedError(
                "cloud workspace resolution is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")

    def resolve_tool_config(self, binding: ExecutionBinding) -> dict[str, Any]:
        if isinstance(binding, LocalExecutionBinding):
            return {"workspace_root": str(self.resolve_workspace_root(binding))}
        if isinstance(binding, CloudWorkspaceBinding):
            raise NotImplementedError(
                "cloud workspace tool config is not yet implemented"
            )
        raise ValueError(f"unsupported binding type: {type(binding).__name__}")
```

- [ ] **Step 2: Add resolver tests**

Append to `tests/ui/test_execution_binding.py`:

```python
from pathlib import Path

from coding_agent.ui.binding_resolver import DefaultBindingResolver


def test_local_resolver_returns_absolute_path() -> None:
    binding = LocalExecutionBinding(workspace_root="/tmp/repo")
    resolver = DefaultBindingResolver()
    assert resolver.resolve_workspace_root(binding) == Path("/tmp/repo").resolve()


def test_cloud_resolver_raises_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()
    with pytest.raises(NotImplementedError):
        resolver.resolve_workspace_root(binding)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ui/test_execution_binding.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/ui/binding_resolver.py tests/ui/test_execution_binding.py
git commit -m "feat: add DefaultBindingResolver for local execution"
```

---

## Task 3: Integrate Binding into Session Metadata

**Files:**
- Modify: `src/coding_agent/ui/session_manager.py`
- Modify: `tests/ui/test_session_manager_public_api.py`

- [ ] **Step 1: Add binding field to `Session`**

In `src/coding_agent/ui/session_manager.py`, import `ExecutionBinding` and `LocalExecutionBinding`, then modify the `Session` dataclass:

```python
from coding_agent.ui.execution_binding import ExecutionBinding, LocalExecutionBinding
```

Add to `Session`:

```python
    execution_binding: ExecutionBinding = field(
        default_factory=lambda: LocalExecutionBinding(workspace_root=str(Path.cwd()))
    )
```

- [ ] **Step 2: Update `to_store_data` and `from_store_data`**

In `to_store_data`, add:

```python
            "execution_binding": self.execution_binding.to_dict(),
```

In `from_store_data`, after `tape_id_raw` handling, add:

```python
        binding_raw = data.get("execution_binding")
        if binding_raw is not None:
            if not isinstance(binding_raw, dict):
                raise TypeError("session metadata has invalid execution_binding")
            execution_binding = ExecutionBinding.from_dict(binding_raw)
        else:
            execution_binding = LocalExecutionBinding(
                workspace_root=str(
                    Path(repo_path_raw).resolve()
                    if repo_path_raw is not None
                    else Path.cwd()
                )
            )
```

Pass `execution_binding=execution_binding` into the `cls(...)` constructor.

- [ ] **Step 3: Update `create_session` to store the binding**

In `create_session`, after `repo_path` is resolved, set:

```python
        binding = LocalExecutionBinding(
            workspace_root=str(repo_path.resolve()) if repo_path else str(Path.cwd())
        )
```

Pass `execution_binding=binding` into the `Session(...)` constructor.

- [ ] **Step 4: Update `run_agent` to resolve workspace from binding**

In `run_agent`, replace the direct `session.repo_path` usage with resolver output:

```python
                from coding_agent.ui.binding_resolver import DefaultBindingResolver
                resolver = DefaultBindingResolver()
                workspace_root = resolver.resolve_workspace_root(
                    session.execution_binding
                )
                pipeline, ctx = self._create_agent_for_session(
                    workspace_root=workspace_root,
                    ...
                )
```

Do the same in `ensure_session_runtime` and `_restore_checkpoint`.

- [ ] **Step 5: Write the failing tests**

Add to `tests/ui/test_session_manager_public_api.py`:

```python
from coding_agent.ui.execution_binding import ExecutionBinding, LocalExecutionBinding


def test_session_metadata_round_trips_local_execution_binding() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="binding-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        execution_binding=LocalExecutionBinding(workspace_root="/tmp/bound-repo"),
    )
    manager.register_session(session)

    reloaded = SessionManager(store=store).get_session("binding-session")
    assert isinstance(reloaded.execution_binding, LocalExecutionBinding)
    assert reloaded.execution_binding.workspace_root == "/tmp/bound-repo"


def test_session_metadata_defaults_missing_binding_to_local_from_repo_path() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="legacy-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        repo_path=Path("/tmp/legacy-repo"),
    )
    # Do not set execution_binding; simulate pre-binding metadata
    store.save(session.id, {
        "id": session.id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "repo_path": "/tmp/legacy-repo",
        "approval_policy": "auto",
        "provider_name": None,
        "model_name": None,
        "base_url": None,
        "max_steps": 30,
        "tape_id": None,
    })

    reloaded = SessionManager(store=store).get_session("legacy-session")
    assert isinstance(reloaded.execution_binding, LocalExecutionBinding)
    assert reloaded.execution_binding.workspace_root == "/tmp/legacy-repo"
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/ui/test_session_manager_public_api.py -k "binding" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_public_api.py
git commit -m "feat: integrate ExecutionBinding into Session metadata"
```

---

## Task 4: Add Thin Phase 2 Substrate (Session Owners, Lease, Fencing)

**Files:**
- Create: `src/coding_agent/ui/session_owner_store.py`
- Modify: `src/agentkit/storage/pg.py`
- Create: `tests/ui/test_session_owner_store.py`
- Modify: `tests/agentkit/storage/test_pg.py`

- [ ] **Step 1: Add `session_owners` table to `agentkit.storage.pg`**

In `src/agentkit/storage/pg.py`, add a new class `PGSessionOwnerStore` after `PGCheckpointStore`:

```python
class PGSessionOwnerStore:
    _CREATE_TABLE_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS session_owners (
        session_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        lease_expires_at TIMESTAMPTZ NOT NULL,
        fencing_token BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """

    _ACQUIRE_SQL: Final[str] = """
    INSERT INTO session_owners (session_id, owner_id, lease_expires_at, fencing_token)
    VALUES ($1, $2, NOW() + $3::interval, $4)
    ON CONFLICT (session_id) DO NOTHING
    """

    _RENEW_SQL: Final[str] = """
    UPDATE session_owners
    SET lease_expires_at = NOW() + $1::interval,
        fencing_token = $2,
        updated_at = NOW()
    WHERE session_id = $3
      AND owner_id = $4
      AND lease_expires_at > NOW()
      AND fencing_token = $5
    """

    _RELEASE_SQL: Final[str] = """
    DELETE FROM session_owners
    WHERE session_id = $1
      AND owner_id = $2
      AND fencing_token = $3
    """

    _GET_SQL: Final[str] = (
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners "
        "WHERE session_id = $1"
    )

    def __init__(self, *, pool: PGPool) -> None:
        self._pool: PGPool = pool
        self._schema_ready: bool = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_TABLE_SQL)
            self._schema_ready = True
        return pool

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        fencing_token: int,
    ) -> bool:
        pool = await self._ensure_schema()
        result = await pool.execute(
            self._ACQUIRE_SQL,
            session_id,
            owner_id,
            f"{lease_seconds} seconds",
            fencing_token,
        )
        return "INSERT 0 1" in result

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        new_fencing_token: int,
        current_fencing_token: int,
    ) -> bool:
        pool = await self._ensure_schema()
        result = await pool.execute(
            self._RENEW_SQL,
            f"{lease_seconds} seconds",
            new_fencing_token,
            session_id,
            owner_id,
            current_fencing_token,
        )
        return "UPDATE 1" in result

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        pool = await self._ensure_schema()
        result = await pool.execute(
            self._RELEASE_SQL,
            session_id,
            owner_id,
            fencing_token,
        )
        return "DELETE 1" in result

    async def get_owner(self, session_id: str) -> dict[str, object] | None:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._GET_SQL, session_id)
        if row is None:
            return None
        return {
            "owner_id": row.get("owner_id"),
            "lease_expires_at": row.get("lease_expires_at"),
            "fencing_token": row.get("fencing_token"),
        }
```

- [ ] **Step 2: Add app-layer `SessionOwnerStore`**

Create `src/coding_agent/ui/session_owner_store.py`:

```python
from __future__ import annotations

from typing import Any

from agentkit.storage.pg import PGPool, PGSessionOwnerStore


class SessionOwnerStore:
    def __init__(self, *, pg_pool: PGPool) -> None:
        self._pg = PGSessionOwnerStore(pool=pg_pool)

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        return await self._pg.acquire(session_id, owner_id, lease_seconds, fencing_token)

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        return await self._pg.renew(
            session_id, owner_id, lease_seconds, new_fencing_token, current_fencing_token
        )

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        return await self._pg.release(session_id, owner_id, fencing_token)

    async def get_owner(self, session_id: str) -> dict[str, Any] | None:
        return await self._pg.get_owner(session_id)
```

- [ ] **Step 3: Write tests for owner store**

Create `tests/ui/test_session_owner_store.py`:

```python
from __future__ import annotations

import pytest

from coding_agent.ui.session_owner_store import SessionOwnerStore


class FakePGOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, dict[str, object]] = {}

    async def acquire(
        self, session_id: str, owner_id: str, lease_seconds: float, fencing_token: int
    ) -> bool:
        if session_id in self._owners:
            return False
        self._owners[session_id] = {
            "owner_id": owner_id,
            "lease_expires_at": "fake",
            "fencing_token": fencing_token,
        }
        return True

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        new_fencing_token: int,
        current_fencing_token: int,
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner["owner_id"] != owner_id or owner["fencing_token"] != current_fencing_token:
            return False
        owner["fencing_token"] = new_fencing_token
        return True

    async def release(
        self, session_id: str, owner_id: str, fencing_token: int
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner["owner_id"] != owner_id or owner["fencing_token"] != fencing_token:
            return False
        del self._owners[session_id]
        return True

    async def get_owner(self, session_id: str) -> dict[str, object] | None:
        return self._owners.get(session_id)


@pytest.fixture
def owner_store() -> SessionOwnerStore:
    store = SessionOwnerStore.__new__(SessionOwnerStore)
    store._pg = FakePGOwnerStore()
    return store


@pytest.mark.asyncio
async def test_session_owner_acquire_conflicts_with_live_lease(
    owner_store: SessionOwnerStore,
) -> None:
    assert await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)
    assert not await owner_store.acquire("s1", "owner-b", lease_seconds=30.0, fencing_token=2)


@pytest.mark.asyncio
async def test_session_owner_renew_rejects_stale_token(
    owner_store: SessionOwnerStore,
) -> None:
    await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)
    assert not await owner_store.renew(
        "s1", "owner-a", lease_seconds=30.0, new_fencing_token=3, current_fencing_token=99
    )


@pytest.mark.asyncio
async def test_session_owner_release_rejects_wrong_owner(
    owner_store: SessionOwnerStore,
) -> None:
    await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)
    assert not await owner_store.release("s1", "owner-b", fencing_token=1)
```

- [ ] **Step 4: Add PG owner store tests**

Append to `tests/agentkit/storage/test_pg.py`:

```python
class TestPGSessionOwnerStore:
    @pytest.fixture
    def fake_pool(self) -> FakePool:
        return FakePool()

    @pytest.fixture
    def pool(self, fake_pool: FakePool) -> PGPool:
        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        return PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

    @pytest.fixture
    def store(self, pool: PGPool) -> PGSessionOwnerStore:
        return PGSessionOwnerStore(pool=pool)

    @pytest.mark.asyncio
    async def test_acquire_returns_true_on_new_session(self, store: PGSessionOwnerStore):
        result = await store.acquire("s1", "owner-a", 30.0, 1)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_returns_false_on_existing_session(self, store: PGSessionOwnerStore):
        await store.acquire("s1", "owner-a", 30.0, 1)
        result = await store.acquire("s1", "owner-b", 30.0, 2)
        assert result is False

    @pytest.mark.asyncio
    async def test_renew_updates_fencing_token(self, store: PGSessionOwnerStore):
        await store.acquire("s1", "owner-a", 30.0, 1)
        result = await store.renew("s1", "owner-a", 30.0, 2, 1)
        assert result is True
        owner = await store.get_owner("s1")
        assert owner is not None
        assert owner["fencing_token"] == 2

    @pytest.mark.asyncio
    async def test_release_removes_owner(self, store: PGSessionOwnerStore):
        await store.acquire("s1", "owner-a", 30.0, 1)
        result = await store.release("s1", "owner-a", 1)
        assert result is True
        assert await store.get_owner("s1") is None
```

Update `FakePool.execute` in `tests/agentkit/storage/test_pg.py` to handle the new SQL:

```python
        if "INSERT INTO session_owners" in query:
            session_id, owner_id, lease_expires_at, fencing_token = args
            if not isinstance(session_id, str):
                raise TypeError("session_id must be a string")
            if session_id in self.sessions:
                return "INSERT 0 0"
            self.sessions[session_id] = {
                "owner_id": owner_id,
                "lease_expires_at": lease_expires_at,
                "fencing_token": fencing_token,
            }
            return "INSERT 0 1"
        if "UPDATE session_owners" in query:
            lease_expires_at, new_fencing_token, session_id, owner_id, current_fencing_token = args
            owner = self.sessions.get(session_id)
            if owner is None:
                return "UPDATE 0"
            if owner["owner_id"] != owner_id or owner["fencing_token"] != current_fencing_token:
                return "UPDATE 0"
            owner["fencing_token"] = new_fencing_token
            owner["lease_expires_at"] = lease_expires_at
            return "UPDATE 1"
        if "DELETE FROM session_owners" in query:
            session_id, owner_id, fencing_token = args
            owner = self.sessions.get(session_id)
            if owner is None:
                return "DELETE 0"
            if owner["owner_id"] != owner_id or owner["fencing_token"] != fencing_token:
                return "DELETE 0"
            del self.sessions[session_id]
            return "DELETE 1"
        if "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners" in query:
            (session_id,) = args
            owner = self.sessions.get(session_id)
            if owner is None:
                return None
            return {
                "owner_id": owner["owner_id"],
                "lease_expires_at": owner["lease_expires_at"],
                "fencing_token": owner["fencing_token"],
            }
        if "CREATE TABLE IF NOT EXISTS session_owners" in query:
            return "CREATE TABLE"
```

Also update `FakePool.fetchrow` to handle the owner SELECT:

```python
        if "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners" in query:
            (session_id,) = args
            owner = self.sessions.get(session_id)
            if owner is None:
                return None
            return {
                "owner_id": owner["owner_id"],
                "lease_expires_at": owner["lease_expires_at"],
                "fencing_token": owner["fencing_token"],
            }
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ui/test_session_owner_store.py tests/agentkit/storage/test_pg.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/storage/pg.py src/coding_agent/ui/session_owner_store.py tests/ui/test_session_owner_store.py tests/agentkit/storage/test_pg.py
git commit -m "feat: add Phase 2 session owner store with lease and fencing"
```

---

## Task 5: Add Owner Checks to `SessionManager`

**Files:**
- Modify: `src/coding_agent/ui/session_manager.py`
- Create: `tests/ui/test_session_manager_owner_checks.py`

- [ ] **Step 1: Add owner checking to `SessionManager`**

Inject an optional `owner_store` into `SessionManager.__init__`:

```python
    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        storage_config: dict[str, Any] | None = None,
        pg_pool: object | None = None,
        tape_store: TapeStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_service: CheckpointService | None = None,
        create_agent_fn: Callable[..., tuple[Any, Any]] | None = None,
        owner_store: SessionOwnerStore | None = None,
    ):
```

Store `self._owner_store = owner_store`.

Add an internal helper:

```python
    async def _assert_owner(self, session_id: str, owner_id: str, fencing_token: int) -> None:
        if self._owner_store is None:
            return
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            raise RuntimeError("session has no owner")
        if owner["owner_id"] != owner_id or owner["fencing_token"] != fencing_token:
            raise RuntimeError("stale owner or fencing token rejected")
```

Add owner checks at the start of `run_agent`, `_restore_checkpoint`, and `close_session`:

```python
    async def run_agent(self, session_id: str, prompt: str) -> None:
        await self._assert_owner(session_id, self._owner_id, self._fencing_token)
        ...
```

Note: `self._owner_id` and `self._fencing_token` should be instance-level config passed at construction or defaulted to `None` when `owner_store` is `None`.

- [ ] **Step 2: Write owner check tests**

Create `tests/ui/test_session_manager_owner_checks.py`:

```python
from __future__ import annotations

import pytest

from coding_agent.ui.session_manager import SessionManager
from coding_agent.ui.session_owner_store import SessionOwnerStore
from coding_agent.ui.session_store import InMemorySessionStore


class FakeOwnerStore(SessionOwnerStore):
    def __init__(self) -> None:
        self._owners: dict[str, dict[str, object]] = {}

    async def acquire(
        self, session_id: str, owner_id: str, lease_seconds: float, fencing_token: int
    ) -> bool:
        if session_id in self._owners:
            return False
        self._owners[session_id] = {
            "owner_id": owner_id,
            "lease_expires_at": "fake",
            "fencing_token": fencing_token,
        }
        return True

    async def get_owner(self, session_id: str) -> dict[str, object] | None:
        return self._owners.get(session_id)


@pytest.mark.asyncio
async def test_run_agent_rejects_non_owner_instance() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        owner_store=owner_store,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    # Manager has no owner_id set; run_agent should reject
    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.run_agent(session_id, "hello")


@pytest.mark.asyncio
async def test_restore_checkpoint_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        owner_store=owner_store,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.restore_checkpoint(session_id, "cp-1")


@pytest.mark.asyncio
async def test_close_session_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        owner_store=owner_store,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.close_session(session_id)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ui/test_session_manager_owner_checks.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_owner_checks.py
git commit -m "feat: add owner checks to SessionManager runtime operations"
```

---

## Task 6: Add Local-Execution Binding Integration

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`
- Modify: `tests/ui/test_http_server.py`

- [ ] **Step 1: Update `create_session` endpoint to store binding**

In `src/coding_agent/ui/http_server.py`, import `LocalExecutionBinding` and modify `create_session`:

```python
from coding_agent.ui.execution_binding import LocalExecutionBinding
```

After session creation, update the session metadata with the binding:

```python
    session = session_manager.get_session(session_id)
    session.execution_binding = LocalExecutionBinding(
        workspace_root=str(repo_path.resolve()) if repo_path else str(Path.cwd())
    )
    session_manager.register_session(session)
```

- [ ] **Step 2: Add HTTP binding tests**

Add to `tests/ui/test_http_server.py`:

```python
from coding_agent.ui.execution_binding import LocalExecutionBinding


class TestExecutionBinding:
    async def test_create_session_stores_local_binding_by_default(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        session = session_manager.get_session(session_id)
        assert isinstance(session.execution_binding, LocalExecutionBinding)
        assert session.execution_binding.workspace_root == str(Path.cwd())

    async def test_create_session_stores_local_binding_with_repo_path(self, client, tmp_path):
        response = await client.post("/sessions", json={"repo_path": str(tmp_path)})
        session_id = response.json()["session_id"]

        session = session_manager.get_session(session_id)
        assert isinstance(session.execution_binding, LocalExecutionBinding)
        assert session.execution_binding.workspace_root == str(tmp_path.resolve())
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ui/test_http_server.py -k "binding" -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/ui/http_server.py tests/ui/test_http_server.py
git commit -m "feat: store local execution binding on HTTP session creation"
```

---

## Task 7: Add Cloud-Workspace Binding (Skeleton)

**Files:**
- Modify: `src/coding_agent/ui/binding_resolver.py`
- Modify: `tests/ui/test_execution_binding.py`

- [ ] **Step 1: Extend resolver to reject cloud binding gracefully**

The resolver already raises `NotImplementedError` for cloud bindings. Add a typed exception for callers to catch:

```python
class CloudBindingNotImplementedError(NotImplementedError):
    pass
```

Update `DefaultBindingResolver` to raise `CloudBindingNotImplementedError` instead of plain `NotImplementedError`.

- [ ] **Step 2: Add cloud binding tests**

Add to `tests/ui/test_execution_binding.py`:

```python
from coding_agent.ui.binding_resolver import (
    CloudBindingNotImplementedError,
    DefaultBindingResolver,
)


def test_cloud_binding_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()
    with pytest.raises(CloudBindingNotImplementedError):
        resolver.resolve_workspace_root(binding)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ui/test_execution_binding.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/ui/binding_resolver.py tests/ui/test_execution_binding.py
git commit -m "feat: add CloudBindingNotImplementedError for future cloud workspace support"
```

---

## Task 8: Add Sticky Routing Hardening and Failover Tests

**Files:**
- Create: `tests/ui/test_http_server_failover.py`

- [ ] **Step 1: Write failover tests**

Create `tests/ui/test_http_server_failover.py`:

```python
from __future__ import annotations

import pytest

from coding_agent.ui.http_server import session_manager
from coding_agent.ui.session_owner_store import SessionOwnerStore
from coding_agent.ui.session_store import InMemorySessionStore


class FakeOwnerStore(SessionOwnerStore):
    def __init__(self) -> None:
        self._owners: dict[str, dict[str, object]] = {}

    async def acquire(
        self, session_id: str, owner_id: str, lease_seconds: float, fencing_token: int
    ) -> bool:
        if session_id in self._owners:
            return False
        self._owners[session_id] = {
            "owner_id": owner_id,
            "lease_expires_at": "fake",
            "fencing_token": fencing_token,
        }
        return True

    async def get_owner(self, session_id: str) -> dict[str, object] | None:
        return self._owners.get(session_id)


@pytest.fixture
def owner_store() -> FakeOwnerStore:
    return FakeOwnerStore()


@pytest.mark.asyncio
async def test_approval_response_is_rejected_after_owner_change(owner_store: FakeOwnerStore) -> None:
    manager = session_manager.__class__(
        store=InMemorySessionStore(),
        owner_store=owner_store,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.submit_approval(session_id, "req-1", approved=True)


@pytest.mark.asyncio
async def test_session_failover_rebuilds_from_persisted_state_without_resuming_inflight_turn(
    owner_store: FakeOwnerStore,
) -> None:
    store = InMemorySessionStore()
    first_manager = session_manager.__class__(store=store)
    session_id = await first_manager.create_session()
    await first_manager.ensure_session_runtime(session_id)

    # Simulate owner loss: new manager takes over
    await owner_store.acquire(session_id, "owner-b", 30.0, 2)
    second_manager = session_manager.__class__(
        store=store,
        owner_store=owner_store,
    )

    # The new manager can load metadata but does not resume runtime
    session = second_manager.get_session(session_id)
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/ui/test_http_server_failover.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/ui/test_http_server_failover.py
git commit -m "test: add sticky routing and failover boundary tests"
```

---

## Task 9: Final Integration Verification

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite for affected modules**

Run:
```bash
uv run pytest tests/ui/test_execution_binding.py tests/ui/test_session_owner_store.py tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_owner_checks.py tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/agentkit/storage/test_pg.py -v
```

Expected: All PASS

- [ ] **Step 2: Run the ADR-0014 acceptance criteria command**

Run:
```bash
uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "binding" -v
```

Expected: All PASS

- [ ] **Step 3: Run the ADR-0013 acceptance criteria command**

Run:
```bash
uv run pytest tests/agentkit/ tests/ui/ -k "owner or lease or fencing or failover" -v
```

Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: verify full integration of execution binding and Phase 2 substrate"
```

---

## Task Packet

When starting implementation, pass the following context to the agent:

**Goal:** Implement the execution binding abstraction and thin Phase 2 owner/lease/fencing substrate as described in ADR-0014 and this plan.

**Key files:**
- `src/coding_agent/ui/execution_binding.py` (new)
- `src/coding_agent/ui/binding_resolver.py` (new)
- `src/coding_agent/ui/session_owner_store.py` (new)
- `src/agentkit/storage/pg.py` (append `PGSessionOwnerStore`)
- `src/coding_agent/ui/session_manager.py` (integrate binding + owner checks)
- `src/coding_agent/ui/http_server.py` (store binding on create)

**Tests to add:**
- `tests/ui/test_execution_binding.py`
- `tests/ui/test_session_owner_store.py`
- `tests/ui/test_session_manager_owner_checks.py`
- `tests/ui/test_http_server_failover.py`
- Additions to `tests/ui/test_session_manager_public_api.py`
- Additions to `tests/ui/test_http_server.py`
- Additions to `tests/agentkit/storage/test_pg.py`

**Verification commands:**
```bash
uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "binding" -v
uv run pytest tests/agentkit/ tests/ui/ -k "owner or lease or fencing or failover" -v
```

**What to avoid:**
- Do not implement cloud workspace tools.
- Do not implement in-flight turn resume.
- Do not implement brokered event routing.
- Do not assume runtime objects are serializable across owners.
