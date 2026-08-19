from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from coding_agent.approval import ApprovalPolicy
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    JSONObject,
    RuntimeEventRecord,
)
from coding_agent.runs import (
    RemoteLoopOwnershipRetired,
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    RunTarget,
    RuntimeAttachedExecutorClaim,
    RuntimeAttachedExecutorClaimService,
    RuntimeAttachedExecutorFinalizeService,
    RuntimeAttachedExecutorRequestService,
    RuntimeAttachedExecutorService,
    run_target_execution_plane,
    run_target_execution_placement,
    run_target_executor_kind,
    run_target_executor_ref_kind,
    run_target_workspace_surface,
)


def _attached_target() -> RunTarget:
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(),
        executor=ExternalWorkerExecutorRef(executor_kind="local_cli"),
        isolation=IsolationPolicy(kind="external_worker_policy"),
    )


@dataclass
class FakeSession:
    id: str = "session-1"
    tape_id: str | None = "tape-1"
    provider_name: str | None = "openai"
    model_name: str | None = "gpt-test"
    approval_policy: ApprovalPolicy = ApprovalPolicy.YOLO
    max_steps: int = 12
    default_run_target: RunTarget = field(default_factory=_attached_target)
    turn_in_progress: bool = False
    turn_status: str = "idle"
    current_turn_id: str | None = None
    last_activity: datetime = datetime(2026, 6, 3, 11, 0, tzinfo=UTC)
    last_failure_details: object | None = "previous failure"


@dataclass(frozen=True)
class FakeResumeContext:
    previous_run_id: str = "run-previous"

    def metadata(self) -> JSONObject:
        return {"resume_reason": "user_resume"}


class RecordingLock:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self) -> None:
        self.calls.append("lock_enter")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.calls.append("lock_exit")


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
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await service.claim_run(
            executor_id="executor-1",
            executor_kind="local_cli",
            session_id="session-1",
            capabilities={"streaming": True},
        )

    assert requested.status == "requested"
    assert requested.parent_run_id == "run-previous"
    assert requested.metadata["prompt"] == "continue work"
    assert requested.metadata["run_request_status"] == "requested"
    assert requested.metadata["resume_reason"] == "user_resume"


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
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await service.claim_run(
            executor_id="executor-1",
            executor_kind="local_cli",
        )


@pytest.mark.asyncio
async def test_attached_executor_request_service_marks_session_turn_running() -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession()
    calls: list[str] = []
    store = FakeAttachedExecutorStore()
    attached_service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
    )
    persisted_sessions: list[FakeSession] = []

    async def assert_owner(session_id: str) -> None:
        calls.append(f"assert_owner:{session_id}")

    async def load_session(session_id: str) -> FakeSession:
        calls.append(f"load_session:{session_id}")
        return session

    async def persist_session(persisted_session: FakeSession) -> None:
        persisted_sessions.append(persisted_session)

    record = await RuntimeAttachedExecutorRequestService(
        lock=RecordingLock(calls),
        assert_owner=assert_owner,
        load_session=load_session,
        attached_executor=lambda: attached_service,
        persist_session=persist_session,
        session_is_attached=lambda session: isinstance(
            session.default_run_target.executor,
            ExternalWorkerExecutorRef,
        ),
        run_id_factory=lambda: "run-requested",
    ).request_run(
        "session-1",
        "run attached",
        resume_context=FakeResumeContext(),
    )

    assert record.run_id == "run-requested"
    assert record.status == "requested"
    assert record.metadata["prompt"] == "run attached"
    assert record.metadata["resume_reason"] == "user_resume"
    assert session.current_turn_id == "run-requested"
    assert session.turn_in_progress is True
    assert session.turn_status == "running"
    assert session.last_activity == now
    assert session.last_failure_details is None
    assert persisted_sessions == [session]
    assert calls == [
        "lock_enter",
        "assert_owner:session-1",
        "load_session:session-1",
        "lock_exit",
    ]


