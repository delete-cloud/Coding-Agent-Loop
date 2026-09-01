from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentkit.runtime.contracts import (
    DispatchPermit,
    EffectCompletedResult,
    EffectIndeterminateResult,
    EffectMutation,
    EffectPlan,
    EffectStatus,
    OperationStateCAS,
    RuntimeCommand,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    EffectLedgerSlot,
    ExecutorAttemptConflictError,
    ExecutorAttemptRecord,
    UnstartedDispatchCloseoutGuard,
)
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID as MATRIX_SESSION_ID,
    SESSION_PAYLOAD as MATRIX_SESSION_PAYLOAD,
    _open_store,
)

SESSION_ID = "session-executor"
EFFECT_ID = "effect-executor"
ATTEMPT_ID = "attempt-executor"
AUTHORIZATION_ID = "authorization-executor"
OWNER_ID = "owner-executor"
STAMP = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


class QuietCancellation:
    cancelled = False

    async def wait(self) -> None:
        await asyncio.Event().wait()


class RecordingStore:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.effect = EffectLedgerSlot(
            effect_id=EFFECT_ID,
            status="dispatched",
            payload={
                "attempt_id": ATTEMPT_ID,
                "authorization_transition_id": AUTHORIZATION_ID,
                "dispatch_owner_epoch": 1,
                "effect_kind": "tool",
                "payload": {
                    "tool_call_id": "call-executor",
                    "tool_name": "read",
                    "arguments": {"path": "README.md"},
                },
                "idempotency_key": "idempotency-executor",
            },
        )
        self.attempt = ExecutorAttemptRecord(
            session_id=SESSION_ID,
            effect_id=EFFECT_ID,
            attempt_id=ATTEMPT_ID,
            authorization_transition_id=AUTHORIZATION_ID,
            dispatch_owner_epoch=1,
            status="authorized_unclaimed",
        )

    async def load_effect_slot(self, session_id: str, effect_id: str):
        self.timeline.append("load_effect")
        assert (session_id, effect_id) == (SESSION_ID, EFFECT_ID)
        return self.effect

    async def load_executor_attempt(
        self,
        session_id: str,
        effect_id: str,
        attempt_id: str,
        authorization_transition_id: str,
    ):
        self.timeline.append("load_attempt")
        assert (
            session_id,
            effect_id,
            attempt_id,
            authorization_transition_id,
        ) == (SESSION_ID, EFFECT_ID, ATTEMPT_ID, AUTHORIZATION_ID)
        return self.attempt

    async def reserve_executor_attempt(self, authority, **kwargs):
        self.timeline.append("reserve")
        self.attempt = replace(
            self.attempt,
            status="reserved",
            executor_id=kwargs["executor_id"],
            claim_generation=1,
            reservation_lease_expires_at=kwargs["lease_expires_at"],
        )
        return self.attempt

    async def mark_executor_attempt_started(self, authority, **kwargs):
        self.timeline.append("started")
        self.attempt = replace(
            self.attempt,
            status="started",
        )
        return self.attempt

    async def quiesce_claimed_executor_attempt(self, authority, **kwargs):
        self.timeline.append("quiescent")
        self.attempt = replace(
            self.attempt,
            status="quiescent",
            quiescence_evidence_ref=kwargs["evidence_ref"],
        )
        return self.attempt


class CompletingBackend:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.invocations = []

    async def execute(self, invocation, cancellation):
        self.timeline.append("backend")
        self.invocations.append((invocation, cancellation))
        return EffectCompletedResult(result={"content": "done"})


def _permit() -> DispatchPermit:
    permit = DispatchPermit.issue(
        opaque_token="opaque-executor",
        session_id=SESSION_ID,
        effect_id=EFFECT_ID,
        attempt_id=ATTEMPT_ID,
        authorization_transition_id=AUTHORIZATION_ID,
        owner_epoch=1,
        idempotency_key="idempotency-executor",
    )
    permit.claim()
    return permit


def _executor(store, backend):
    from coding_agent.executors.durable import DurableEffectExecutor

    return DurableEffectExecutor(
        store,
        owner_id=OWNER_ID,
        executor_id="executor-one",
        backend=backend,
        clock=lambda: STAMP,
        reservation_lease=timedelta(seconds=30),
        quiescence_evidence_factory=lambda invocation, generation: (
            f"quiescent:{invocation.effect_id}:{generation}"
        ),
    )


@pytest.mark.asyncio
async def test_durable_executor_starts_backend_only_after_started_row() -> None:
    timeline: list[str] = []
    store = RecordingStore(timeline)
    backend = CompletingBackend(timeline)

    result = await _executor(store, backend).execute(_permit(), QuietCancellation())

    assert result == EffectCompletedResult(result={"content": "done"})
    assert timeline == [
        "load_effect",
        "load_attempt",
        "reserve",
        "started",
        "backend",
        "quiescent",
    ]
    invocation, _ = backend.invocations[0]
    assert invocation.payload["tool_name"] == "read"
    assert store.attempt.status == "quiescent"


