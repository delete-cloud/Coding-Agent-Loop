from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from coding_agent.approval import ApprovalPolicy
from coding_agent.environment.execution_binding import ExternalWorkerBinding
from coding_agent.runtime_store import AgentRunRecord, JSONObject, RuntimeEventRecord
from coding_agent.runs import RuntimeAttachedExecutorService


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"
    provider_name: str | None = "openai"
    model_name: str | None = "gpt-test"
    approval_policy: ApprovalPolicy = ApprovalPolicy.YOLO
    max_steps: int = 12
    execution_binding: ExternalWorkerBinding = ExternalWorkerBinding(
        executor_kind="local_cli"
    )


@dataclass(frozen=True)
class FakeResumeContext:
    previous_run_id: str = "run-previous"

    def metadata(self) -> JSONObject:
        return {"resume_reason": "user_resume"}


@pytest.mark.asyncio
async def test_attached_executor_service_requests_claims_updates_events_and_finalizes() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    store = FakeAttachedExecutorStore()
    service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
        token_urlsafe=lambda _: "claim-token",
    )

    requested = await service.request_run(
        FakeSession(),
        prompt="continue work",
        run_id="run-1",
        resume_context=FakeResumeContext(),
    )
    claim = await service.claim_run(
        executor_id="executor-1",
        executor_kind="local_cli",
        session_id="session-1",
        capabilities={"streaming": True},
    )
    assert claim is not None
    heartbeat = await service.heartbeat_run(
        run_id="run-1",
        executor_id="executor-1",
        claim_token=claim.claim_token,
        worker_instance_id="worker-1",
    )
    event = await service.append_event(
        run_id="run-1",
        executor_id="executor-1",
        claim_token=claim.claim_token,
        event_id="event-1",
        event_kind="wire.StreamDelta",
        payload={"content": "hello"},
        created_at=now,
    )
    finalized = await service.finalize_run(
        run_id="run-1",
        executor_id="executor-1",
        claim_token=claim.claim_token,
        status="completed",
        result={"ok": True},
        error=None,
        tape_id="tape-final",
    )

    assert requested.status == "requested"
    assert requested.parent_run_id == "run-previous"
    assert requested.metadata["prompt"] == "continue work"
    assert requested.metadata["run_request_status"] == "requested"
    assert requested.metadata["resume_reason"] == "user_resume"
    assert claim.run.status == "claimed"
    assert claim.prompt == "continue work"
    assert claim.claim_token == "claim-token"
    assert claim.run.metadata["executor_id"] == "executor-1"
    assert claim.run.metadata["capabilities"] == {"streaming": True}
    assert heartbeat.status == "running"
    assert heartbeat.metadata["worker_instance_id"] == "worker-1"
    assert event.payload["content"] == "hello"
    assert event.payload["session_id"] == "session-1"
    assert event.payload["run_id"] == "run-1"
    assert finalized.status == "completed"
    assert finalized.metadata["final_tape_id"] == "tape-final"
    assert finalized.result == {"ok": True}


@pytest.mark.asyncio
async def test_attached_executor_service_rejects_wrong_executor_owner() -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    store = FakeAttachedExecutorStore()
    service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
        token_urlsafe=lambda _: "claim-token",
    )
    await service.request_run(
        FakeSession(),
        prompt="continue work",
        run_id="run-1",
    )
    claim = await service.claim_run(
        executor_id="executor-1",
        executor_kind="local_cli",
    )
    assert claim is not None

    with pytest.raises(
        PermissionError,
        match="attached executor does not own this run",
    ):
        await service.heartbeat_run(
            run_id="run-1",
            executor_id="executor-2",
            claim_token=claim.claim_token,
        )


def _metadata_for_session(
    session: FakeSession,
    *,
    resume_context: FakeResumeContext | None = None,
) -> JSONObject:
    metadata: JSONObject = {
        "provider_name": session.provider_name,
        "model_name": session.model_name,
        "approval_policy": session.approval_policy.value,
        "max_steps": session.max_steps,
        "execution_binding_kind": session.execution_binding.kind,
        "workspace_surface": session.execution_binding.workspace_surface,
        "execution_plane": session.execution_binding.execution_plane,
        "execution_placement": "local_attached",
        "executor_kind": session.execution_binding.executor_kind,
    }
    if resume_context is not None:
        metadata.update(resume_context.metadata())
    return metadata


class FakeAttachedExecutorStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: list[RuntimeEventRecord] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
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
        existing = self.runs[run_id]
        updated = AgentRunRecord(
            run_id=existing.run_id,
            session_id=existing.session_id,
            tape_id=existing.tape_id,
            parent_run_id=existing.parent_run_id,
            agent_id=existing.agent_id,
            status=status,
            started_at=existing.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        self.runs[run_id] = updated
        return updated

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        for run in self.runs.values():
            if session_id is not None and run.session_id != session_id:
                continue
            if run.status != "requested":
                continue
            if run.metadata.get("executor_kind") != executor_kind:
                continue
            claimed = AgentRunRecord(
                run_id=run.run_id,
                session_id=run.session_id,
                tape_id=run.tape_id,
                parent_run_id=run.parent_run_id,
                agent_id=run.agent_id,
                status="claimed",
                started_at=run.started_at,
                ended_at=run.ended_at,
                metadata={**run.metadata, **claim_metadata},
                result=run.result,
                error=run.error,
            )
            self.runs[run.run_id] = claimed
            return claimed
        return None

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        self.events.append(record)
        return record
