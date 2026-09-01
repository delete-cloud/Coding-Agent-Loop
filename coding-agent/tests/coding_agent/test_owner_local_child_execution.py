from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from agentkit.runtime import (
    AppliedCommandDisposition,
    BlockedOutcome,
    CommitRef,
    CompletedOutcome,
    CancelledOutcome,
    EffectCompletedResult,
    EffectFailedResult,
    OperationStateVersion,
    SafeYieldOutcome,
    RuntimeCommand,
)
from coding_agent.executors.durable import DurableEffectInvocation
from coding_agent.runs.child_execution import (
    ChildControlBatch,
    OwnerLocalChildEffectBackend,
    RecoveredChildCommitPort,
    approval_resolved_input_id,
    child_execution_binding,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import (
    CommandMailboxEntry,
    RecoveredChildControlState,
    RecoveredChildExecutionLease,
    RecoveryLeaseConflictError,
)


def _state(
    revision: int = 1,
    *,
    value: dict[str, object] | None = None,
) -> OperationStateVersion:
    return OperationStateVersion(
        run_id="child-run",
        revision=revision,
        projection_epoch=0,
        commit_ref=CommitRef(transition_id=f"child-transition-{revision}"),
        value={} if value is None else value,
    )


def _binding():
    return child_execution_binding(
        session_id="session-a",
        parent_run_id="parent-run",
        parent_effect_id="parent-effect",
        parent_attempt_id="parent-attempt",
        prepared_transition_id="parent-prepared",
    )


def _invocation(binding):
    return DurableEffectInvocation(
        session_id=binding.session_id,
        effect_id=binding.parent_effect_id,
        attempt_id=binding.parent_attempt_id,
        authorization_transition_id=binding.authorization_transition_id,
        owner_epoch=3,
        effect_kind="tool",
        payload={"tool_name": "subagent", "arguments": {"task": "inspect"}},
        idempotency_key=None,
    )


@dataclass
class Cancellation:
    cancelled: bool = False


class BindingStore:
    def __init__(self, binding) -> None:
        self.binding = binding

    async def load_child_execution_binding(
        self,
        session_id: str,
        *,
        parent_effect_id: str | None = None,
        child_run_id: str | None = None,
    ):
        del session_id, child_run_id
        if parent_effect_id == self.binding.parent_effect_id:
            return self.binding
        return None


class Driver:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.approvals: list[ChildControlBatch] = []
        self.timeline: list[str] = []

    async def next_outcome(self, binding, cancellation):
        del binding, cancellation
        self.timeline.append("next")
        return self.outcomes.pop(0)

    async def apply_approval(self, binding, batch):
        del binding
        self.timeline.append("approval")
        self.approvals.append(batch)

    async def cancel_and_quiesce(self, binding):
        del binding
        self.timeline.append("quiesce")

    async def settle_claimed_permits(self, binding):
        del binding
        self.timeline.append("settle")


class Controls:
    def __init__(self, batches=(), *, immediate: bool = True) -> None:
        self.batches = list(batches)
        self.immediate = immediate
        self.waiting = asyncio.Event()

    async def observe(self, binding, *, after_generation: int):
        del binding, after_generation
        return self.batches.pop(0) if self.immediate and self.batches else None

    async def wait(self, binding, *, after_generation: int):
        del binding, after_generation
        await self.waiting.wait()
        return self.batches.pop(0)


@pytest.mark.asyncio
async def test_child_blocked_keeps_parent_execute_and_permit_live() -> None:
    binding = _binding()
    driver = Driver(
        [
            BlockedOutcome(
                state_version=_state(),
                reason="approval_required",
                effect=None,
                steps_taken=1,
            ),
            CompletedOutcome(
                state_version=_state(2),
                final_message="child done",
                steps_taken=2,
                stop_reason="completed",
            ),
        ]
    )
    controls = Controls(
        [
            ChildControlBatch(
                generation=2,
                command_id="approval-command",
                kind="approval",
                approved=True,
            )
        ],
        immediate=False,
    )
    backend = OwnerLocalChildEffectBackend(
        BindingStore(binding),
        driver=driver,
        controls=controls,
    )

    execution = asyncio.create_task(
        backend.execute(_invocation(binding), Cancellation())
    )
    await asyncio.sleep(0)
    assert not execution.done()
    controls.waiting.set()

    result = await execution
    assert isinstance(result, EffectCompletedResult)
    assert result.result == {
        "content": "child done",
        "child_run_id": binding.child_run_id,
    }


@pytest.mark.asyncio
async def test_approval_denial_disposition_clears_probe_and_continues_child() -> None:
    binding = _binding()
    denial = ChildControlBatch(
        generation=4,
        command_id="approval-denied",
        kind="approval",
        approved=False,
        reason_code="user_denied",
        reason_message="denied",
    )
    driver = Driver(
        [
            SafeYieldOutcome(
                state_version=_state(),
                reason="targeted approval command",
                steps_taken=1,
            ),
            CompletedOutcome(
                state_version=_state(2),
                final_message="continued",
                steps_taken=2,
                stop_reason="completed",
            ),
        ]
    )
    backend = OwnerLocalChildEffectBackend(
        BindingStore(binding),
        driver=driver,
        controls=Controls([denial]),
    )

    result = await backend.execute(_invocation(binding), Cancellation())

    assert isinstance(result, EffectCompletedResult)
    assert driver.approvals == [denial]
    assert driver.timeline == ["next", "approval", "next"]


@pytest.mark.asyncio
async def test_cancelled_child_outcome_maps_to_stable_parent_failure() -> None:
    binding = _binding()
    backend = OwnerLocalChildEffectBackend(
        BindingStore(binding),
        driver=Driver(
            [
                CancelledOutcome(
                    state_version=_state(),
                    command_disposition=AppliedCommandDisposition("cancel-child"),
                    steps_taken=1,
                )
            ]
        ),
        controls=Controls(),
    )

    result = await backend.execute(_invocation(binding), Cancellation())

    assert isinstance(result, EffectFailedResult)
    assert result.error.code == "child_cancelled"
    assert result.error.message == "child execution was cancelled"


@pytest.mark.asyncio
async def test_child_cancel_settles_child_permits_before_parent() -> None:
    binding = _binding()
    driver = Driver(
        [
            SafeYieldOutcome(
                state_version=_state(),
                reason="targeted cancel command",
                steps_taken=1,
            )
        ]
    )
    cancel = ChildControlBatch(
        generation=7,
        command_id="cancel-child",
        kind="cancel",
    )
    backend = OwnerLocalChildEffectBackend(
        BindingStore(binding),
        driver=driver,
        controls=Controls([cancel]),
    )

    result = await backend.execute(_invocation(binding), Cancellation())

    assert isinstance(result, EffectFailedResult)
    assert result.error.code == "child_cancelled"
    assert driver.timeline == ["next", "quiesce", "settle"]


class RebaseStore:
    def __init__(
        self,
        lease: RecoveredChildExecutionLease,
        mailbox_snapshot: tuple[CommandMailboxEntry, ...] = (),
        dispatch_generation: str = "9",
        session_seq: str | None = None,
    ) -> None:
        self.lease = lease
        self.calls = 0
        self.load_calls = 0
        self.state = RecoveredChildControlState(
            dispatch_generation=dispatch_generation,
            session_seq=dispatch_generation if session_seq is None else session_seq,
            mailbox_snapshot=mailbox_snapshot,
        )
        self.refresh_calls = 0

    async def rebase_recovered_child_execution_lease(self, authority, lease):
        assert authority == OwnerAuthority(
            session_id="session-a",
            owner_id="owner-a",
            epoch=4,
        )
        assert lease == self.lease
        self.calls += 1
        self.lease = replace(
            lease,
            resume_generation=lease.resume_generation + 1,
            resume_cut=self.state.dispatch_generation,
            resume_session_seq=self.state.session_seq,
            mailbox_snapshot=self.state.mailbox_snapshot,
        )
        return self.lease

    async def refresh_recovered_child_execution_lease_for_approval(
        self,
        authority,
        *,
        lease,
        state_version,
        approval,
        expected_dispatch_cut,
    ):
        assert authority == OwnerAuthority("session-a", "owner-a", 4)
        assert lease == self.lease
        assert state_version.run_id == lease.child_run_id
        assert approval.command_id == "approve-child-9"
        assert expected_dispatch_cut == lease.resume_cut
        self.refresh_calls += 1
        self.lease = replace(
            lease,
            resume_generation=lease.resume_generation + 1,
            resume_session_seq=self.state.session_seq,
            mailbox_snapshot=self.state.mailbox_snapshot,
        )
        return self.lease

    async def load_recovered_child_control_state(self, authority, lease):
        assert authority == OwnerAuthority(
            session_id="session-a",
            owner_id="owner-a",
            epoch=4,
        )
        assert lease == self.lease
        self.load_calls += 1
        return self.state


class StaleDelegate:
    def __init__(self) -> None:
        self.requests = []

    async def authorize_dispatch(self, request):
        from agentkit.runtime import StaleMailboxCutCommitResult

        self.requests.append(request)
        return StaleMailboxCutCommitResult(
            expected_mailbox_cut=request.mailbox_cut,
            current_mailbox_cut=9,
        )

    async def commit_transition(self, request):
        raise AssertionError(request)

    async def commit_settlement(self, request):
        raise AssertionError(request)

    async def commit_reconciliation(self, request):
        raise AssertionError(request)


class CurrentCutDelegate(StaleDelegate):
    async def authorize_dispatch(self, request):
        if request.mailbox_cut == 9:
            self.requests.append(request)
            return "authorized-at-current-cut"
        return await super().authorize_dispatch(request)


def _dispatch_authorization_request(*, mailbox_cut: int, approved: bool = False):
    from agentkit.runtime import (
        ApprovalSettlement,
        DispatchAuthorizationRequest,
        EffectMutation,
        EffectPlan,
        EffectStatus,
        EngineStepRequest,
        Initial,
        PreparedEffectAction,
        PreparedEffectActionKind,
        TransitionProposal,
    )

    plan = EffectPlan(
        effect_id="child-effect",
        attempt_id="child-attempt",
        effect_kind="tool",
        payload={"tool_call_id": "call", "tool_name": "read"},
        requires_approval=approved,
        approval_request_id="approval-1" if approved else None,
    )
    approval = (
        ApprovalSettlement(
            input_id="approval-1",
            command_id="approve-child-9",
            tool_call_id="call",
            tool_name="read",
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            transition_id="child-transition-1",
            owner_epoch=4,
            approved=True,
        )
        if approved
        else None
    )
    return DispatchAuthorizationRequest(
        session_id="session-a",
        owner_id="owner-a",
        owner_epoch=4,
        mailbox_cut=mailbox_cut,
        engine_request=EngineStepRequest(
            state_version=_state(
                value={
                    "_agentkit_runtime": {
                        "pending_effect_plans": [
                            {
                                "effect_id": plan.effect_id,
                                "attempt_id": plan.attempt_id,
                                "payload": dict(plan.payload),
                            }
                        ]
                    }
                }
            ),
            step_input=Initial(
                input_id="resume",
                command_batch=(),
                mailbox_cut=mailbox_cut,
            ),
        ),
        proposal=TransitionProposal(
            transition_id="child-authorization",
            state_value={},
            next_action=PreparedEffectAction(
                action_kind=PreparedEffectActionKind.DISPATCH,
                effect_plan=plan,
            ),
        ),
        effect_plan=plan,
        effect_mutation=EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.DISPATCHED,
            payload={},
        ),
        approval_settlement=approval,
    )