@pytest.mark.asyncio
async def test_backend_exception_is_indeterminate_and_quiescent() -> None:
    timeline: list[str] = []
    store = RecordingStore(timeline)

    class FailingBackend:
        async def execute(self, invocation, cancellation):
            del invocation, cancellation
            timeline.append("backend")
            raise RuntimeError()

    result = await _executor(store, FailingBackend()).execute(
        _permit(),
        QuietCancellation(),
    )

    assert isinstance(result, EffectIndeterminateResult)
    assert result.reason_code == "effect_backend_error"
    assert result.message.strip()
    assert timeline[-1] == "quiescent"


@pytest.mark.asyncio
async def test_cancelled_backend_quiesces_then_reraises() -> None:
    timeline: list[str] = []
    store = RecordingStore(timeline)

    class CancelledBackend:
        async def execute(self, invocation, cancellation):
            del invocation, cancellation
            timeline.append("backend")
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _executor(store, CancelledBackend()).execute(
            _permit(),
            QuietCancellation(),
        )

    assert timeline[-2:] == ["backend", "quiescent"]
    assert store.attempt.status == "quiescent"


def test_durable_executor_rejects_unclaimed_permit() -> None:
    timeline: list[str] = []
    store = RecordingStore(timeline)
    backend = CompletingBackend(timeline)
    permit = DispatchPermit.issue(
        opaque_token="opaque-unclaimed",
        session_id=SESSION_ID,
        effect_id=EFFECT_ID,
        attempt_id=ATTEMPT_ID,
        authorization_transition_id=AUTHORIZATION_ID,
        owner_epoch=1,
        idempotency_key="idempotency-executor",
    )

    with pytest.raises(ValueError, match="claimed"):
        _executor(store, backend).validate_permit(permit)
    assert timeline == []


@pytest.fixture(params=["sqlite", "pg"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def _authorized_store(store_kind: str, tmp_path: Path):
    store, owner = await _open_store(store_kind, tmp_path)
    plan = EffectPlan(
        effect_id="matrix-executor-effect",
        attempt_id="matrix-executor-attempt",
        effect_kind="tool",
        payload={
            "tool_call_id": "matrix-call",
            "tool_name": "read",
            "arguments": {"path": "README.md"},
        },
        idempotency_key="matrix-idempotency",
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=MATRIX_SESSION_PAYLOAD,
            transition_id="matrix-prepare",
            state_cas=OperationStateCAS("matrix-run", 0, 0),
            state_value={"phase": "prepared"},
            effect_mutations=(EffectMutation.prepare(plan),),
            effect_plans=(plan,),
        ),
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=MATRIX_SESSION_PAYLOAD,
            transition_id="matrix-authorize",
            state_cas=OperationStateCAS("matrix-run", 1, 0),
            state_value={"phase": "authorized"},
            effect_mutation=EffectMutation(
                effect_id=plan.effect_id,
                attempt_id=plan.attempt_id,
                expected_status=EffectStatus.PREPARED,
                status=EffectStatus.DISPATCHED,
                payload={},
            ),
            expected_mailbox_cut="0",
        ),
    )
    permit = DispatchPermit.issue(
        opaque_token="matrix-permit",
        session_id=MATRIX_SESSION_ID,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        owner_epoch=owner.epoch,
        idempotency_key=plan.idempotency_key,
    )
    permit.claim()
    return store, owner, plan, permit


class ClaimBoundaryGate:
    def __init__(self, store: Any, boundary: str) -> None:
        self.store = store
        self.boundary = boundary
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self.store, name)

    async def _before(self, name: str, operation):
        if self.boundary == name:
            self.entered.set()
            await self.release.wait()
        return await operation()

    async def _after(self, name: str, operation):
        result = await operation()
        if self.boundary == name:
            self.entered.set()
            await self.release.wait()
        return result

    async def load_effect_slot(self, *args, **kwargs):
        return await self._before(
            "effect_load",
            lambda: self.store.load_effect_slot(*args, **kwargs),
        )

    async def load_executor_attempt(self, *args, **kwargs):
        return await self._before(
            "attempt_load",
            lambda: self.store.load_executor_attempt(*args, **kwargs),
        )

    async def reserve_executor_attempt(self, *args, **kwargs):
        return await self._after(
            "reservation",
            lambda: self.store.reserve_executor_attempt(*args, **kwargs),
        )

    async def mark_executor_attempt_started(self, *args, **kwargs):
        return await self._after(
            "start",
            lambda: self.store.mark_executor_attempt_started(*args, **kwargs),
        )

    async def quiesce_claimed_executor_attempt(self, *args, **kwargs):
        return await self._before(
            "quiescence",
            lambda: self.store.quiesce_claimed_executor_attempt(
                *args,
                **kwargs,
            ),
        )


