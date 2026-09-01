"""Host-private owner-local child execution and recovery binding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from agentkit.runtime.contracts import (
    ApprovalSettlement,
    BlockedOutcome,
    CancelledOutcome,
    CancellationToken,
    ControlGeneration,
    ControlSnapshot,
    CommitReconciliationRequest,
    CommitReconciliationResult,
    CommitSettlementRequest,
    CommitSettlementResult,
    CommitTransitionRequest,
    CommitTransitionResult,
    CompletedOutcome,
    DispatchAuthorizationRequest,
    DispatchAuthorizationResult,
    EffectCompletedResult,
    EffectExecutionResult,
    EffectFailedResult,
    FailedOutcome,
    FailureReport,
    RoundLimitOutcome,
    SafeYieldOutcome,
    OperationStateVersion,
    StaleMailboxCutCommitResult,
    TerminalAction,
)
from coding_agent.executors.durable import (
    DurableEffectInvocation,
    _await_task_through_cancellation,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import (
    ChildExecutionBinding,
    RecoveredChildExecutionLease,
    RecoveredChildControlState,
    RecoveryGuardKind,
    RecoveryLeaseConflictError,
    RecoveryTransitionGuard,
    parse_u64,
    runtime_command_invalidates_dispatch,
)


@dataclass(frozen=True, slots=True)
class ChildControlBatch:
    """One already-admitted targeted command at a durable generation."""

    generation: int
    command_id: str
    kind: Literal["approval", "cancel", "interrupt"]
    approved: bool | None = None
    reason_code: str | None = None
    reason_message: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if not self.command_id:
            raise ValueError("command_id must be non-empty")
        if self.kind == "approval" and self.approved is None:
            raise ValueError("approval control requires an approved decision")
        if self.kind != "approval" and self.approved is not None:
            raise ValueError("cancel and interrupt controls cannot approve")


class ChildBindingStore(Protocol):
    async def load_child_execution_binding(
        self,
        session_id: str,
        *,
        parent_effect_id: str | None = None,
        child_run_id: str | None = None,
    ) -> ChildExecutionBinding | None: ...


class OwnerLocalChildDriver(Protocol):
    async def next_outcome(
        self,
        binding: ChildExecutionBinding,
        cancellation: CancellationToken,
    ) -> Any: ...

    async def apply_approval(
        self,
        binding: ChildExecutionBinding,
        batch: ChildControlBatch,
    ) -> None: ...

    async def cancel_and_quiesce(
        self,
        binding: ChildExecutionBinding,
    ) -> None: ...

    async def settle_claimed_permits(
        self,
        binding: ChildExecutionBinding,
    ) -> None: ...


class ChildControlPort(Protocol):
    async def observe(
        self,
        binding: ChildExecutionBinding,
        *,
        after_generation: int,
    ) -> ChildControlBatch | None: ...

    async def wait(
        self,
        binding: ChildExecutionBinding,
        *,
        after_generation: int,
    ) -> ChildControlBatch: ...


class OwnerLocalChildEffectBackend:
    """Keep the parent effect active until its deterministic child is terminal."""

    __slots__ = ("_bindings", "_controls", "_driver")

    def __init__(
        self,
        bindings: ChildBindingStore,
        *,
        driver: OwnerLocalChildDriver,
        controls: ChildControlPort,
    ) -> None:
        self._bindings = bindings
        self._driver = driver
        self._controls = controls

    async def execute(
        self,
        invocation: DurableEffectInvocation,
        cancellation: CancellationToken,
    ) -> EffectExecutionResult:
        binding = await self._bindings.load_child_execution_binding(
            invocation.session_id,
            parent_effect_id=invocation.effect_id,
        )
        if binding is None:
            return _child_failure(
                "child_binding_missing",
                "durable child binding does not exist",
            )
        if (
            binding.parent_attempt_id != invocation.attempt_id
            or binding.authorization_transition_id
            != invocation.authorization_transition_id
        ):
            return _child_failure(
                "child_binding_mismatch",
                "durable child binding does not match parent dispatch",
            )
        tool_name = invocation.payload.get("tool_name")
        if tool_name != "subagent":
            return _child_failure(
                "invalid_child_effect",
                "owner-local child backend requires the subagent tool",
            )

        observed_generation = 0
        cleaned = False
        try:
            while True:
                if cancellation.cancelled:
                    raise asyncio.CancelledError
                outcome = await self._driver.next_outcome(binding, cancellation)
                if isinstance(outcome, CompletedOutcome):
                    return EffectCompletedResult(
                        result={
                            "content": outcome.final_message,
                            "child_run_id": binding.child_run_id,
                        }
                    )
                if isinstance(outcome, FailedOutcome):
                    return _child_failure(
                        "child_failed",
                        outcome.error.message,
                    )
                if isinstance(outcome, CancelledOutcome):
                    return _child_failure(
                        "child_cancelled",
                        "child execution was cancelled",
                    )
                if isinstance(outcome, RoundLimitOutcome):
                    return _child_failure(
                        "child_round_limit",
                        "child reached its configured round limit",
                    )
                if not isinstance(outcome, (BlockedOutcome, SafeYieldOutcome)):
                    return _child_failure(
                        "invalid_child_outcome",
                        "child driver returned a nonterminal unsupported outcome",
                    )

                batch = await self._controls.observe(
                    binding,
                    after_generation=observed_generation,
                )
                if batch is None:
                    batch = await self._controls.wait(
                        binding,
                        after_generation=observed_generation,
                    )
                observed_generation = batch.generation
                if batch.kind in {"cancel", "interrupt"}:
                    cleaned = True
                    cancelled = await self._quiesce_before_exit(binding)
                    if cancelled:
                        raise asyncio.CancelledError
                    return _child_failure(
                        "child_cancelled",
                        "child execution was cancelled by targeted control",
                    )
                await self._driver.apply_approval(binding, batch)
        except asyncio.CancelledError:
            if not cleaned:
                await self._quiesce_before_exit(binding)
            raise

    async def _quiesce_before_exit(
        self,
        binding: ChildExecutionBinding,
    ) -> bool:
        cleanup_task = asyncio.create_task(self._cleanup_child(binding))
        _, caller_cancelled = await _await_task_through_cancellation(cleanup_task)
        return caller_cancelled

    async def _cleanup_child(self, binding: ChildExecutionBinding) -> None:
        await self._driver.cancel_and_quiesce(binding)
        await self._driver.settle_claimed_permits(binding)


class TargetAwareChildControlProbe:
    """Synchronous view of the latest store-locked child control batch."""

    __slots__ = ("_changed", "_snapshot", "_state")

    def __init__(self, *, initial_generation: int = 0) -> None:
        self._changed = asyncio.Event()
        self._state: RecoveredChildControlState | None = None
        self._snapshot = ControlSnapshot(
            generation=ControlGeneration(initial_generation),
            raised=False,
        )

    @property
    def state(self) -> RecoveredChildControlState | None:
        return self._state

    def publish(self, state: RecoveredChildControlState) -> None:
        raised_entry = next(
            (
                entry
                for entry in state.mailbox_snapshot
                if runtime_command_invalidates_dispatch(entry.command)
            ),
            None,
        )
        reason = (
            None
            if raised_entry is None
            else f"targeted_{raised_entry.command.command_kind}"
        )
        self._state = state
        self._snapshot = ControlSnapshot(
            generation=ControlGeneration(
                parse_u64(
                    state.dispatch_generation,
                    field_name="dispatch_generation",
                )
            ),
            raised=raised_entry is not None,
            reason=reason,
        )
        self._changed.set()

    def observe(self) -> ControlSnapshot:
        return self._snapshot

    async def wait(self, after: ControlGeneration) -> ControlSnapshot:
        while self._snapshot.generation <= after:
            changed = self._changed
            await changed.wait()
            if changed is self._changed:
                self._changed = asyncio.Event()
        return self._snapshot


class RecoveredChildLeaseStore(Protocol):
    async def rebase_recovered_child_execution_lease(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
    ) -> RecoveredChildExecutionLease: ...

    async def refresh_recovered_child_execution_lease_for_approval(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
        state_version: OperationStateVersion,
        approval: ApprovalSettlement,
        expected_dispatch_cut: str,
    ) -> RecoveredChildExecutionLease: ...

    async def load_recovered_child_control_state(
        self,
        authority: OwnerAuthority,
        *,
        lease: RecoveredChildExecutionLease,
    ) -> RecoveredChildControlState: ...


class ChildCommitPortDelegate(Protocol):
    async def commit_transition(
        self,
        request: CommitTransitionRequest,
    ) -> CommitTransitionResult: ...

    async def commit_transition_with_recovery_guard(
        self,
        request: CommitTransitionRequest,
        guard: RecoveryTransitionGuard,
    ) -> CommitTransitionResult: ...

    async def authorize_dispatch(
        self,
        request: DispatchAuthorizationRequest,
    ) -> DispatchAuthorizationResult: ...

    async def commit_settlement(
        self,
        request: CommitSettlementRequest,
    ) -> CommitSettlementResult: ...

    async def commit_settlement_with_recovery_guard(
        self,
        request: CommitSettlementRequest,
        guard: RecoveryTransitionGuard,
    ) -> CommitSettlementResult: ...

    async def commit_reconciliation(
        self,
        request: CommitReconciliationRequest,
    ) -> CommitReconciliationResult: ...


class RecoveredChildCommitPort:
    """Bind coordinator stale-cut retries to one durable recovery lease."""

    __slots__ = (
        "_delegate",
        "_latest_control_state",
        "_lease",
        "_owner_id",
        "_probe",
        "_store",
    )

    def __init__(
        self,
        delegate: ChildCommitPortDelegate,
        *,
        store: RecoveredChildLeaseStore,
        owner_id: str,
        lease: RecoveredChildExecutionLease,
        probe: TargetAwareChildControlProbe | None = None,
    ) -> None:
        if not owner_id:
            raise ValueError("owner_id must be non-empty")
        self._delegate = delegate
        self._store = store
        self._owner_id = owner_id
        self._lease = lease
        self._probe = probe or TargetAwareChildControlProbe(
            initial_generation=parse_u64(
                lease.resume_cut,
                field_name="resume_cut",
            )
        )
        self._latest_control_state: RecoveredChildControlState | None = None

    @property
    def lease(self) -> RecoveredChildExecutionLease:
        return self._lease

    @property
    def control_probe(self) -> TargetAwareChildControlProbe:
        return self._probe

    async def commit_transition(
        self,
        request: CommitTransitionRequest,
    ) -> CommitTransitionResult:
        if isinstance(request.proposal.next_action, TerminalAction):
            return await self._delegate.commit_transition_with_recovery_guard(
                request,
                self.recovery_guard(RecoveryGuardKind.CHILD_TERMINAL),
            )
        return await self._delegate.commit_transition(request)

    async def authorize_dispatch(
        self,
        request: DispatchAuthorizationRequest,
    ) -> DispatchAuthorizationResult:
        self._assert_request_authority(request)
        authority = OwnerAuthority(
            session_id=request.session_id,
            owner_id=self._owner_id,
            epoch=request.owner_epoch,
        )
        request_cut = request.mailbox_cut
        lease_cut = parse_u64(
            self._lease.resume_cut,
            field_name="resume_cut",
        )
        cached = self._latest_control_state
        if request.approval_settlement is not None:
            latest = await self._store.load_recovered_child_control_state(
                authority,
                lease=self._lease,
            )
            self._probe.publish(latest)
            self._latest_control_state = latest
        else:
            latest = cached
        if latest is not None:
            latest_cut = parse_u64(
                latest.dispatch_generation,
                field_name="dispatch_generation",
            )
            if latest_cut < request_cut:
                raise ValueError(
                    "recovery mailbox cut moved behind the coordinator request"
                )
            if (
                request.approval_settlement is not None
                and cached is None
                and latest_cut != lease_cut
            ):
                return StaleMailboxCutCommitResult(
                    expected_mailbox_cut=lease_cut,
                    current_mailbox_cut=latest_cut,
                )
            latest_session_seq = parse_u64(
                latest.session_seq,
                field_name="session_seq",
            )
            lease_session_seq = parse_u64(
                self._lease.resume_session_seq,
                field_name="resume_session_seq",
            )
            if (
                request.approval_settlement is not None
                and latest_cut == lease_cut
                and latest_session_seq > lease_session_seq
            ):
                self._lease = await self._store.refresh_recovered_child_execution_lease_for_approval(
                    authority,
                    lease=self._lease,
                    state_version=request.engine_request.state_version,
                    approval=request.approval_settlement,
                    expected_dispatch_cut=self._lease.resume_cut,
                )
                latest = await self._store.load_recovered_child_control_state(
                    authority,
                    lease=self._lease,
                )
                self._probe.publish(latest)
                self._latest_control_state = latest
                lease_cut = parse_u64(
                    self._lease.resume_cut,
                    field_name="resume_cut",
                )
                latest_cut = parse_u64(
                    latest.dispatch_generation,
                    field_name="dispatch_generation",
                )
            if latest_cut > lease_cut:
                if self._probe.observe().raised:
                    return StaleMailboxCutCommitResult(
                        expected_mailbox_cut=lease_cut,
                        current_mailbox_cut=latest_cut,
                    )
                try:
                    self._lease = (
                        await self._store.rebase_recovered_child_execution_lease(
                            authority,
                            lease=self._lease,
                        )
                    )
                except RecoveryLeaseConflictError:
                    raced = await self._store.load_recovered_child_control_state(
                        authority,
                        lease=self._lease,
                    )
                    self._probe.publish(raced)
                    self._latest_control_state = raced
                    return StaleMailboxCutCommitResult(
                        expected_mailbox_cut=lease_cut,
                        current_mailbox_cut=parse_u64(
                            raced.dispatch_generation,
                            field_name="dispatch_generation",
                        ),
                    )
                refreshed = await self._store.load_recovered_child_control_state(
                    authority,
                    lease=self._lease,
                )
                self._probe.publish(refreshed)
                self._latest_control_state = refreshed
                return StaleMailboxCutCommitResult(
                    expected_mailbox_cut=lease_cut,
                    current_mailbox_cut=parse_u64(
                        self._lease.resume_cut,
                        field_name="resume_cut",
                    ),
                )
        if request_cut != lease_cut:
            raise ValueError("authorization cut does not match active recovery lease")
        result = await self._delegate.authorize_dispatch(request)
        if not isinstance(result, StaleMailboxCutCommitResult):
            return result
        refreshed = await self._store.load_recovered_child_control_state(
            authority,
            lease=self._lease,
        )
        self._probe.publish(refreshed)
        self._latest_control_state = refreshed
        return replace(
            result,
            current_mailbox_cut=parse_u64(
                refreshed.dispatch_generation,
                field_name="dispatch_generation",
            ),
        )

    async def commit_settlement(
        self,
        request: CommitSettlementRequest,
    ) -> CommitSettlementResult:
        if isinstance(request.proposal.next_action, TerminalAction):
            return await self._delegate.commit_settlement_with_recovery_guard(
                request,
                self.recovery_guard(RecoveryGuardKind.CHILD_TERMINAL),
            )
        return await self._delegate.commit_settlement(request)

    async def commit_reconciliation(
        self,
        request: CommitReconciliationRequest,
    ) -> CommitReconciliationResult:
        return await self._delegate.commit_reconciliation(request)

    async def commit_parent_settlement(
        self,
        request: CommitSettlementRequest,
    ) -> CommitSettlementResult:
        return await self._delegate.commit_settlement_with_recovery_guard(
            request,
            self.recovery_guard(RecoveryGuardKind.PARENT_SETTLEMENT),
        )

    def recovery_guard(
        self,
        kind: RecoveryGuardKind | str,
    ) -> RecoveryTransitionGuard:
        guard_kind = (
            kind if isinstance(kind, RecoveryGuardKind) else RecoveryGuardKind(kind)
        )
        return RecoveryTransitionGuard(
            lease_id=self._lease.lease_id,
            child_run_id=self._lease.child_run_id,
            resume_generation=self._lease.resume_generation,
            expected_recovery_cut=self._lease.resume_cut,
            kind=guard_kind,
        )

    def _assert_request_authority(
        self,
        request: DispatchAuthorizationRequest,
    ) -> None:
        if (
            request.session_id != self._lease.session_id
            or request.owner_id != self._owner_id
            or request.owner_epoch != self._lease.owner_epoch
        ):
            raise ValueError("authorization request does not match recovery lease")


def approval_resolved_input_id(child_run_id: str, command_id: str) -> str:
    if not child_run_id or not command_id:
        raise ValueError("child_run_id and command_id must be non-empty")
    return f"{child_run_id}:approval:{command_id}"


def child_execution_binding(
    *,
    session_id: str,
    parent_run_id: str,
    parent_effect_id: str,
    parent_attempt_id: str,
    prepared_transition_id: str,
) -> ChildExecutionBinding:
    """Build the deterministic durable identity for one parent subagent plan."""

    child_run_id = (
        f"{session_id}:{parent_run_id}:child:{parent_effect_id}:{parent_attempt_id}"
    )
    authorization_transition_id = (
        f"{prepared_transition_id}:dispatch:{parent_effect_id}:{parent_attempt_id}"
    )
    settlement_input_id = (
        f"{authorization_transition_id}:settlement:{parent_attempt_id}"
    )
    return ChildExecutionBinding(
        session_id=session_id,
        parent_run_id=parent_run_id,
        parent_effect_id=parent_effect_id,
        parent_attempt_id=parent_attempt_id,
        child_run_id=child_run_id,
        authorization_transition_id=authorization_transition_id,
        live_parent_settlement_transition_id=(
            f"{parent_run_id}:transition:EffectSettled:{settlement_input_id}"
        ),
    )


def _child_failure(code: str, message: str) -> EffectFailedResult:
    return EffectFailedResult(
        error=FailureReport(
            code=code,
            message=message,
        )
    )
