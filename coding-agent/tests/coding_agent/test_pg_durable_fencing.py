from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import patch

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot

from coding_agent.pg_durable_store import (
    FencedPGCheckpointStore,
    FencedPGRuntimeStore,
    FencedPGTapeStore,
    PGDurableStore,
)
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnerStore,
    SessionOwnershipConflictError,
)
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.server.stores.session_store import PGSessionMetadataStore


class FakePGConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []
        self.owner_row: dict[str, object] | None = None
        self.run_session_id = "session-a"
        self.session_tape_by_session: dict[str, str] = {}
        self.session_by_tape: dict[str, str] = {}
        self.event_row: dict[str, object] | None = None
        self.run_row: dict[str, object] | None = None
        self.snapshot_row: dict[str, object] | None = None
        self.interaction_row: dict[str, object] | None = None
        self.checkpoint_row: dict[str, object] | None = {
            "checkpoint_id": "checkpoint-a"
        }
        self.owned_sql_returns_none = False
        self.session_tape_race_binding: tuple[str, str] | None = None

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append(("execute", _sql_label(query), args))
        if "INSERT INTO session_owners" in query:
            if self.owner_row is None:
                self.owner_row = {
                    "owner_id": args[1],
                    "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
                    "fencing_token": args[3],
                }
            return "INSERT 0 1"
        if "UPDATE session_owners" in query:
            self.owner_row = {
                "owner_id": args[1],
                "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
                "fencing_token": args[3],
            }
            return "UPDATE 1"
        if "INSERT INTO session_tapes" in query:
            session_id = cast(str, args[0])
            tape_id = cast(str, args[1])
            if self.session_tape_race_binding is not None:
                raced_session_id, raced_tape_id = self.session_tape_race_binding
                self.session_tape_by_session[raced_session_id] = raced_tape_id
                self.session_by_tape[raced_tape_id] = raced_session_id
                return "INSERT 0 0"
            if (
                session_id not in self.session_tape_by_session
                and tape_id not in self.session_by_tape
            ):
                self.session_tape_by_session[session_id] = tape_id
                self.session_by_tape[tape_id] = session_id
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.calls.append(("fetchrow", _sql_label(query), args))
        if "FROM session_owners" in query:
            return self.owner_row
        if "FROM session_tapes" in query and "WHERE session_id" in query:
            tape_id = self.session_tape_by_session.get(cast(str, args[0]))
            return None if tape_id is None else {"tape_id": tape_id}
        if "FROM session_tapes" in query and "WHERE tape_id" in query:
            session_id = self.session_by_tape.get(cast(str, args[0]))
            return None if session_id is None else {"session_id": session_id}
        if "FROM agent_runs" in query:
            return {"session_id": self.run_session_id}
        if (
            "INSERT INTO agent_runs" in query
            or "INSERT INTO runtime_events" in query
            or "INSERT INTO run_message_snapshots" in query
            or "INSERT INTO agent_interactions" in query
            or "INSERT INTO agent_checkpoints" in query
        ):
            if self.owned_sql_returns_none:
                return None
        if "INSERT INTO agent_runs" in query:
            assert self.run_row is not None
            return self.run_row
        if "INSERT INTO runtime_events" in query:
            assert self.event_row is not None
            return self.event_row
        if "INSERT INTO run_message_snapshots" in query:
            assert self.snapshot_row is not None
            return self.snapshot_row
        if "INSERT INTO agent_interactions" in query:
            assert self.interaction_row is not None
            return self.interaction_row
        if "INSERT INTO agent_checkpoints" in query:
            return self.checkpoint_row
        return None

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.calls.append(("fetch", _sql_label(query), args))
        return []


class FakePGPool:
    def __init__(self) -> None:
        self.connection = FakePGConnection()
        self.released: list[FakePGConnection] = []
        self.schema_queries: list[str] = []

    async def get_pool(self) -> FakePGPool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        del args
        self.schema_queries.append(_sql_label(query))
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        return await self.connection.fetchrow(query, *args)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        return await self.connection.fetch(query, *args)

    async def acquire(self) -> FakePGConnection:
        return self.connection

    async def release(self, connection: FakePGConnection) -> None:
        self.released.append(connection)

    async def close(self) -> None:
        return None