@pytest.mark.asyncio
async def test_attached_executor_request_service_rejects_non_attached_session() -> None:
    session = FakeSession()

    async def assert_owner(session_id: str) -> None:
        del session_id

    async def load_session(session_id: str) -> FakeSession:
        del session_id
        return session

    async def persist_session(persisted_session: FakeSession) -> None:
        del persisted_session
        raise AssertionError("non-attached request should not persist")

    with pytest.raises(
        ValueError,
        match="session does not use attached executor execution",
    ):
        await RuntimeAttachedExecutorRequestService(
            lock=RecordingLock([]),
            assert_owner=assert_owner,
            load_session=load_session,
            attached_executor=lambda: RuntimeAttachedExecutorService(
                store=FakeAttachedExecutorStore(),
                metadata_for_session=_metadata_for_session,
            ),
            persist_session=persist_session,
            session_is_attached=lambda session: False,
        ).request_run("session-1", "run attached")


@pytest.mark.asyncio
async def test_attached_executor_request_service_rejects_active_turn() -> None:
    session = FakeSession(turn_in_progress=True)

    async def assert_owner(session_id: str) -> None:
        del session_id

    async def load_session(session_id: str) -> FakeSession:
        del session_id
        return session

    async def persist_session(persisted_session: FakeSession) -> None:
        del persisted_session
        raise AssertionError("active turn request should not persist")

    with pytest.raises(RuntimeError, match="turn already in progress"):
        await RuntimeAttachedExecutorRequestService(
            lock=RecordingLock([]),
            assert_owner=assert_owner,
            load_session=load_session,
            attached_executor=lambda: RuntimeAttachedExecutorService(
                store=FakeAttachedExecutorStore(),
                metadata_for_session=_metadata_for_session,
            ),
            persist_session=persist_session,
            session_is_attached=lambda session: True,
        ).request_run("session-1", "run attached")


@pytest.mark.asyncio
async def test_attached_executor_claim_service_loads_session_and_wraps_claim() -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession()
    store = FakeAttachedExecutorStore()
    attached_service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
        token_urlsafe=lambda _: "claim-token",
    )
    await attached_service.request_run(
        session,
        prompt="run attached",
        run_id="run-1",
    )
    loaded_sessions: list[str] = []

    async def load_session(session_id: str) -> FakeSession:
        loaded_sessions.append(session_id)
        return session

    @dataclass(frozen=True)
    class ClaimEnvelope:
        run: AgentRunRecord
        claim_token: str
        prompt: str
        session: FakeSession

    def claim_factory(
        claim: RuntimeAttachedExecutorClaim,
        loaded_session: object,
    ) -> ClaimEnvelope:
        return ClaimEnvelope(
            run=claim.run,
            claim_token=claim.claim_token,
            prompt=claim.prompt,
            session=loaded_session,
        )

    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await RuntimeAttachedExecutorClaimService(
            attached_executor=lambda: attached_service,
            load_session=load_session,
            claim_factory=claim_factory,
        ).claim_run(
            executor_id="executor-1",
            executor_kind="local_cli",
            session_id="session-1",
            capabilities={"streaming": True},
        )

    assert loaded_sessions == []


@pytest.mark.asyncio
async def test_attached_executor_claim_service_returns_none_without_claim() -> None:
    loaded_sessions: list[str] = []

    async def load_session(session_id: str) -> FakeSession:
        loaded_sessions.append(session_id)
        return FakeSession()

    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await RuntimeAttachedExecutorClaimService(
            attached_executor=lambda: RuntimeAttachedExecutorService(
                store=FakeAttachedExecutorStore(),
                metadata_for_session=_metadata_for_session,
            ),
            load_session=load_session,
            claim_factory=lambda claim, session: claim,
        ).claim_run(
            executor_id="executor-1",
            executor_kind="local_cli",
            session_id="session-1",
        )

    assert loaded_sessions == []