@pytest.mark.asyncio
async def test_recovered_child_sibling_stale_rebases_lease_before_authorization_unit() -> (
    None
):
    lease = RecoveredChildExecutionLease(
        session_id="session-a",
        child_run_id="child-run",
        lease_id="recovery-lease",
        resume_generation=1,
        resume_cut="6",
        owner_epoch=4,
    )
    store = RebaseStore(lease)
    delegate = StaleDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    request = _dispatch_authorization_request(mailbox_cut=6)

    first_stale = await port.authorize_dispatch(request)
    assert first_stale.current_mailbox_cut == 9
    assert store.calls == 0
    assert store.load_calls == 1

    second_stale = await port.authorize_dispatch(replace(request, mailbox_cut=9))
    guard = port.recovery_guard("child_terminal")

    assert second_stale.current_mailbox_cut == 9
    assert store.calls == 1
    assert port.lease.resume_cut == "9"
    assert port.lease.resume_generation == 2
    assert guard.expected_recovery_cut == "9"
    assert guard.resume_generation == 2


def _targeted_denial_entry(
    *,
    session_seq: str = "9",
) -> CommandMailboxEntry:
    return CommandMailboxEntry(
        command=RuntimeCommand(
            command_id=f"deny-child-{session_seq}",
            command_kind="approval_decision",
            payload={
                "approved": False,
                "request_id": "approval-1",
                "target_run_id": "child-run",
            },
        ),
        admitted_session_seq=session_seq,
        admitted_dispatch_generation=session_seq,
        disposition="admitted",
    )


