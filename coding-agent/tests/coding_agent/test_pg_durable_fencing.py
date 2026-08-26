from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import patch

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot

from coding_agent.stores.durable_pg import (
    FencedPGCheckpointStore,
    FencedPGRuntimeStore,
    FencedPGTapeStore,
    PGDurableStore,
)
from coding_agent.stores.runtime_store import (
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
from coding_agent.topics.store import (
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)


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
        self.checkpoint_meta_row: dict[str, object] | None = {
            "meta": {"session_id": "session-a"}
        }
        self.agent_runs: dict[str, dict[str, object]] = {}
        self.topics: dict[str, dict[str, object]] = {}
        self.topic_anchors: list[dict[str, object]] = []
        self.topic_recall_links: list[dict[str, object]] = []
        self.topic_costs: dict[str, dict[str, object]] = {}
        self.fact_source_rows: dict[str, dict[str, object]] = {}
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
        if "UPDATE agent_runs" in query and "superseded_at IS NULL" in query:
            session_id = cast(str, args[0])
            checkpoint_created_at = cast(datetime, args[1])
            checkpoint_id = cast(str, args[2])
            for run in self.agent_runs.values():
                if (
                    run["session_id"] == session_id
                    and cast(datetime, run["started_at"]) > checkpoint_created_at
                    and run["superseded_at"] is None
                ):
                    run["superseded_by_checkpoint_id"] = checkpoint_id
                    run["superseded_at"] = datetime.now(UTC)
            return "UPDATE"
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
        if "DELETE FROM topic_recall_links" in query:
            tape_id = cast(str, args[0])
            topic_ids = self._topic_ids_for_tape(tape_id)
            self.topic_recall_links = [
                link
                for link in self.topic_recall_links
                if link["source_topic_id"] not in topic_ids
                and link["recalled_topic_id"] not in topic_ids
            ]
            return "DELETE"
        if "DELETE FROM topic_costs" in query:
            tape_id = cast(str, args[0])
            topic_ids = self._topic_ids_for_tape(tape_id)
            self.topic_costs = {
                topic_id: cost
                for topic_id, cost in self.topic_costs.items()
                if topic_id not in topic_ids
            }
            return "DELETE"
        if "DELETE FROM topic_anchors" in query:
            tape_id = cast(str, args[0])
            entry_count = cast(int, args[1])
            self.topic_anchors = [
                anchor
                for anchor in self.topic_anchors
                if not (
                    anchor["tape_id"] == tape_id
                    and cast(int, anchor["seq"]) >= entry_count
                )
            ]
            return "DELETE"
        if "DELETE FROM topics" in query:
            tape_id = cast(str, args[0])
            entry_count = cast(int, args[1])
            checkpoint_created_at = cast(datetime, args[2])
            self.topics = {
                topic_id: topic
                for topic_id, topic in self.topics.items()
                if not (
                    topic["tape_id"] == tape_id
                    and (
                        cast(int, topic["topic_initial_seq"]) >= entry_count
                        or cast(datetime, topic["created_at"]) > checkpoint_created_at
                    )
                )
            }
            return "DELETE"
        if "UPDATE topics" in query and "SET status = 'open'" in query:
            tape_id = cast(str, args[0])
            entry_count = cast(int, args[1])
            checkpoint_created_at = cast(datetime, args[2])
            for topic in self.topics.values():
                finalized_seq = cast(int | None, topic["topic_finalized_seq"])
                finalized_at = cast(datetime | None, topic["finalized_at"])
                if (
                    topic["tape_id"] == tape_id
                    and topic["status"] in {"finalized", "aborted"}
                    and (
                        (finalized_seq is not None and finalized_seq >= entry_count)
                        or (
                            finalized_at is not None
                            and finalized_at > checkpoint_created_at
                        )
                    )
                ):
                    topic["status"] = "open"
                    topic["summary"] = None
                    topic["topic_finalized_seq"] = None
                    topic["finalized_at"] = None
                    topic["metadata"] = {}
            return "UPDATE"
        return "OK"

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.calls.append(("fetchrow", _sql_label(query), args))
        if "session_fact_source" in query:
            session_id = cast(str, args[0])
            if "INSERT INTO session_fact_source" in query:
                if session_id in self.fact_source_rows:
                    return None
                row = {
                    "session_id": session_id,
                    "session_seq": args[1],
                    "retention_floor": args[2],
                    "projection": args[3],
                    "projection_epoch": args[4],
                }
                self.fact_source_rows[session_id] = row
                return row
            if "projection_epoch = projection_epoch + 1" in query:
                existing = self.fact_source_rows.get(session_id)
                if existing is None:
                    return None
                existing["projection_epoch"] = int(existing["projection_epoch"]) + 1
                return existing
            return self.fact_source_rows.get(session_id)
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
        if "FROM agent_checkpoints" in query:
            return self.checkpoint_meta_row
        if "session_id, tape_id" in query and "FROM topics" in query:
            topic = self.topics.get(cast(str, args[0]))
            if topic is None:
                return None
            return {
                "session_id": topic["session_id"],
                "tape_id": topic["tape_id"],
            }
        if "INSERT INTO topics" in query:
            existing = self.topics.get(cast(str, args[0]))
            if existing is not None:
                return existing
            row = {
                "topic_id": args[0],
                "tape_id": args[1],
                "session_id": args[2],
                "kind": args[3],
                "status": args[4],
                "title": args[5],
                "summary": args[6],
                "owner": args[7],
                "topic_initial_seq": args[8],
                "topic_finalized_seq": args[9],
                "created_at": args[10],
                "finalized_at": args[11],
                "metadata": args[12],
            }
            self.topics[cast(str, row["topic_id"])] = row
            return row
        if "UPDATE topics" in query and "status = 'finalized'" in query:
            return self._close_topic("finalized", args)
        if "UPDATE topics" in query and "status = 'aborted'" in query:
            return self._close_topic("aborted", args)
        if "INSERT INTO topic_anchors" in query:
            row = {
                "topic_id": args[0],
                "tape_id": args[1],
                "seq": args[2],
                "anchor_type": args[3],
                "entry_id": args[4],
                "metadata": args[5],
                "created_at": datetime.now(UTC),
            }
            self.topic_anchors.append(row)
            return row
        if "INSERT INTO topic_recall_links" in query:
            row = {
                "source_topic_id": args[0],
                "recalled_topic_id": args[1],
                "relation": args[2],
                "anchor_seq": args[3],
                "source_entry_start_seq": args[4],
                "source_entry_end_seq": args[5],
                "metadata": args[6],
                "created_at": datetime.now(UTC),
            }
            self.topic_recall_links.append(row)
            return row
        if "INSERT INTO topic_costs" in query:
            row = {
                "topic_id": args[0],
                "prompt_tokens": args[1],
                "completion_tokens": args[2],
                "total_tokens": args[3],
                "run_count": args[4],
                "action_count": args[5],
                "validation_count": args[6],
                "tool_call_count": args[7],
                "metadata": args[8],
                "updated_at": datetime.now(UTC),
            }
            self.topic_costs[cast(str, row["topic_id"])] = row
            return row
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

    def _topic_ids_for_tape(self, tape_id: str) -> set[str]:
        return {
            topic_id
            for topic_id, topic in self.topics.items()
            if topic["tape_id"] == tape_id
        }

    def _close_topic(
        self,
        status: str,
        args: tuple[object, ...],
    ) -> dict[str, object] | None:
        topic_id = cast(str, args[0])
        topic = self.topics.get(topic_id)
        if topic is None or topic["status"] != "open":
            return None
        topic_finalized_seq = cast(int | None, args[2])
        if topic_finalized_seq is not None and topic_finalized_seq < cast(
            int,
            topic["topic_initial_seq"],
        ):
            return None
        topic["status"] = status
        topic["summary"] = args[1]
        topic["topic_finalized_seq"] = topic_finalized_seq
        topic["finalized_at"] = args[3]
        topic["metadata"] = args[4]
        return topic


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
async def test_fenced_pg_runtime_claim_uses_durable_active_claim_path() -> None:
    pool = FakePGPool()
    authority = OwnerAuthority("session-a", "owner-a", 7)
    expected = AgentRunRecord(
        run_id="run-active",
        session_id="session-a",
        tape_id="tape-a",
        parent_run_id=None,
        agent_id=None,
        status="claimed",
        started_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )
    calls: list[tuple[Mapping[str, OwnerAuthority], str | None, str]] = []

    class DurableStore:
        async def claim_attached_executor_run(
            self,
            authorities,
            *,
            session_id,
            executor_kind,
            claim_metadata,
        ):
            del claim_metadata
            calls.append((authorities, session_id, executor_kind))
            return expected

    store = FencedPGRuntimeStore(
        durable_store=cast(Any, DurableStore()),
        pool=cast(Any, pool),
        authority_for_session=lambda _: authority,
        authorities=lambda: {authority.session_id: authority},
    )

    claimed = await store.claim_attached_executor_run(
        session_id=authority.session_id,
        executor_kind="local_cli",
        claim_metadata={"worker_id": "worker-1"},
    )

    assert claimed == expected
    assert calls == [
        ({authority.session_id: authority}, authority.session_id, "local_cli")
    ]


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
async def test_pg_durable_restore_reconciles_topic_projection_with_checkpoint() -> None:
    pool = FakePGPool()
    checkpoint_created_at = datetime(2026, 6, 26, 9, 0, 0, tzinfo=UTC)
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.session_tape_by_session["session-a"] = "tape-a"
    pool.connection.session_by_tape["tape-a"] = "session-a"
    pool.connection.checkpoint_meta_row = {"meta": {"session_id": "session-a"}}
    pool.connection.agent_runs = {
        "run-before": {
            "session_id": "session-a",
            "started_at": checkpoint_created_at - timedelta(seconds=1),
            "superseded_by_checkpoint_id": None,
            "superseded_at": None,
        },
        "run-at-boundary": {
            "session_id": "session-a",
            "started_at": checkpoint_created_at,
            "superseded_by_checkpoint_id": None,
            "superseded_at": None,
        },
        "run-after": {
            "session_id": "session-a",
            "started_at": checkpoint_created_at + timedelta(seconds=1),
            "superseded_by_checkpoint_id": None,
            "superseded_at": None,
        },
        "run-already-superseded": {
            "session_id": "session-a",
            "started_at": checkpoint_created_at + timedelta(seconds=2),
            "superseded_by_checkpoint_id": "checkpoint-original",
            "superseded_at": checkpoint_created_at + timedelta(seconds=3),
        },
        "run-other-session": {
            "session_id": "session-b",
            "started_at": checkpoint_created_at + timedelta(seconds=1),
            "superseded_by_checkpoint_id": None,
            "superseded_at": None,
        },
    }
    pool.connection.topics = {
        "topic-keep": {
            "topic_id": "topic-keep",
            "tape_id": "tape-a",
            "status": "finalized",
            "summary": "closed before checkpoint",
            "topic_initial_seq": 0,
            "topic_finalized_seq": 0,
            "created_at": checkpoint_created_at - timedelta(seconds=5),
            "finalized_at": checkpoint_created_at - timedelta(seconds=2),
            "metadata": {},
        },
        "topic-reopen": {
            "topic_id": "topic-reopen",
            "tape_id": "tape-a",
            "status": "finalized",
            "summary": "closed after checkpoint",
            "topic_initial_seq": 0,
            "topic_finalized_seq": 1,
            "created_at": checkpoint_created_at - timedelta(seconds=4),
            "finalized_at": checkpoint_created_at + timedelta(seconds=1),
            "metadata": {"after": True},
        },
        "topic-delete": {
            "topic_id": "topic-delete",
            "tape_id": "tape-a",
            "status": "open",
            "summary": None,
            "topic_initial_seq": 1,
            "topic_finalized_seq": None,
            "created_at": checkpoint_created_at + timedelta(seconds=1),
            "finalized_at": None,
            "metadata": {},
        },
        "topic-other": {
            "topic_id": "topic-other",
            "tape_id": "tape-other",
            "status": "open",
            "summary": None,
            "topic_initial_seq": 9,
            "topic_finalized_seq": None,
            "created_at": checkpoint_created_at + timedelta(seconds=1),
            "finalized_at": None,
            "metadata": {},
        },
    }
    pool.connection.topic_anchors = [
        {
            "topic_id": "topic-keep",
            "tape_id": "tape-a",
            "seq": 0,
            "anchor_type": "start",
        },
        {
            "topic_id": "topic-reopen",
            "tape_id": "tape-a",
            "seq": 1,
            "anchor_type": "finalize",
        },
        {
            "topic_id": "topic-other",
            "tape_id": "tape-other",
            "seq": 9,
            "anchor_type": "start",
        },
    ]
    pool.connection.topic_recall_links = [
        {
            "source_topic_id": "topic-keep",
            "recalled_topic_id": "topic-reopen",
        },
        {
            "source_topic_id": "topic-other",
            "recalled_topic_id": "topic-keep",
        },
        {
            "source_topic_id": "topic-other",
            "recalled_topic_id": "topic-other",
        },
    ]
    pool.connection.topic_costs = {
        "topic-keep": {"topic_id": "topic-keep", "total_tokens": 10},
        "topic-reopen": {"topic_id": "topic-reopen", "total_tokens": 20},
        "topic-other": {"topic_id": "topic-other", "total_tokens": 30},
    }
    store = PGDurableStore(pool=cast(Any, pool))

    await store.restore_checkpoint_state(
        OwnerAuthority("session-a", "owner-a", 7),
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="checkpoint-keep",
                tape_id="tape-a",
                session_id="session-a",
                entry_count=1,
                window_start=0,
                created_at=checkpoint_created_at,
                label="keep",
            ),
            tape_entries=({"kind": "message", "payload": {"text": "keep"}},),
            plugin_states={},
        ),
        {
            "id": "session-a",
            "session_id": "session-a",
            "tape_id": "tape-a",
            "provider_name": "restored-provider",
        },
    )

    assert set(pool.connection.topics) == {
        "topic-keep",
        "topic-reopen",
        "topic-other",
    }
    assert pool.connection.topics["topic-reopen"]["status"] == "open"
    assert pool.connection.topics["topic-reopen"]["summary"] is None
    assert pool.connection.topics["topic-reopen"]["topic_finalized_seq"] is None
    assert pool.connection.topics["topic-reopen"]["finalized_at"] is None
    assert pool.connection.topics["topic-reopen"]["metadata"] == {}
    assert pool.connection.topic_anchors == [
        {
            "topic_id": "topic-keep",
            "tape_id": "tape-a",
            "seq": 0,
            "anchor_type": "start",
        },
        {
            "topic_id": "topic-other",
            "tape_id": "tape-other",
            "seq": 9,
            "anchor_type": "start",
        },
    ]
    assert pool.connection.topic_recall_links == [
        {
            "source_topic_id": "topic-other",
            "recalled_topic_id": "topic-other",
        }
    ]
    assert set(pool.connection.topic_costs) == {"topic-other"}
    assert pool.connection.agent_runs["run-before"]["superseded_at"] is None
    assert pool.connection.agent_runs["run-at-boundary"]["superseded_at"] is None
    assert (
        pool.connection.agent_runs["run-after"]["superseded_by_checkpoint_id"]
        == "checkpoint-keep"
    )
    assert pool.connection.agent_runs["run-after"]["superseded_at"] is not None
    assert (
        pool.connection.agent_runs["run-already-superseded"][
            "superseded_by_checkpoint_id"
        ]
        == "checkpoint-original"
    )
    assert pool.connection.agent_runs["run-other-session"]["superseded_at"] is None


