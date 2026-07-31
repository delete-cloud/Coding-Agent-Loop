from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RuntimeEventRecord,
    RunMessageSnapshotRecord,
    SQLiteRuntimeStore,
)


@pytest.mark.asyncio
async def test_sqlite_runtime_store_persists_runtime_records_across_instances(
    tmp_path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    first = SQLiteRuntimeStore(path)
    started_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    await first.create_agent_run(
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id=None,
            agent_id=None,
            status="requested",
            started_at=started_at,
            metadata={
                "executor_ref_kind": "local_attached",
                "executor_kind": "local_cli",
            },
            result={},
            error=None,
        )
    )
    claimed = await first.claim_attached_executor_run(
        session_id="session-1",
        executor_kind="local_cli",
        claim_metadata={"executor_id": "executor-1"},
    )
    assert claimed is not None
    assert claimed.status == "claimed"
    assert claimed.metadata["executor_id"] == "executor-1"

    await first.update_agent_run(
        "run-1",
        status="completed",
        ended_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        metadata={
            "executor_ref_kind": "local_attached",
            "executor_kind": "local_cli",
            "executor_id": "executor-1",
            "resume_from_run_id": "run-0",
        },
        result={"stop_reason": "done"},
        error=None,
    )
    event = await first.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-1",
            run_id="run-1",
            event_kind="wire.TurnEnd",
            payload={"run_id": "run-1", "resume_from_run_id": "run-0"},
            created_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        )
    )
    await first.save_message_snapshot(
        RunMessageSnapshotRecord(
            snapshot_id="snapshot-1",
            run_id="run-1",
            messages=[{"role": "user", "content": "resume"}],
            metadata={"source": "test"},
            created_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        )
    )
    await first.create_agent_interaction(
        AgentInteractionRecord(
            interaction_id="interaction-1",
            run_id="run-1",
            interaction_kind="approval",
            status="pending",
            request_payload={"tool": "shell"},
            response_payload={},
            metadata={},
            created_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        )
    )
    await first.resolve_agent_interaction(
        "interaction-1",
        status="approved",
        response_payload={"approved": True},
        resolved_at=datetime(2026, 6, 1, 12, 2, tzinfo=UTC),
    )

    second = SQLiteRuntimeStore(path)
    loaded = await second.load_agent_run("run-1")
    events = await second.replay_runtime_events("run-1")
    snapshots = await second.list_message_snapshots("run-1")
    interactions = await second.list_agent_interactions("run-1")

    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.metadata["resume_from_run_id"] == "run-0"
    assert event.sequence == 1
    assert [item.event_id for item in events] == ["event-1"]
    assert events[0].payload["resume_from_run_id"] == "run-0"
    assert [item.snapshot_id for item in snapshots] == ["snapshot-1"]
    assert snapshots[0].messages == [{"role": "user", "content": "resume"}]
    assert [item.interaction_id for item in interactions] == ["interaction-1"]
    assert interactions[0].status == "approved"
    assert interactions[0].response_payload == {"approved": True}


@pytest.mark.asyncio
async def test_sqlite_runtime_store_normalizes_blank_error_to_none(tmp_path) -> None:
    """Defense-in-depth: a blank error must persist as NULL, never raise."""
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    await store.create_agent_run(
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id=None,
            parent_run_id=None,
            agent_id=None,
            status="running",
            started_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            metadata={},
            result={},
            error=None,
        )
    )

    updated = await store.update_agent_run(
        "run-1",
        status="failed",
        ended_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        metadata={},
        result={},
        error="",
    )
    assert updated.error is None

    loaded = await store.load_agent_run("run-1")
    assert loaded is not None
    assert loaded.error is None