@pytest.mark.asyncio
async def test_recovery_rebase_targeted_denial_leaves_lease_unchanged() -> None:
    lease = RecoveredChildExecutionLease(
        session_id="session-a",
        child_run_id="child-run",
        lease_id="targeted-denial-lease",
        resume_generation=1,
        resume_cut="6",
        owner_epoch=4,
    )
    denial = _targeted_denial_entry()
    store = RebaseStore(lease, mailbox_snapshot=(denial,))
    delegate = StaleDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    request = _dispatch_authorization_request(mailbox_cut=6)

    first_stale = await port.authorize_dispatch(request)
    blocked_retry = await port.authorize_dispatch(replace(request, mailbox_cut=9))

    assert first_stale.current_mailbox_cut == 9
    assert blocked_retry.current_mailbox_cut == 9
    assert port.control_probe.observe().raised
    assert store.calls == 0
    assert len(delegate.requests) == 1


def _targeted_approval_entry(
    *,
    session_seq: str = "9",
) -> CommandMailboxEntry:
    return CommandMailboxEntry(
        command=RuntimeCommand(
            command_id=f"approve-child-{session_seq}",
            command_kind="approval_decision",
            payload={
                "approved": True,
                "request_id": "approval-1",
                "target_run_id": "child-run",
            },
        ),
        admitted_session_seq=session_seq,
        admitted_dispatch_generation=session_seq,
        disposition="admitted",
    )