@pytest.mark.asyncio
async def test_pg_durable_create_topic_locks_owner_and_tape_before_insert() -> None:
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.session_tape_by_session["session-a"] = "tape-a"
    pool.connection.session_by_tape["tape-a"] = "session-a"
    store = PGDurableStore(pool=cast(Any, pool))

    topic = await store.create_topic(
        OwnerAuthority("session-a", "owner-a", 7),
        TopicRecord(
            topic_id="topic-a",
            tape_id="tape-a",
            session_id="session-a",
            kind="interaction",
            status="open",
            title="Topic A",
            summary=None,
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=None,
            created_at=datetime.now(UTC),
        ),
    )

    assert topic.topic_id == "topic-a"
    labels = [call[1] for call in pool.connection.calls]
    assert labels[:4] == [
        "BEGIN",
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners WHERE session_id = $1 FOR UPDATE",
        "SELECT session_id FROM session_tapes WHERE tape_id = $1 FOR UPDATE",
        "WITH inserted AS ( INSERT INTO topics ( topic_id, tape_id, session_id, kind, status, title, summary, owner, topic_initial_seq, topic_finalized_seq, created_at, finalized_at, metadata ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb) ON CONFLICT (topic_id) DO NOTHING RETURNING * ) SELECT * FROM inserted UNION ALL SELECT * FROM topics WHERE topic_id = $1 AND NOT EXISTS (SELECT 1 FROM inserted)",
    ]
    assert labels[-1] == "COMMIT"