@pytest.mark.asyncio
async def test_attached_executor_finalize_service_saves_tape_before_final_status() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession(turn_in_progress=True, turn_status="running")
    calls: list[str] = []
    store = FakeAttachedExecutorStore(calls=calls)
    attached_service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
        token_urlsafe=lambda _: "claim-token",
    )
    await attached_service.request_run(
        session,
        prompt="run attached",
        run_id="run-1",
    )
    claimed = await store.claim_attached_executor_run(
        session_id="session-1",
        executor_kind="local_cli",
        claim_metadata=_historical_claim_metadata(now),
    )
    assert claimed is not None
    persisted_sessions: list[FakeSession] = []

    async def load_session(session_id: str) -> FakeSession:
        calls.append(f"load_session:{session_id}")
        return session

    async def save_tape_entries(
        tape_id: str,
        entries: list[JSONObject],
    ) -> None:
        calls.append(f"save_tape:{tape_id}:{len(entries)}")

    async def persist_session(persisted_session: FakeSession) -> None:
        calls.append(f"persist_session:{persisted_session.id}")
        persisted_sessions.append(persisted_session)

    finalized = await RuntimeAttachedExecutorFinalizeService(
        lock=RecordingLock(calls),
        load_session=load_session,
        attached_executor=lambda: attached_service,
        save_tape_entries=save_tape_entries,
        persist_session=persist_session,
        now=lambda: now,
    ).finalize_run(
        run_id="run-1",
        executor_id="executor-1",
        claim_token="claim-token",
        status="completed",
        result={"ok": True},
        error=None,
        tape_id="tape-final",
        tape_entries=[{"kind": "message"}],
    )

    assert finalized.status == "completed"
    assert calls == [
        "save_tape:tape-final:1",
        "update_run:run-1:completed",
        "lock_enter",
        "load_session:session-1",
        "persist_session:session-1",
        "lock_exit",
    ]
    assert session.tape_id == "tape-final"
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.current_turn_id == "run-1"
    assert session.last_activity == now
    assert session.last_failure_details is None
    assert persisted_sessions == [session]


@pytest.mark.asyncio
async def test_attached_executor_finalize_service_does_not_finalize_when_tape_save_fails() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession(turn_in_progress=True, turn_status="running")
    calls: list[str] = []
    store = FakeAttachedExecutorStore(calls=calls)
    attached_service = RuntimeAttachedExecutorService(
        store=store,
        metadata_for_session=_metadata_for_session,
        now=lambda: now,
        token_urlsafe=lambda _: "claim-token",
    )
    await attached_service.request_run(
        session,
        prompt="run attached",
        run_id="run-1",
    )
    claimed = await store.claim_attached_executor_run(
        session_id="session-1",
        executor_kind="local_cli",
        claim_metadata=_historical_claim_metadata(now),
    )
    assert claimed is not None

    async def load_session(session_id: str) -> FakeSession:
        del session_id
        raise AssertionError("failed tape save should not load session")

    async def save_tape_entries(
        tape_id: str,
        entries: list[JSONObject],
    ) -> None:
        del tape_id, entries
        calls.append("save_tape_failed")
        raise RuntimeError("tape save failed")

    async def persist_session(persisted_session: FakeSession) -> None:
        del persisted_session
        raise AssertionError("failed tape save should not persist session")

    with pytest.raises(RuntimeError, match="tape save failed"):
        await RuntimeAttachedExecutorFinalizeService(
            lock=RecordingLock(calls),
            load_session=load_session,
            attached_executor=lambda: attached_service,
            save_tape_entries=save_tape_entries,
            persist_session=persist_session,
            now=lambda: now,
        ).finalize_run(
            run_id="run-1",
            executor_id="executor-1",
            claim_token="claim-token",
            status="completed",
            result={"ok": True},
            error=None,
            tape_id="tape-final",
            tape_entries=[{"kind": "message"}],
        )

    assert calls == ["save_tape_failed"]
    loaded = await store.load_agent_run("run-1")
    assert loaded is not None
    assert loaded.status == "claimed"
    assert session.turn_in_progress is True
    assert session.turn_status == "running"


def _historical_claim_metadata(now: datetime) -> JSONObject:
    return {
        "worker_id": "executor-1",
        "executor_id": "executor-1",
        "claim_token_hash": hashlib.sha256(b"claim-token").hexdigest(),
        "claimed_at": now.isoformat(),
        "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
    }


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
        "workspace_surface": run_target_workspace_surface(session.default_run_target),
        "execution_plane": run_target_execution_plane(session.default_run_target),
        "execution_placement": run_target_execution_placement(
            session.default_run_target
        ),
        "executor_kind": run_target_executor_kind(session.default_run_target),
    }
    executor_ref_kind = run_target_executor_ref_kind(session.default_run_target)
    if executor_ref_kind is not None:
        metadata["executor_ref_kind"] = executor_ref_kind
    if resume_context is not None:
        metadata.update(resume_context.metadata())
    return metadata


class FakeAttachedExecutorStore:
    def __init__(self, *, calls: list[str] | None = None) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: list[RuntimeEventRecord] = []
        self.calls = calls

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
        if self.calls is not None:
            self.calls.append(f"update_run:{run_id}:{status}")
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