@pytest.mark.asyncio
async def test_targeted_stale_approval_safe_yields_without_rebase() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "stale-approval", 1, "6", 4
    )
    store = RebaseStore(lease, mailbox_snapshot=(_targeted_approval_entry(),))
    port = RecoveredChildCommitPort(
        StaleDelegate(),
        store=store,
        owner_id="owner-a",
        lease=lease,
    )

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=6, approved=True)
    )

    assert stale.current_mailbox_cut == 9
    assert port.control_probe.observe().raised is False
    assert store.calls == 0
    assert store.refresh_calls == 0
    assert port.lease == lease


@pytest.mark.asyncio
async def test_recovery_approval_refreshes_snapshot_then_authorizes_at_same_cut() -> (
    None
):
    lease = RecoveredChildExecutionLease(
        session_id="session-a",
        child_run_id="child-run",
        lease_id="refresh-approval",
        resume_generation=1,
        resume_cut="9",
        owner_epoch=4,
        resume_session_seq="6",
    )
    store = RebaseStore(
        lease,
        mailbox_snapshot=(_targeted_approval_entry(),),
        dispatch_generation="9",
        session_seq="12",
    )
    delegate = CurrentCutDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )

    authorized = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=9, approved=True)
    )

    assert authorized == "authorized-at-current-cut"
    assert store.calls == 0
    assert store.refresh_calls == 1
    assert port.lease.resume_cut == "9"
    assert port.lease.resume_generation == 2
    assert port.lease.resume_session_seq == "12"
    assert port.lease.mailbox_snapshot == (_targeted_approval_entry(),)


