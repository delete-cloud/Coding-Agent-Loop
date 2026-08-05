from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    PGRuntimeStore,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)


class FakePool:
    def __init__(self) -> None:
        self.agent_runs: dict[str, dict[str, object]] = {}
        self.runtime_events: dict[str, dict[str, object]] = {}
        self.run_message_snapshots: dict[str, dict[str, object]] = {}
        self.agent_interactions: dict[str, dict[str, object]] = {}
        self.next_event_sequence = 1
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS agent_runs" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO agent_runs" in query:
            (
                run_id,
                session_id,
                tape_id,
                parent_run_id,
                agent_id,
                status,
                started_at,
                ended_at,
                metadata,
                result,
                error,
                superseded_by_checkpoint_id,
                superseded_at,
            ) = args
            row = {
                "run_id": run_id,
                "session_id": session_id,
                "tape_id": tape_id,
                "parent_run_id": parent_run_id,
                "agent_id": agent_id,
                "status": status,
                "started_at": started_at,
                "ended_at": ended_at,
                "metadata": metadata,
                "result": result,
                "error": error,
                "superseded_by_checkpoint_id": superseded_by_checkpoint_id,
                "superseded_at": superseded_at,
            }
            self.agent_runs[cast(str, run_id)] = row
            return row
        if "FOR UPDATE SKIP LOCKED" in query:
            session_id, executor_kind, claim_metadata = args
            for row in self.agent_runs.values():
                if session_id is not None and row["session_id"] != session_id:
                    continue
                if row["status"] not in {"requested", "expired"}:
                    continue
                metadata = cast(dict[str, object], row["metadata"])
                if metadata.get("executor_ref_kind") not in {
                    "external_worker",
                    "local_attached",
                }:
                    continue
                if metadata.get("executor_kind") != executor_kind:
                    continue
                row["status"] = "claimed"
                row["metadata"] = {
                    **metadata,
                    **cast(dict[str, object], claim_metadata),
                }
                return row
            return None
        if "UPDATE agent_runs" in query:
            run_id, status, ended_at, metadata, result, error = args
            row = self.agent_runs.get(cast(str, run_id))
            if row is None:
                return None
            row.update(
                {
                    "status": status,
                    "ended_at": ended_at,
                    "metadata": metadata,
                    "result": result,
                    "error": error,
                }
            )
            return row
        if "SELECT * FROM agent_runs WHERE run_id = $1" in query:
            return self.agent_runs.get(cast(str, args[0]))
        if "INSERT INTO runtime_events" in query:
            event_id, run_id, event_kind, payload, created_at = args
            existing = self.runtime_events.get(cast(str, event_id))
            if existing is not None:
                return existing
            row = {
                "sequence": self.next_event_sequence,
                "event_id": event_id,
                "run_id": run_id,
                "event_kind": event_kind,
                "payload": payload,
                "created_at": created_at,
            }
            self.next_event_sequence += 1
            self.runtime_events[cast(str, event_id)] = row
            return row
        if "INSERT INTO run_message_snapshots" in query:
            snapshot_id, run_id, messages, metadata, created_at = args
            row = {
                "snapshot_id": snapshot_id,
                "run_id": run_id,
                "messages": messages,
                "metadata": metadata,
                "created_at": created_at,
            }
            self.run_message_snapshots[cast(str, snapshot_id)] = row
            return row
        if "SELECT * FROM run_message_snapshots WHERE snapshot_id = $1" in query:
            return self.run_message_snapshots.get(cast(str, args[0]))
        if "SELECT * FROM runtime_events WHERE event_id = $1" in query:
            return self.runtime_events.get(cast(str, args[0]))
        if "INSERT INTO agent_interactions" in query:
            (
                interaction_id,
                run_id,
                interaction_kind,
                status,
                request_payload,
                response_payload,
                metadata,
                created_at,
                resolved_at,
            ) = args
            row = {
                "interaction_id": interaction_id,
                "run_id": run_id,
                "interaction_kind": interaction_kind,
                "status": status,
                "request_payload": request_payload,
                "response_payload": response_payload,
                "metadata": metadata,
                "created_at": created_at,
                "resolved_at": resolved_at,
            }
            self.agent_interactions[cast(str, interaction_id)] = row
            return row
        if "WITH resolved AS" in query:
            interaction_id, status, response_payload, resolved_at = args
            row = self.agent_interactions.get(cast(str, interaction_id))
            if row is None:
                return None
            if row["resolved_at"] is None:
                row.update(
                    {
                        "status": status,
                        "response_payload": response_payload,
                        "resolved_at": resolved_at,
                    }
                )
            return row
        if "SELECT * FROM agent_interactions WHERE interaction_id = $1" in query:
            return self.agent_interactions.get(cast(str, args[0]))
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM agent_runs WHERE session_id = $1" in query:
            session_id = cast(str, args[0])
            return [
                row
                for row in sorted(
                    self.agent_runs.values(),
                    key=lambda item: cast(str, item["run_id"]),
                )
                if row["session_id"] == session_id
            ]
        if "SELECT * FROM runtime_events" in query:
            run_id, after_sequence, limit = args
            rows = [
                row
                for row in sorted(
                    self.runtime_events.values(),
                    key=lambda item: cast(int, item["sequence"]),
                )
                if row["run_id"] == run_id
                and cast(int, row["sequence"]) > cast(int, after_sequence)
            ]
            return rows[: cast(int, limit)]
        if "SELECT * FROM run_message_snapshots WHERE run_id = $1" in query:
            run_id = cast(str, args[0])
            return [
                row
                for row in sorted(
                    self.run_message_snapshots.values(),
                    key=lambda item: cast(str, item["snapshot_id"]),
                )
                if row["run_id"] == run_id
            ]
        if "SELECT * FROM agent_interactions WHERE run_id = $1" in query:
            run_id = cast(str, args[0])
            return [
                row
                for row in sorted(
                    self.agent_interactions.values(),
                    key=lambda item: cast(str, item["interaction_id"]),
                )
                if row["run_id"] == run_id
            ]
        raise AssertionError(f"unexpected fetch query: {query}")

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakePool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")


