from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    JSONLRuntimeStore,
    RuntimeEventRecord,
)


@pytest.mark.asyncio
async def test_jsonl_runtime_store_persists_runs_and_events_across_instances(
    tmp_path,
) -> None:
    root = tmp_path / "runtime"
    first = JSONLRuntimeStore(root)
    started_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    superseded_at = datetime(2026, 6, 1, 12, 2, tzinfo=UTC)

    await first.create_agent_run(
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id=None,
            agent_id=None,
            status="queued",
            started_at=started_at,
            metadata={"execution_placement": "server_embedded"},
            result={},
            error=None,
            superseded_by_checkpoint_id="checkpoint-1",
            superseded_at=superseded_at,
        )
    )
    await first.update_agent_run(
        "run-1",
        status="completed",
        ended_at=datetime(2026, 6, 1, 12, 1, tzinfo=UTC),
        metadata={
            "execution_placement": "server_embedded",
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

    second = JSONLRuntimeStore(root)
    loaded = await second.load_agent_run("run-1")
    events = await second.replay_runtime_events("run-1")

    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.metadata["resume_from_run_id"] == "run-0"
    assert loaded.superseded_by_checkpoint_id == "checkpoint-1"
    assert loaded.superseded_at == superseded_at
    assert event.sequence == 1
    assert [item.event_id for item in events] == ["event-1"]
    assert events[0].payload["resume_from_run_id"] == "run-0"


@pytest.mark.asyncio
async def test_jsonl_runtime_store_normalizes_blank_error_to_none(tmp_path) -> None:
    """Defense-in-depth: a blank error must persist as absent, never raise."""
    store = JSONLRuntimeStore(tmp_path / "runtime")
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
        error="   ",
    )
    assert updated.error is None

    loaded = await store.load_agent_run("run-1")
    assert loaded is not None
    assert loaded.error is None