@pytest.mark.asyncio
async def test_first_recovery_stale_refreshes_probe_without_rebasing_lease() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "first-stale", 1, "6", 4
    )
    store = RebaseStore(lease)
    port = RecoveredChildCommitPort(
        StaleDelegate(),
        store=store,
        owner_id="owner-a",
        lease=lease,
    )

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=6)
    )

    assert stale.current_mailbox_cut == 9
    assert store.calls == 0
    assert port.lease == lease
    assert port.control_probe.observe().generation.value == 9


@pytest.mark.asyncio
async def test_targeted_command_through_stale_cut_refreshes_probe_before_retry() -> (
    None
):
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "targeted-stale", 1, "6", 4
    )
    store = RebaseStore(lease, mailbox_snapshot=(_targeted_approval_entry(),))
    port = RecoveredChildCommitPort(
        StaleDelegate(),
        store=store,
        owner_id="owner-a",
        lease=lease,
    )

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=6)
    )

    assert stale.current_mailbox_cut == 9
    assert store.load_calls == 1
    assert store.calls == 0
    assert port.control_probe.state == store.state
    assert port.lease == lease


@pytest.mark.asyncio
async def test_recovery_retry_rebases_only_after_unraised_refreshed_probe() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "retry-rebase", 1, "6", 4
    )
    store = RebaseStore(lease)
    delegate = StaleDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=6))

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=9)
    )

    assert stale.current_mailbox_cut == 9
    assert store.calls == 1
    assert len(delegate.requests) == 1
    assert port.lease.resume_generation == 2


@pytest.mark.asyncio
async def test_recovery_rebase_newest_below_request_fails_closed() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "below-request", 1, "4", 4
    )
    store = RebaseStore(lease, dispatch_generation="5")
    port = RecoveredChildCommitPort(
        StaleDelegate(),
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=4))

    with pytest.raises(ValueError, match="behind"):
        await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=9))
    assert store.calls == 0


@pytest.mark.asyncio
async def test_recovery_rebase_newest_above_request_returns_stale_without_authorization() -> (
    None
):
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "above-request", 1, "6", 4
    )
    store = RebaseStore(lease, dispatch_generation="12")
    delegate = StaleDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=6))

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=9)
    )

    assert stale.current_mailbox_cut == 12
    assert store.calls == 1
    assert len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_stale_port_publishes_locked_batch_before_sync_probe_observe() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "publish-before-observe", 1, "6", 4
    )
    denial = _targeted_denial_entry()
    store = RebaseStore(lease, mailbox_snapshot=(denial,))
    port = RecoveredChildCommitPort(
        StaleDelegate(),
        store=store,
        owner_id="owner-a",
        lease=lease,
    )

    await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=6))

    assert port.control_probe.observe().raised
    assert port.control_probe.state == store.state


class RacingRebaseStore(RebaseStore):
    async def rebase_recovered_child_execution_lease(self, authority, lease):
        del authority, lease
        self.calls += 1
        self.state = RecoveredChildControlState(
            dispatch_generation="12",
            session_seq="12",
            mailbox_snapshot=(_targeted_denial_entry(session_seq="12"),),
        )
        raise RecoveryLeaseConflictError("target raced with rebase")