@pytest.fixture
def fake_pool() -> FakePool:
    return FakePool()


@pytest.fixture
def store(fake_pool: FakePool) -> PGRuntimeStore:
    async def fake_pool_factory(**_: object) -> AsyncPGPool:
        return cast(AsyncPGPool, fake_pool)

    return PGRuntimeStore(
        pool=PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
    )


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 19, hour, minute, tzinfo=UTC)


@pytest.mark.asyncio
async def test_create_update_load_and_list_agent_runs(
    store: PGRuntimeStore,
) -> None:
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id=None,
            agent_id="main",
            status="running",
            started_at=_dt(9),
            ended_at=None,
            metadata={"model": "gpt-5"},
            result={},
            error=None,
        )
    )
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-2",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id="run-1",
            agent_id="subagent-1",
            status="running",
            started_at=_dt(9, 5),
            ended_at=None,
            metadata={"role": "explorer"},
            result={},
            error=None,
        )
    )

    updated = await store.update_agent_run(
        "run-1",
        status="completed",
        ended_at=_dt(9, 30),
        metadata={"model": "gpt-5", "steps": 4},
        result={"final_message": "done"},
        error=None,
    )
    loaded = await store.load_agent_run("run-1")
    listed = await store.list_agent_runs("session-1")

    assert loaded == updated
    assert updated.status == "completed"
    assert updated.tape_id == "tape-1"
    assert updated.ended_at == _dt(9, 30)
    assert updated.metadata == {"model": "gpt-5", "steps": 4}
    assert updated.result == {"final_message": "done"}
    assert [run.run_id for run in listed] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_load_agent_run_round_trips_supersession_fields(
    store: PGRuntimeStore,
) -> None:
    superseded_at = _dt(10)
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-superseded",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=_dt(9),
            ended_at=_dt(9, 30),
            superseded_by_checkpoint_id="checkpoint-1",
            superseded_at=superseded_at,
        )
    )

    loaded = await store.load_agent_run("run-superseded")

    assert loaded is not None
    assert loaded.superseded_by_checkpoint_id == "checkpoint-1"
    assert loaded.superseded_at == superseded_at