@pytest.mark.asyncio
async def test_pg_durable_topic_mutators_lock_owner_tape_and_topic_before_write() -> (
    None
):
    pool = FakePGPool()
    pool.connection.owner_row = {
        "owner_id": "owner-a",
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=30),
        "fencing_token": 7,
    }
    pool.connection.session_tape_by_session["session-a"] = "tape-a"
    pool.connection.session_by_tape["tape-a"] = "session-a"
    pool.connection.topics["topic-a"] = {
        "topic_id": "topic-a",
        "tape_id": "tape-a",
        "session_id": "session-a",
        "kind": "interaction",
        "status": "open",
        "title": "Topic A",
        "summary": None,
        "owner": None,
        "topic_initial_seq": 0,
        "topic_finalized_seq": None,
        "created_at": datetime.now(UTC),
        "finalized_at": None,
        "metadata": {},
    }
    store = PGDurableStore(pool=cast(Any, pool))
    authority = OwnerAuthority("session-a", "owner-a", 7)

    pool.connection.calls.clear()
    await store.finalize_topic(
        authority,
        "topic-a",
        summary="done",
        topic_finalized_seq=0,
        finalized_at=datetime.now(UTC),
        metadata={},
    )
    _assert_topic_mutator_locks_before_write(
        [call[1] for call in pool.connection.calls],
        write_sql="UPDATE topics SET status = 'finalized'",
    )

    pool.connection.calls.clear()
    await store.record_topic_anchor(
        authority,
        TopicAnchorRecord(
            topic_id="topic-a",
            tape_id="tape-a",
            seq=0,
            anchor_type="finalize",
            entry_id=None,
        ),
    )
    _assert_topic_mutator_locks_before_write(
        [call[1] for call in pool.connection.calls],
        write_sql="INSERT INTO topic_anchors",
    )

    pool.connection.calls.clear()
    await store.record_recall_link(
        authority,
        TopicRecallLinkRecord(
            source_topic_id="topic-a",
            recalled_topic_id="topic-a",
            relation="self",
        ),
    )
    _assert_topic_mutator_locks_before_write(
        [call[1] for call in pool.connection.calls],
        write_sql="INSERT INTO topic_recall_links",
    )

    pool.connection.calls.clear()
    await store.update_topic_cost(
        authority,
        TopicCostRecord(topic_id="topic-a", total_tokens=10, run_count=1),
    )
    _assert_topic_mutator_locks_before_write(
        [call[1] for call in pool.connection.calls],
        write_sql="INSERT INTO topic_costs",
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
    assert type(manager.selected_topic_store()).__name__ == "FencedPGTopicStore"
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


def _assert_topic_mutator_locks_before_write(
    labels: list[str],
    *,
    write_sql: str,
) -> None:
    owner_idx = labels.index(
        "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners WHERE session_id = $1 FOR UPDATE"
    )
    tape_idx = labels.index(
        "SELECT session_id FROM session_tapes WHERE tape_id = $1 FOR UPDATE"
    )
    topic_idx = labels.index(
        "SELECT session_id, tape_id FROM topics WHERE topic_id = $1 FOR UPDATE"
    )
    write_idx = next(idx for idx, label in enumerate(labels) if write_sql in label)
    assert owner_idx < tape_idx < topic_idx < write_idx