@pytest.mark.asyncio
async def test_control_racing_after_probe_publish_cannot_rebase_past_target() -> None:
    lease = RecoveredChildExecutionLease(
        "session-a", "child-run", "racing-control", 1, "6", 4
    )
    store = RacingRebaseStore(lease)
    delegate = StaleDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=store,
        owner_id="owner-a",
        lease=lease,
    )
    await port.authorize_dispatch(_dispatch_authorization_request(mailbox_cut=6))

    stale = await port.authorize_dispatch(
        _dispatch_authorization_request(mailbox_cut=9)
    )

    assert stale.current_mailbox_cut == 12
    assert port.lease == lease
    assert port.control_probe.observe().raised
    assert len(delegate.requests) == 1
    assert store.calls == 1


class GuardRecordingDelegate(StaleDelegate):
    def __init__(self) -> None:
        super().__init__()
        self.guards = []

    async def commit_transition_with_recovery_guard(self, request, guard):
        self.requests.append(request)
        self.guards.append(guard)
        return "guarded"


@pytest.mark.asyncio
async def test_recovery_terminal_commit_injects_active_guard() -> None:
    from agentkit.runtime import (
        CommitTransitionRequest,
        EngineStepRequest,
        Initial,
        TerminalAction,
        TransitionProposal,
    )

    lease = RecoveredChildExecutionLease(
        session_id="session-a",
        child_run_id="child-run",
        lease_id="terminal-lease",
        resume_generation=3,
        resume_cut="11",
        owner_epoch=4,
    )
    delegate = GuardRecordingDelegate()
    port = RecoveredChildCommitPort(
        delegate,
        store=RebaseStore(lease),
        owner_id="owner-a",
        lease=lease,
    )
    request = CommitTransitionRequest(
        session_id="session-a",
        owner_id="owner-a",
        owner_epoch=4,
        engine_request=EngineStepRequest(
            state_version=_state(),
            step_input=Initial(
                input_id="terminal",
                command_batch=(),
                mailbox_cut=11,
            ),
        ),
        proposal=TransitionProposal(
            transition_id="child-terminal",
            state_value={"status": "completed"},
            next_action=TerminalAction(
                final_message="done",
                stop_reason="completed",
            ),
        ),
    )

    result = await port.commit_transition(request)

    assert result == "guarded"
    assert delegate.guards == [port.recovery_guard("child_terminal")]


def test_approval_resolved_input_id_uses_child_run_and_command() -> None:
    binding = _binding()
    assert approval_resolved_input_id(binding.child_run_id, "deny-command") == (
        f"{binding.child_run_id}:approval:deny-command"
    )


@pytest.mark.asyncio
async def test_child_cancel_cleanup_survives_repeated_task_cancellation() -> None:
    binding = _binding()

    class GatedDriver(Driver):
        def __init__(self) -> None:
            super().__init__(
                [
                    SafeYieldOutcome(
                        state_version=_state(),
                        reason="targeted cancel command",
                        steps_taken=1,
                    )
                ]
            )
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def cancel_and_quiesce(self, binding):
            del binding
            self.timeline.append("quiesce-start")
            self.started.set()
            await self.release.wait()
            self.timeline.append("quiesce")

    driver = GatedDriver()
    backend = OwnerLocalChildEffectBackend(
        BindingStore(binding),
        driver=driver,
        controls=Controls(
            [
                ChildControlBatch(
                    generation=7,
                    command_id="cancel-child",
                    kind="cancel",
                )
            ]
        ),
    )
    execution = asyncio.create_task(
        backend.execute(_invocation(binding), Cancellation())
    )
    await driver.started.wait()
    execution.cancel()
    execution.cancel()
    driver.release.set()

    with pytest.raises(asyncio.CancelledError):
        await execution
    assert driver.timeline == ["next", "quiesce-start", "quiesce", "settle"]