class FakePGTapeStore:
    def __init__(self, *, pool: FakePGPool) -> None:
        self.pool = pool

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        del tape_id
        return []

    async def save(
        self,
        tape_id: str,
        entries: list[dict[str, object]],
    ) -> None:
        del tape_id, entries


class FakePGCheckpointStore:
    def __init__(self, *, pool: FakePGPool) -> None:
        self.pool = pool


class FakePGRuntimeStore:
    def __init__(self, *, pool: FakePGPool) -> None:
        self.pool = pool


@pytest.mark.asyncio
async def test_pg_owner_store_acquire_authority_allocates_db_epoch() -> None:
    pool = FakePGPool()
    store = SessionOwnerStore(pg_pool=cast(Any, pool))

    authority = await store.acquire_authority(
        "session-a",
        "owner-a",
        lease_seconds=30.0,
    )

    assert authority == OwnerAuthority("session-a", "owner-a", 1)
    labels = [call[1] for call in pool.connection.calls]
    assert labels == [
        "BEGIN",
        "INSERT INTO session_owners (session_id, owner_id, lease_expires_at, fencing_token) VALUES ($1, $2, NOW() + make_interval(secs => $3), $4) ON CONFLICT (session_id) DO NOTHING",
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners WHERE session_id = $1 FOR UPDATE",
        "COMMIT",
    ]
    assert any(
        "CREATE TABLE IF NOT EXISTS session_owners" in query
        for query in pool.schema_queries
    )
    assert pool.released == [pool.connection]


@pytest.mark.asyncio
async def test_pg_owner_store_takeover_advances_expired_epoch() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) - timedelta(seconds=1),
        "fencing_token": 4,
    }
    store = SessionOwnerStore(pg_pool=cast(Any, pool))

    authority = await store.acquire_authority(
        "session-a",
        "owner-b",
        lease_seconds=30.0,
    )

    assert authority == OwnerAuthority("session-a", "owner-b", 5)
    labels = [call[1] for call in pool.connection.calls]
    assert (
        "UPDATE session_owners SET owner_id = $2, lease_expires_at = NOW() + make_interval(secs => $3), fencing_token = $4, updated_at = NOW() WHERE session_id = $1"
        in labels
    )


@pytest.mark.asyncio
async def test_pg_durable_runtime_event_checks_owner_and_run_in_one_transaction() -> (
    None
):
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.event_row = {
        "sequence": 1,
        "event_id": "event-a",
        "run_id": "run-a",
        "event_kind": "display",
        "payload": {"ok": True},
        "created_at": datetime.now(UTC),
    }
    store = PGDurableStore(pool=cast(Any, pool))

    event = await store.append_runtime_event(
        OwnerAuthority("session-a", "owner-a", 7),
        RuntimeEventRecord(
            sequence=1,
            event_id="event-a",
            run_id="run-a",
            event_kind="display",
            payload={"ok": True},
            created_at=cast(datetime, pool.connection.event_row["created_at"]),
        ),
    )

    assert event.sequence == 1
    labels = [call[1] for call in pool.connection.calls]
    assert labels == [
        "BEGIN",
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners WHERE session_id = $1 FOR UPDATE",
        "SELECT session_id FROM agent_runs WHERE run_id = $1 FOR UPDATE",
        "WITH inserted AS ( INSERT INTO runtime_events ( event_id, run_id, event_kind, payload, created_at ) VALUES ($1, $2, $3, $4::jsonb, $5) ON CONFLICT (event_id) DO NOTHING RETURNING * ) SELECT * FROM inserted UNION ALL SELECT runtime_events.* FROM runtime_events WHERE runtime_events.event_id = $1 AND runtime_events.run_id = $2 AND NOT EXISTS (SELECT 1 FROM inserted)",
        "COMMIT",
    ]


