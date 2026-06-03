from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agentkit.checkpoint.models import CheckpointMeta

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.runs import RuntimeQueryService


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"


class RecordingRuntimeQueryStore:
    def __init__(self) -> None:
        self.runs: list[AgentRunRecord] = []
        self.events: list[RuntimeEventRecord] = []
        self.interactions: dict[str, AgentInteractionRecord] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return next((run for run in self.runs if run.run_id == run_id), None)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs if run.session_id == session_id]

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions.values()
            if interaction.run_id == run_id
        ]

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        return self.interactions.get(interaction_id)

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        return next((event for event in self.events if event.event_id == event_id), None)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = [
            event
            for event in self.events
            if event.run_id == run_id
            and event.sequence is not None
            and event.sequence > after_sequence
        ]
        return events[:limit]


@pytest.mark.asyncio
async def test_runtime_query_service_reports_resume_metadata() -> None:
    store = RecordingRuntimeQueryStore()
    store.runs.extend(
        [
            _run(
                "run-old",
                status="completed",
                started_at=datetime(2026, 5, 19, 11, 0, tzinfo=UTC),
            ),
            _run(
                "run-interrupted",
                status="interrupted",
                started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            ),
        ]
    )
    store.events.extend(
        [
            _event("event-1", run_id="run-interrupted", sequence=1),
            _event("event-last", run_id="run-interrupted", sequence=2),
        ]
    )

    metadata = await RuntimeQueryService(store).session_resume_metadata(
        FakeSession(),
        list_checkpoints=_list_checkpoints,
    )

    assert metadata == {
        "resumable": True,
        "last_run_id": "run-interrupted",
        "last_run_status": "interrupted",
        "last_interrupted_run_id": "run-interrupted",
        "resume_from_event_id": "event-last",
        "checkpoint_count": 2,
        "latest_checkpoint_id": "cp-latest",
        "latest_checkpoint_label": "latest",
    }


@pytest.mark.asyncio
async def test_runtime_query_service_marks_active_run_not_resumable() -> None:
    store = RecordingRuntimeQueryStore()
    store.runs.append(
        _run(
            "run-running",
            status="running",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
        )
    )

    metadata = await RuntimeQueryService(store).session_resume_metadata(
        FakeSession(tape_id=None),
        list_checkpoints=_list_checkpoints,
    )

    assert metadata["resumable"] is False
    assert metadata["last_run_id"] == "run-running"
    assert metadata["checkpoint_count"] == 0


@pytest.mark.asyncio
async def test_runtime_query_service_keeps_checkpoint_metadata_without_store() -> None:
    metadata = await RuntimeQueryService(None).session_resume_metadata(
        FakeSession(),
        list_checkpoints=_list_checkpoints,
    )

    assert metadata == {
        "resumable": False,
        "last_run_id": None,
        "last_run_status": None,
        "last_interrupted_run_id": None,
        "resume_from_event_id": None,
        "checkpoint_count": 2,
        "latest_checkpoint_id": "cp-latest",
        "latest_checkpoint_label": "latest",
    }


@pytest.mark.asyncio
async def test_runtime_query_service_load_methods_raise_for_missing_records() -> None:
    service = RuntimeQueryService(RecordingRuntimeQueryStore())

    with pytest.raises(KeyError, match="runtime run not found: missing-run"):
        await service.load_runtime_run("missing-run")
    with pytest.raises(KeyError, match="runtime interaction not found: missing-int"):
        await service.load_runtime_interaction("missing-int")
    with pytest.raises(KeyError, match="runtime message snapshot not found"):
        await service.load_runtime_message_snapshot("missing-run")


async def _list_checkpoints(session_id: str) -> list[CheckpointMeta]:
    assert session_id == "session-1"
    return [
        CheckpointMeta(
            checkpoint_id="cp-old",
            tape_id="tape-1",
            session_id=session_id,
            entry_count=1,
            window_start=0,
            created_at=datetime(2026, 5, 19, 11, 0, tzinfo=UTC),
            label="old",
        ),
        CheckpointMeta(
            checkpoint_id="cp-latest",
            tape_id="tape-1",
            session_id=session_id,
            entry_count=2,
            window_start=0,
            created_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            label="latest",
        ),
    ]


def _run(
    run_id: str,
    *,
    status: str,
    started_at: datetime,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        session_id="session-1",
        tape_id="tape-1",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=started_at,
        metadata={},
        result={},
    )


def _event(
    event_id: str,
    *,
    run_id: str,
    sequence: int,
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id=event_id,
        run_id=run_id,
        event_kind="wire.StreamDelta",
        payload={"message_type": "StreamDelta"},
        created_at=datetime(2026, 5, 19, 12, sequence, tzinfo=UTC),
        sequence=sequence,
    )