@pytest.mark.asyncio
async def test_claim_external_worker_run_marks_requested_run_claimed(
    store: PGRuntimeStore,
) -> None:
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-worker",
            session_id="session-worker",
            tape_id=None,
            parent_run_id=None,
            agent_id=None,
            status="requested",
            started_at=_dt(9),
            ended_at=None,
            metadata={
                "executor_ref_kind": "external_worker",
                "executor_kind": "local_cli",
                "prompt": "hello",
            },
            result={},
            error=None,
        )
    )

    claimed = await store.claim_external_worker_run(
        session_id="session-worker",
        executor_kind="local_cli",
        claim_metadata={
            "worker_id": "worker-1",
            "claim_token_hash": "hash",
        },
    )

    assert claimed is not None
    assert claimed.run_id == "run-worker"
    assert claimed.status == "claimed"
    assert claimed.metadata["worker_id"] == "worker-1"
    assert claimed.metadata["prompt"] == "hello"


@pytest.mark.asyncio
async def test_claim_attached_executor_run_accepts_local_attached_binding(
    store: PGRuntimeStore,
) -> None:
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-local-attached",
            session_id="session-local-attached",
            tape_id=None,
            parent_run_id=None,
            agent_id=None,
            status="requested",
            started_at=_dt(9),
            ended_at=None,
            metadata={
                "executor_ref_kind": "local_attached",
                "execution_placement": "local_attached",
                "executor_kind": "local_cli",
                "prompt": "hello",
            },
            result={},
            error=None,
        )
    )

    claimed = await store.claim_attached_executor_run(
        session_id="session-local-attached",
        executor_kind="local_cli",
        claim_metadata={
            "worker_id": "worker-1",
            "claim_token_hash": "hash",
        },
    )

    assert claimed is not None
    assert claimed.run_id == "run-local-attached"
    assert claimed.status == "claimed"
    assert claimed.metadata["executor_ref_kind"] == "local_attached"
    assert claimed.metadata["worker_id"] == "worker-1"


@pytest.mark.asyncio
async def test_append_runtime_event_replays_in_sequence_order(
    store: PGRuntimeStore,
) -> None:
    first = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-1",
            run_id="run-1",
            event_kind="model.start",
            payload={"step": 1},
            created_at=_dt(10),
        )
    )
    second = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-2",
            run_id="run-1",
            event_kind="model.done",
            payload={"step": 2},
            created_at=_dt(10, 1),
        )
    )

    replayed = await store.replay_runtime_events("run-1")
    after_first = await store.replay_runtime_events(
        "run-1",
        after_sequence=cast(int, first.sequence),
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.event_id for event in replayed] == ["evt-1", "evt-2"]
    assert [event.event_id for event in after_first] == ["evt-2"]


@pytest.mark.asyncio
async def test_append_runtime_event_returns_existing_record_for_duplicate_event_id(
    store: PGRuntimeStore,
) -> None:
    first = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-dup",
            run_id="run-1",
            event_kind="tool.start",
            payload={"attempt": 1},
            created_at=_dt(11),
        )
    )
    duplicate = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-dup",
            run_id="run-1",
            event_kind="tool.start",
            payload={"attempt": 2},
            created_at=_dt(11, 1),
        )
    )
    replayed = await store.replay_runtime_events("run-1")

    assert duplicate == first
    assert [event.payload for event in replayed] == [{"attempt": 1}]