async def _cancel_at_claim_boundary(
    store_kind: str,
    tmp_path: Path,
    boundary: str,
) -> None:
    store, owner, plan, permit = await _authorized_store(store_kind, tmp_path)
    gated = ClaimBoundaryGate(store, boundary)
    backend = CompletingBackend([])
    executor = _real_executor(gated, owner, backend=backend)
    execution = asyncio.create_task(executor.execute(permit, QuietCancellation()))
    await gated.entered.wait()
    execution.cancel()
    gated.release.set()

    with pytest.raises(asyncio.CancelledError):
        await execution
    attempt = await store.load_executor_attempt(
        MATRIX_SESSION_ID,
        plan.effect_id,
        plan.attempt_id,
        "matrix-authorize",
    )
    assert attempt is not None and attempt.status == "quiescent"
    assert backend.invocations == []


def _real_executor(
    store: Any,
    owner: OwnerAuthority,
    *,
    backend: Any | None = None,
):
    from coding_agent.executors.durable import DurableEffectExecutor

    return DurableEffectExecutor(
        store,
        owner_id=owner.owner_id,
        executor_id="matrix-executor",
        backend=backend or CompletingBackend([]),
        clock=lambda: STAMP,
        reservation_lease=timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_executor_cancellation_during_effect_load_quiesces_unclaimed_attempt(
    store_kind: str,
    tmp_path: Path,
) -> None:
    await _cancel_at_claim_boundary(store_kind, tmp_path, "effect_load")


@pytest.mark.asyncio
async def test_executor_cancellation_during_attempt_load_quiesces_unclaimed_attempt(
    store_kind: str,
    tmp_path: Path,
) -> None:
    await _cancel_at_claim_boundary(store_kind, tmp_path, "attempt_load")


@pytest.mark.asyncio
async def test_executor_cancellation_during_reservation_quiesces_claim(
    store_kind: str,
    tmp_path: Path,
) -> None:
    await _cancel_at_claim_boundary(store_kind, tmp_path, "reservation")


@pytest.mark.asyncio
async def test_executor_cancellation_during_start_quiesces_claim(
    store_kind: str,
    tmp_path: Path,
) -> None:
    await _cancel_at_claim_boundary(store_kind, tmp_path, "start")


@pytest.mark.asyncio
async def test_quiescence_cleanup_survives_repeated_task_cancellation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner, plan, permit = await _authorized_store(store_kind, tmp_path)
    gated = ClaimBoundaryGate(store, "quiescence")
    execution = asyncio.create_task(
        _real_executor(gated, owner).execute(permit, QuietCancellation())
    )
    await gated.entered.wait()
    execution.cancel()
    execution.cancel()
    gated.release.set()

    with pytest.raises(asyncio.CancelledError):
        await execution
    attempt = await store.load_executor_attempt(
        MATRIX_SESSION_ID,
        plan.effect_id,
        plan.attempt_id,
        "matrix-authorize",
    )
    assert attempt is not None and attempt.status == "quiescent"


@pytest.mark.asyncio
async def test_quiesce_unclaimed_attempt_persists_complete_record(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner, plan, _permit = await _authorized_store(store_kind, tmp_path)
    quiescent = await store.quiesce_claimed_executor_attempt(
        owner,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="matrix-executor",
        now=STAMP,
        evidence_ref="unclaimed-quiescence",
    )

    assert quiescent.status == "quiescent"
    assert quiescent.executor_id == "matrix-executor"
    assert quiescent.claim_generation == 1
    assert quiescent.reservation_lease_expires_at == STAMP
    assert quiescent.quiescence_evidence_ref == "unclaimed-quiescence"


@pytest.mark.asyncio
async def test_unstarted_closeout_guard_is_snapshotted_fingerprinted_and_atomic(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner, plan, _permit = await _authorized_store(store_kind, tmp_path)
    guard = UnstartedDispatchCloseoutGuard(
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="coordinator-unstarted",
        evidence_ref="control-after-authorization",
        closed_at=STAMP,
    )
    mutation = EffectMutation(
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        expected_status=EffectStatus.DISPATCHED,
        status=EffectStatus.UNKNOWN,
        payload={"authorization_transition_id": "matrix-authorize"},
    )
    unit = AuthoritativeUnitOfWork(
        event=None,
        session_state=MATRIX_SESSION_PAYLOAD,
        transition_id="matrix-unstarted-closeout",
        state_cas=OperationStateCAS("matrix-run", 2, 0),
        state_value={"phase": "unknown"},
        effect_mutation=mutation,
        terminal_action=True,
        unstarted_dispatch_closeout=guard,
    )

    committed = await store.commit_authoritative_uow(owner, unit)
    attempt = await store.load_executor_attempt(
        MATRIX_SESSION_ID,
        plan.effect_id,
        plan.attempt_id,
        "matrix-authorize",
    )
    effect = await store.load_effect_slot(MATRIX_SESSION_ID, plan.effect_id)

    assert committed.transition_receipt is not None
    assert attempt is not None and attempt.status == "quiescent"
    assert attempt.executor_id == "coordinator-unstarted"
    assert attempt.quiescence_evidence_ref == "control-after-authorization"
    assert effect is not None and effect.status == "unknown"
    assert effect.payload["authorization_transition_id"] == "matrix-authorize"


@pytest.mark.asyncio
async def test_quiesce_claimed_attempt_replay_and_conflicts(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner, plan, _permit = await _authorized_store(store_kind, tmp_path)
    await store.reserve_executor_attempt(
        owner,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="matrix-executor",
        lease_expires_at=STAMP + timedelta(seconds=30),
    )
    first = await store.quiesce_claimed_executor_attempt(
        owner,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="matrix-executor",
        now=STAMP,
        evidence_ref="claimed-quiescence",
    )
    replay = await store.quiesce_claimed_executor_attempt(
        owner,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="matrix-executor",
        now=STAMP,
        evidence_ref="claimed-quiescence",
    )
    assert replay == first

    with pytest.raises(ExecutorAttemptConflictError):
        await store.quiesce_claimed_executor_attempt(
            owner,
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            authorization_transition_id="matrix-authorize",
            executor_id="other-executor",
            now=STAMP,
            evidence_ref="claimed-quiescence",
        )


@pytest.mark.asyncio
async def test_reserve_executor_attempt_preserves_authorization_watermarks(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    admission = await store.admit_new_runtime_command(
        owner,
        RuntimeCommand(
            command_id="watermark-interrupt",
            command_kind="interrupt",
            payload={"target_run_id": "other-run"},
        ),
    )
    plan = EffectPlan(
        effect_id="matrix-executor-effect",
        attempt_id="matrix-executor-attempt",
        effect_kind="tool",
        payload={
            "tool_call_id": "matrix-call",
            "tool_name": "read",
            "arguments": {"path": "README.md"},
        },
        idempotency_key="matrix-idempotency",
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=MATRIX_SESSION_PAYLOAD,
            transition_id="matrix-prepare",
            state_cas=OperationStateCAS("matrix-run", 0, 0),
            state_value={"phase": "prepared"},
            effect_mutations=(EffectMutation.prepare(plan),),
            effect_plans=(plan,),
        ),
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=MATRIX_SESSION_PAYLOAD,
            transition_id="matrix-authorize",
            state_cas=OperationStateCAS("matrix-run", 1, 0),
            state_value={"phase": "authorized"},
            effect_mutation=EffectMutation(
                effect_id=plan.effect_id,
                attempt_id=plan.attempt_id,
                expected_status=EffectStatus.PREPARED,
                status=EffectStatus.DISPATCHED,
                payload={},
            ),
            expected_mailbox_cut=admission.mailbox_cut,
        ),
    )
    authorized = await store.load_executor_attempt(
        MATRIX_SESSION_ID,
        plan.effect_id,
        plan.attempt_id,
        "matrix-authorize",
    )
    reserved = await store.reserve_executor_attempt(
        owner,
        effect_id=plan.effect_id,
        attempt_id=plan.attempt_id,
        authorization_transition_id="matrix-authorize",
        executor_id="matrix-executor",
        lease_expires_at=STAMP + timedelta(seconds=30),
    )

    assert authorized is not None
    assert reserved.authorization_mailbox_cut == authorized.authorization_mailbox_cut
    assert (
        reserved.authorization_mailbox_session_seq
        == authorized.authorization_mailbox_session_seq
    )
    assert reserved.authorization_mailbox_cut == admission.mailbox_cut
    assert reserved.authorization_mailbox_cut != "0"


@pytest.mark.asyncio
async def test_await_task_through_cancellation_returns_completed_result() -> None:
    from coding_agent.executors.durable import _await_task_through_cancellation

    release = asyncio.Event()

    async def finished() -> str:
        await release.wait()
        return "claimed"

    task = asyncio.create_task(finished())
    wait_task = asyncio.create_task(_await_task_through_cancellation(task))
    await asyncio.sleep(0)
    wait_task.cancel()
    wait_task.cancel()
    release.set()
    result, cancelled = await wait_task
    assert result == "claimed"
    assert cancelled is True
