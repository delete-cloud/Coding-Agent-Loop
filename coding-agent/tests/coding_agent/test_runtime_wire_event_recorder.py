from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from coding_agent.runs import (
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    RuntimeWireEventRecorder,
)
from coding_agent.runtime_store import AgentRunRecord, RuntimeEventRecord
from coding_agent.wire.protocol import StreamDelta


def _local_target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/workspace"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _external_worker_target() -> RunTarget:
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(),
        executor=ExternalWorkerExecutorRef(executor_kind="local_cli"),
        isolation=IsolationPolicy(kind="external_worker_policy"),
    )


@dataclass
class FakeSession:
    id: str = "session-1"
    current_turn_id: str | None = "run-1"
    tape_id: str | None = "tape-1"
    default_run_target: RunTarget = field(default_factory=_local_target)


class RecordingRuntimeEventStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: list[RuntimeEventRecord] = []

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        self.events.append(record)
        return record


@pytest.mark.asyncio
async def test_runtime_wire_event_recorder_uses_run_correlation_metadata() -> None:
    store = RecordingRuntimeEventStore()
    store.runs["run-1"] = AgentRunRecord(
        run_id="run-1",
        session_id="session-1",
        tape_id="stable-tape",
        parent_run_id="run-0",
        agent_id=None,
        status="running",
        started_at=datetime(2026, 6, 3, 1, 2, tzinfo=UTC),
        metadata={
            "execution_placement": "local_attached",
            "executor_ref_kind": "external_worker",
            "workspace_surface": "external_worker_workspace_ref",
            "execution_plane": "executor_plane",
            "previous_run_id": "run-0",
            "resume_from_run_id": "run-0",
            "resume_context_injected": True,
            "executor_id": "executor-1",
        },
        result={},
    )
    emitted_at = datetime(2026, 6, 3, 1, 3, tzinfo=UTC)
    recorder = RuntimeWireEventRecorder(
        store,
        new_event_id=lambda run_id: f"{run_id}:wire:fixed",
    )

    record = await recorder.append_wire_event(
        FakeSession(),
        StreamDelta(
            session_id="session-1",
            agent_id="root",
            timestamp=emitted_at,
            content="hello",
            role="assistant",
        ),
    )

    assert record == store.events[0]
    assert record.event_id == "run-1:wire:fixed"
    assert record.run_id == "run-1"
    assert record.event_kind == "wire.StreamDelta"
    assert record.created_at == emitted_at
    assert record.payload["session_id"] == "session-1"
    assert record.payload["run_id"] == "run-1"
    assert record.payload["tape_id"] == "stable-tape"
    assert record.payload["execution_placement"] == "local_attached"
    assert record.payload["executor_ref_kind"] == "external_worker"
    assert record.payload["workspace_surface"] == "external_worker_workspace_ref"
    assert record.payload["execution_plane"] == "executor_plane"
    assert record.payload["previous_run_id"] == "run-0"
    assert record.payload["resume_from_run_id"] == "run-0"
    assert record.payload["resume_context_injected"] is True
    assert record.payload["executor_id"] == "executor-1"
    assert record.payload["message_type"] == "StreamDelta"
    assert record.payload["message"] == {
        "session_id": "session-1",
        "agent_id": "root",
        "timestamp": emitted_at.isoformat(),
        "content": "hello",
        "role": "assistant",
    }


@pytest.mark.asyncio
async def test_runtime_wire_event_recorder_falls_back_to_session_correlation() -> None:
    store = RecordingRuntimeEventStore()
    emitted_at = datetime(2026, 6, 3, 1, 3, tzinfo=UTC)
    recorder = RuntimeWireEventRecorder(
        store,
        new_event_id=lambda run_id: f"{run_id}:wire:fixed",
    )
    session = FakeSession(default_run_target=_external_worker_target())

    record = await recorder.append_wire_event(
        session,
        StreamDelta(
            session_id="session-1",
            agent_id="root",
            timestamp=emitted_at,
            content="hello",
            role="assistant",
        ),
    )

    assert record == store.events[0]
    assert record.payload["session_id"] == "session-1"
    assert record.payload["run_id"] == "run-1"
    assert record.payload["tape_id"] == "tape-1"
    assert record.payload["execution_placement"] == "local_attached"
    assert record.payload["executor_ref_kind"] == "external_worker"
    assert record.payload["workspace_surface"] == "external_worker_workspace_ref"
    assert record.payload["execution_plane"] == "executor_plane"


@pytest.mark.asyncio
async def test_runtime_wire_event_recorder_skips_without_store_or_turn() -> None:
    store = RecordingRuntimeEventStore()
    emitted_at = datetime(2026, 6, 3, 1, 3, tzinfo=UTC)
    message = StreamDelta(
        session_id="session-1",
        agent_id="root",
        timestamp=emitted_at,
        content="hello",
        role="assistant",
    )

    assert await RuntimeWireEventRecorder(None).append_wire_event(
        FakeSession(),
        message,
    ) is None
    assert await RuntimeWireEventRecorder(store).append_wire_event(
        FakeSession(current_turn_id=None),
        message,
    ) is None
    assert store.events == []