@pytest.mark.asyncio
async def test_pg_durable_runtime_event_rejects_cross_session_run_before_mutation() -> (
    None
):
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.run_session_id = "session-b"
    store = PGDurableStore(pool=cast(Any, pool))

    with pytest.raises(SessionOwnershipConflictError):
        await store.append_runtime_event(
            OwnerAuthority("session-a", "owner-a", 7),
            RuntimeEventRecord(
                sequence=1,
                event_id="event-a",
                run_id="run-b",
                event_kind="display",
                payload={"ok": True},
                created_at=datetime.now(UTC),
            ),
        )

    labels = [call[1] for call in pool.connection.calls]
    assert "ROLLBACK" in labels
    assert not any("INSERT INTO runtime_events" in label for label in labels)


@pytest.mark.asyncio
async def test_pg_durable_session_save_binds_unique_tape_in_transaction() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    store = PGDurableStore(pool=cast(Any, pool))

    await store.save_session(
        OwnerAuthority("session-a", "owner-a", 7),
        {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
    )

    labels = [call[1] for call in pool.connection.calls]
    assert labels[:6] == [
        "BEGIN",
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners WHERE session_id = $1 FOR UPDATE",
        "INSERT INTO session_tapes (session_id, tape_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        "SELECT tape_id FROM session_tapes WHERE session_id = $1 FOR UPDATE",
        "SELECT session_id FROM session_tapes WHERE tape_id = $1 FOR UPDATE",
        "SELECT payload FROM agent_http_sessions WHERE session_id = $1 FOR UPDATE",
    ]


@pytest.mark.asyncio
async def test_pg_durable_session_save_rejects_duplicate_tape_binding() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-b",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 3,
    }
    pool.connection.session_by_tape["tape-a"] = "session-a"
    store = PGDurableStore(pool=cast(Any, pool))

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_session(
            OwnerAuthority("session-b", "owner-b", 3),
            {"id": "session-b", "session_id": "session-b", "tape_id": "tape-a"},
        )


