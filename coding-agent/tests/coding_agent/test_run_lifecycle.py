from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.runs import RuntimeRunLifecycle


@dataclass
class FakeSession:
    id: str
    tape_id: str | None
    provider: str = "openai"


@dataclass
class FakeResumeContext:
    previous_run_id: str


class RecordingRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []

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


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_skips_storeless_runs() -> None:
    metadata_calls: list[FakeSession] = []
    lifecycle = RuntimeRunLifecycle(
        store=None,
        metadata_for_session=lambda session, *, resume_context=None: metadata_calls.append(
            session
        )
        or {},
    )

    created = await lifecycle.create(
        FakeSession(id="session-1", tape_id="tape-1"),
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert created is False
    assert metadata_calls == []


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_starts_queued_then_running_run() -> None:
    store = RecordingRuntimeStore()
    metadata_calls: list[tuple[str, str | None]] = []

    def metadata_for_session(
        session: FakeSession,
        *,
        resume_context: FakeResumeContext | None = None,
    ) -> JSONObject:
        metadata_calls.append(
            (session.id, None if resume_context is None else resume_context.previous_run_id)
        )
        return {
            "provider_name": session.provider,
            "resume_from": None
            if resume_context is None
            else resume_context.previous_run_id,
        }

    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=metadata_for_session,
    )
    session = FakeSession(id="session-1", tape_id="tape-1")
    resume_context = FakeResumeContext(previous_run_id="previous-run")
    started_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    created = await lifecycle.start(
        session,
        run_id="run-1",
        started_at=started_at,
        resume_context=resume_context,
    )

    assert created is True
    assert metadata_calls == [("session-1", "previous-run"), ("session-1", "previous-run")]
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
            "status": "running",
            "ended_at": None,
            "metadata": {"provider_name": "openai", "resume_from": "previous-run"},
            "result": {},
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_finishes_run_with_current_metadata() -> None:
    store = RecordingRuntimeStore()
    finished_at = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "provider_name": session.provider,
            "tape_id": session.tape_id,
        },
        now=lambda: finished_at,
    )
    session = FakeSession(id="session-1", tape_id="tape-finished", provider="anthropic")
    _ = await lifecycle.create(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await lifecycle.finish(
        session,
        run_id="run-1",
        status="failed",
        result=cast(JSONObject, {"steps_taken": 2}),
        error="boom",
    )

    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "failed",
            "ended_at": finished_at,
            "metadata": {
                "provider_name": "anthropic",
                "tape_id": "tape-finished",
            },
            "result": {"steps_taken": 2},
            "error": "boom",
        }
    ]