@pytest.mark.asyncio
async def test_load_runtime_event_returns_record_by_event_id(
    store: PGRuntimeStore,
) -> None:
    event = await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-load",
            run_id="run-1",
            event_kind="wire.StreamDelta",
            payload={"delta": "hello"},
            created_at=_dt(11, 2),
        )
    )

    loaded = await store.load_runtime_event("evt-load")
    missing = await store.load_runtime_event("missing-event")

    assert loaded == event
    assert missing is None


@pytest.mark.asyncio
async def test_save_load_and_list_run_message_snapshots(
    store: PGRuntimeStore,
) -> None:
    first = await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="snap-1",
            run_id="run-1",
            messages=[{"role": "user", "content": "hi"}],
            metadata={"window": 1},
            created_at=_dt(12),
        )
    )
    second = await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="snap-2",
            run_id="run-1",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            metadata={"window": 2},
            created_at=_dt(12, 1),
        )
    )

    loaded = await store.load_message_snapshot("snap-1")
    listed = await store.list_message_snapshots("run-1")

    assert loaded == first
    assert [snapshot.snapshot_id for snapshot in listed] == ["snap-1", "snap-2"]
    assert listed[1] == second


@pytest.mark.asyncio
async def test_create_and_resolve_agent_interaction_idempotently(
    store: PGRuntimeStore,
) -> None:
    await store.create_agent_interaction(
        AgentInteractionRecord(
            interaction_id="interaction-1",
            run_id="run-1",
            interaction_kind="approval",
            status="pending",
            request_payload={"tool": "shell", "command": "pytest"},
            response_payload={},
            metadata={"request_id": "req-1"},
            created_at=_dt(13),
            resolved_at=None,
        )
    )

    resolved = await store.resolve_agent_interaction(
        "interaction-1",
        status="approved",
        response_payload={"approved": True},
        resolved_at=_dt(13, 5),
    )
    repeated = await store.resolve_agent_interaction(
        "interaction-1",
        status="rejected",
        response_payload={"approved": False},
        resolved_at=_dt(13, 10),
    )
    loaded = await store.load_agent_interaction("interaction-1")
    listed = await store.list_agent_interactions("run-1")

    assert repeated == resolved
    assert loaded == resolved
    assert resolved.status == "approved"
    assert resolved.response_payload == {"approved": True}
    assert resolved.resolved_at == _dt(13, 5)
    assert [interaction.interaction_id for interaction in listed] == ["interaction-1"]


@pytest.mark.asyncio
async def test_pg_runtime_store_schema_initialization_is_idempotent(
    store: PGRuntimeStore,
    fake_pool: FakePool,
) -> None:
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id=None,
            agent_id="main",
            status="running",
            started_at=_dt(14),
            ended_at=None,
            metadata={},
            result={},
            error=None,
        )
    )
    await store.append_runtime_event(
        RuntimeEventRecord(
            event_id="evt-1",
            run_id="run-1",
            event_kind="notice",
            payload={},
            created_at=_dt(14, 1),
        )
    )
    await store.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="snap-1",
            run_id="run-1",
            messages=[],
            metadata={},
            created_at=_dt(14, 2),
        )
    )
    await store.create_agent_interaction(
        AgentInteractionRecord(
            interaction_id="interaction-1",
            run_id="run-1",
            interaction_kind="approval",
            status="pending",
            request_payload={},
            response_payload={},
            metadata={},
            created_at=_dt(14, 3),
            resolved_at=None,
        )
    )

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS agent_runs" in query
    ]

    assert len(schema_calls) == 1
    assert "CREATE TABLE IF NOT EXISTS runtime_events" in schema_calls[0]
    assert "CREATE TABLE IF NOT EXISTS run_message_snapshots" in schema_calls[0]
    assert "CREATE TABLE IF NOT EXISTS agent_interactions" in schema_calls[0]
    assert "ADD COLUMN IF NOT EXISTS superseded_by_checkpoint_id" in schema_calls[0]
    assert "ADD COLUMN IF NOT EXISTS superseded_at" in schema_calls[0]