@pytest.mark.asyncio
async def test_pg_durable_session_save_rejects_first_bind_race_rebind() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.session_tape_race_binding = ("session-a", "tape-other")
    store = PGDurableStore(pool=cast(Any, pool))

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_session(
            OwnerAuthority("session-a", "owner-a", 7),
            {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
        )

    labels = [call[1] for call in pool.connection.calls]
    assert "ROLLBACK" in labels


@pytest.mark.asyncio
async def test_pg_durable_runtime_event_rejects_existing_cross_run_event_id() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.owned_sql_returns_none = True
    store = PGDurableStore(pool=cast(Any, pool))

    with pytest.raises(SessionOwnershipConflictError):
        await store.append_runtime_event(
            OwnerAuthority("session-a", "owner-a", 7),
            RuntimeEventRecord(
                event_id="event-duplicate",
                run_id="run-a",
                event_kind="display",
                payload={"ok": True},
                created_at=datetime.now(UTC),
            ),
        )

    labels = [call[1] for call in pool.connection.calls]
    assert "ROLLBACK" in labels


@pytest.mark.asyncio
async def test_pg_durable_owned_conflict_paths_reject_cross_session_targets() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.session_by_tape["tape-a"] = "session-a"
    pool.connection.owned_sql_returns_none = True
    store = PGDurableStore(pool=cast(Any, pool))
    authority = OwnerAuthority("session-a", "owner-a", 7)
    now = datetime.now(UTC)

    with pytest.raises(SessionOwnershipConflictError):
        await store.create_agent_run(
            authority,
            AgentRunRecord(
                run_id="run-a",
                session_id="session-a",
                tape_id="tape-a",
                parent_run_id=None,
                agent_id="agent-a",
                status="running",
                started_at=now,
            ),
        )

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_message_snapshot(
            authority,
            RunMessageSnapshotRecord(
                snapshot_id="snapshot-a",
                run_id="run-a",
                messages=[],
                metadata={},
                created_at=now,
            ),
        )

    with pytest.raises(SessionOwnershipConflictError):
        await store.create_agent_interaction(
            authority,
            AgentInteractionRecord(
                interaction_id="interaction-a",
                run_id="run-a",
                interaction_kind="approval",
                status="pending",
                request_payload={},
                response_payload={},
                metadata={},
                created_at=now,
            ),
        )

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_checkpoint(
            authority,
            CheckpointSnapshot(
                meta=CheckpointMeta(
                    checkpoint_id="checkpoint-a",
                    tape_id="tape-a",
                    session_id="session-a",
                    entry_count=0,
                    window_start=0,
                    created_at=now,
                    label="owned",
                ),
                tape_entries=(),
                plugin_states={},
            ),
        )


@pytest.mark.asyncio
async def test_pg_durable_create_run_requires_stable_tape_binding() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    store = PGDurableStore(pool=cast(Any, pool))

    with pytest.raises(SessionOwnershipConflictError):
        await store.create_agent_run(
            OwnerAuthority("session-a", "owner-a", 7),
            AgentRunRecord(
                run_id="run-no-tape",
                session_id="session-a",
                tape_id=None,
                parent_run_id=None,
                agent_id="agent-a",
                status="running",
                started_at=datetime.now(UTC),
            ),
        )

    assert not pool.connection.calls


def test_session_manager_enables_fenced_pg_wrappers_after_owner_store_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pg_pool = FakePGPool()
    owner_store = SessionOwnerStore(pg_pool=cast(Any, pg_pool))
    with (
        patch(
            "coding_agent.server.session_manager.create_session_store"
        ) as create_store,
        patch(
            "coding_agent.server.session_manager._load_pg_storage_types",
            return_value=(lambda **_: pg_pool, FakePGTapeStore, FakePGCheckpointStore),
        ),
        patch(
            "coding_agent.server.session_manager.PGRuntimeStore",
            FakePGRuntimeStore,
        ),
        caplog.at_level("WARNING", logger="coding_agent.server.session_manager"),
    ):
        create_store.return_value = PGSessionMetadataStore(pool=cast(Any, pg_pool))
        manager = SessionManager(
            storage_config={
                "http_session_backend": "pg",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "runtime_backend": "pg",
                "dsn": "postgresql://example",
            },
            pg_pool=cast(Any, pg_pool),
            owner_store=owner_store,
            owner_id="server-a",
            fencing_token=1,
        )

    assert manager._pg_durable_store is not None
    assert isinstance(manager._tape_store, FencedPGTapeStore)
    assert isinstance(manager._checkpoint_service._store, FencedPGCheckpointStore)
    assert isinstance(manager._runtime_store, FencedPGRuntimeStore)
    assert "durable fencing disabled" not in caplog.text


def test_session_manager_warns_when_custom_store_disables_pg_fencing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pg_pool = FakePGPool()
    owner_store = SessionOwnerStore(pg_pool=cast(Any, pg_pool))

    with (
        patch(
            "coding_agent.server.session_manager._load_pg_storage_types",
            return_value=(
                lambda **_: pg_pool,
                FakePGTapeStore,
                FakePGCheckpointStore,
            ),
        ),
        patch(
            "coding_agent.server.session_manager.PGRuntimeStore",
            FakePGRuntimeStore,
        ),
        caplog.at_level("WARNING", logger="coding_agent.server.session_manager"),
    ):
        manager = SessionManager(
            storage_config={
                "http_session_backend": "pg",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "runtime_backend": "pg",
                "dsn": "postgresql://example",
            },
            pg_pool=cast(Any, pg_pool),
            store=InMemorySessionStore(),
            owner_store=owner_store,
            owner_id="server-a",
            fencing_token=1,
        )

    assert manager._pg_durable_store is None
    assert "durable fencing disabled: custom store supplied" in caplog.text
    assert caplog.text.count("durable fencing disabled") == 1


def _sql_label(query: str) -> str:
    return " ".join(query.split())
