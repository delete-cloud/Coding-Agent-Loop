from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from typing import cast

import pytest

from coding_agent.runtime_store import (
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
)
from coding_agent.runs import RuntimeRunPersistenceService


@dataclass
class FakeSession:
    id: str
    tape_id: str | None
    provider: str = "openai"
    turn_status: str = "running"
    last_failure_details: str | None = None


@dataclass
class FakeResumeContext:
    previous_run_id: str


class RecordingRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []
        self.snapshots: list[RunMessageSnapshotRecord] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.created.append(record)
        return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        self.updated.append(
            {
                "run_id": run_id,
                "status": status,
                "ended_at": ended_at,
                "metadata": metadata,
                "result": result,
                "error": error,
            }
        )
        created = self.created[-1]
        return AgentRunRecord(
            run_id=run_id,
            session_id=created.session_id,
            tape_id=created.tape_id,
            parent_run_id=created.parent_run_id,
            agent_id=created.agent_id,
            status=status,
            started_at=created.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots.append(record)
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        for snapshot in self.snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        return None

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        return [snapshot for snapshot in self.snapshots if snapshot.run_id == run_id]


@pytest.mark.asyncio
async def test_runtime_run_persistence_service_finishes_run_with_current_metadata() -> (
    None
):
    store = RecordingRuntimeStore()
    finished_at = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
    resume_context = FakeResumeContext(previous_run_id="previous-run")

    def metadata_for_session(
        session: FakeSession,
        *,
        resume_context: FakeResumeContext | None = None,
    ) -> JSONObject:
        return {
            "provider_name": session.provider,
            "resume_from": None
            if resume_context is None
            else resume_context.previous_run_id,
        }

    service = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=store,
        metadata_for_session=metadata_for_session,
        now=lambda: finished_at,
    )
    session = FakeSession(id="session-1", tape_id="tape-1")
    started_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    created = await service.lifecycle().create(
        session,
        run_id="run-1",
        started_at=started_at,
        resume_context=resume_context,
    )
    session.provider = "anthropic"
    await service.finish_run(
        session,
        run_id="run-1",
        status="failed",
        result=cast(JSONObject, {"steps_taken": 2}),
        error="boom",
        resume_context=resume_context,
    )

    assert created is True
    assert store.created == [
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id="previous-run",
            agent_id=None,
            status="queued",
            started_at=started_at,
            metadata={"provider_name": "openai", "resume_from": "previous-run"},
            result={},
            error=None,
        )
    ]
    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "failed",
            "ended_at": finished_at,
            "metadata": {
                "provider_name": "anthropic",
                "resume_from": "previous-run",
            },
            "result": {"steps_taken": 2},
            "error": "boom",
        }
    ]


class MessageRole(Enum):
    SYSTEM = "system"


@dataclass(frozen=True)
class RuntimeMessage:
    role: MessageRole
    content: str
    created_at: datetime
    tags: tuple[str, ...]


@pytest.mark.asyncio
async def test_runtime_run_persistence_service_saves_latest_context_snapshot() -> None:
    store = RecordingRuntimeStore()
    saved_at = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
    message_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    service = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=store,
        metadata_for_session=lambda session, *, resume_context=None: {},
        now=lambda: saved_at,
    )
    session = FakeSession(id="session-1", tape_id="tape-1")
    ctx = SimpleNamespace(
        messages=[
            RuntimeMessage(
                role=MessageRole.SYSTEM,
                content="runtime context",
                created_at=message_time,
                tags=("context", "latest"),
            ),
            {"role": "user", "content": "hello"},
        ]
    )

    await service.save_message_snapshot(session, ctx, run_id="run-1")

    assert store.snapshots == [
        RunMessageSnapshotRecord(
            snapshot_id="run-1:latest",
            run_id="run-1",
            messages=[
                {
                    "role": "system",
                    "content": "runtime context",
                    "created_at": message_time.isoformat(),
                    "tags": ["context", "latest"],
                },
                {"role": "user", "content": "hello"},
            ],
            metadata={
                "session_id": "session-1",
                "tape_id": "tape-1",
                "message_count": 2,
                "snapshot_kind": "latest_context",
            },
            created_at=saved_at,
        )
    ]


@pytest.mark.asyncio
async def test_runtime_run_persistence_service_skips_snapshot_without_source_messages() -> (
    None
):
    store = RecordingRuntimeStore()
    service = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=store,
        metadata_for_session=lambda session, *, resume_context=None: {},
    )

    await service.save_message_snapshot(
        FakeSession(id="session-1", tape_id="tape-1"),
        SimpleNamespace(messages=None),
        run_id="run-1",
    )

    assert store.snapshots == []


@pytest.mark.asyncio
async def test_runtime_run_persistence_service_rejects_non_object_snapshot_entries() -> (
    None
):
    store = RecordingRuntimeStore()
    service = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=store,
        metadata_for_session=lambda session, *, resume_context=None: {},
    )

    with pytest.raises(
        TypeError,
        match="runtime message snapshot entries must be JSON objects",
    ):
        await service.save_message_snapshot(
            FakeSession(id="session-1", tape_id="tape-1"),
            SimpleNamespace(messages=["plain text"]),
            run_id="run-1",
        )

    assert store.snapshots == []
