"""Port-driven AgentKit segment coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from collections.abc import Mapping
from contextlib import suppress

from . import contracts as runtime_contracts
from .contracts import (
    ApprovalResolved,
    ApprovalSettlement,
    BlockedAction,
    BlockedOutcome,
    CASConflictCommitResult,
    CommitPort,
    CommitReconciliationRequest,
    CommitSettlementRequest,
    CommitTransitionRequest,
    CommittedCommitResult,
    CommittedFactNotice,
    CommittedFactSink,
    CompletedOutcome,
    ControlProbe,
    ControlSnapshot,
    DeliveryFailure,
    DispatchAuthorizationRequest,
    DispatchAuthorizedResult,
    DispatchPermit,
    EffectCompletedResult,
    EffectExecutionResult,
    EffectExecutor,
    EffectFailedResult,
    EffectIndeterminateResult,
    EffectMutation,
    EffectPlan,
    EffectReference,
    EffectSettled,
    EffectSettlement,
    EffectSettlementOutcome,
    EffectStatus,
    EngineStepRequest,
    ExactReplayCommitResult,
    FailedOutcome,
    FailureReport,
    FrameSink,
    Initial,
    InvalidTransitionCommitResult,
    ModelAdapter,
    ModelGenerationAction,
    ModelGenerationCompleted,
    OperationStateVersion,
    PreparedEffectAction,
    PreparedEffectActionKind,
    ReconciliationRecord,
    RoundLimitOutcome,
    RunSegmentRequest,
    SafeYieldAction,
    SafeYieldOutcome,
    SegmentOutcome,
    StaleMailboxCutCommitResult,
    StaleOwnerCommitResult,
    StorageFailureCommitResult,
    TerminalAction,
    TransitionProposal,
)


class _CancellationSignal:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def cancel(self) -> None:
        self._event.set()


class _GuardedFrameSink:
    __slots__ = ("_enabled", "_failures", "_sink")

    def __init__(self, sink: FrameSink, failures: list[DeliveryFailure]) -> None:
        self._sink = sink
        self._failures = failures
        self._enabled = True

    async def emit(self, frame) -> None:
        if not self._enabled:
            return
        try:
            await self._sink.emit(frame)
        except Exception as exc:
            self._enabled = False
            self._failures.append(
                DeliveryFailure(sink="frame", message=_exception_message(exc))
            )


class _GuardedCommittedFactSink:
    __slots__ = ("_enabled", "_failures", "_sink")

    def __init__(
        self,
        sink: CommittedFactSink,
        failures: list[DeliveryFailure],
    ) -> None:
        self._sink = sink
        self._failures = failures
        self._enabled = True

    async def emit_all(self, notices: tuple[CommittedFactNotice, ...]) -> None:
        if not self._enabled:
            return
        for notice in notices:
            try:
                await self._sink.emit(notice)
            except Exception as exc:
                self._enabled = False
                self._failures.append(
                    DeliveryFailure(
                        sink="committed_fact",
                        message=_exception_message(exc),
                    )
                )
                return


class SegmentCoordinator:
    """Own the engine/commit/action/re-entry loop for one runtime segment."""

    __slots__ = (
        "_commit_port",
        "_delivery_failures",
        "_effect_executor",
        "_engine",
        "_model_adapter",
    )

    def __init__(
        self,
        *,
        engine,
        model_adapter: ModelAdapter,
        commit_port: CommitPort,
        effect_executor: EffectExecutor,
    ) -> None:
        self._engine = engine
        self._model_adapter = model_adapter
        self._commit_port = commit_port
        self._effect_executor = effect_executor
        self._delivery_failures: list[DeliveryFailure] = []

    @property
    def delivery_failures(self) -> tuple[DeliveryFailure, ...]:
        return tuple(self._delivery_failures)

    async def run(
        self,
        request: RunSegmentRequest,
        control_probe: ControlProbe,
        frame_sink: FrameSink,
        committed_fact_sink: CommittedFactSink,
    ) -> SegmentOutcome:
        if not isinstance(request, RunSegmentRequest):
            raise TypeError("request must be a RunSegmentRequest")
        self._delivery_failures.clear()
        guarded_frames = _GuardedFrameSink(frame_sink, self._delivery_failures)
        guarded_facts = _GuardedCommittedFactSink(
            committed_fact_sink,
            self._delivery_failures,
        )
        current_state = request.state_version
        step_input = request.step_input
        steps_taken = 0
        mailbox_cut = _mailbox_cut(request)
        propagate_task_cancellation = False

        try:
            while True:
                if not isinstance(step_input, (EffectSettled, ApprovalResolved)):
                    snapshot = control_probe.observe()
                    if snapshot.raised:
                        return _safe_yield(current_state, snapshot, steps_taken)

                if isinstance(step_input, (EffectSettled, ApprovalResolved)):
                    runtime_contracts._validate_settlement_binding(
                        current_state,
                        _settlement(step_input),
                        owner_epoch=request.owner_epoch,
                    )
                engine_request = EngineStepRequest(
                    state_version=current_state,
                    step_input=step_input,
                )
                proposal = self._engine.propose(engine_request)
                approval_for_authorization = _approved_settlement(step_input)
                defer_commit_to_authorization = (
                    approval_for_authorization is not None
                    and isinstance(proposal.next_action, PreparedEffectAction)
                    and proposal.next_action.action_kind
                    is PreparedEffectActionKind.DISPATCH
                )

                if not defer_commit_to_authorization:
                    if not isinstance(step_input, (EffectSettled, ApprovalResolved)):
                        snapshot = control_probe.observe()
                        if snapshot.raised:
                            return _safe_yield(current_state, snapshot, steps_taken)

                    if isinstance(step_input, (EffectSettled, ApprovalResolved)):
                        settlement = _settlement(step_input)
                        if _is_post_reconciliation_settlement(
                            current_state,
                            settlement,
                        ):
                            result = await self._commit_port.commit_transition(
                                CommitTransitionRequest(
                                    session_id=request.session_id,
                                    owner_id=request.owner_id,
                                    owner_epoch=request.owner_epoch,
                                    engine_request=engine_request,
                                    proposal=proposal,
                                )
                            )
                        else:
                            result = await self._commit_port.commit_settlement(
                                CommitSettlementRequest(
                                    session_id=request.session_id,
                                    owner_id=request.owner_id,
                                    owner_epoch=request.owner_epoch,
                                    engine_request=engine_request,
                                    proposal=proposal,
                                    settlement=settlement,
                                    effect_mutation=_settlement_mutation(settlement),
                                )
                            )
                    else:
                        result = await self._commit_port.commit_transition(
                            CommitTransitionRequest(
                                session_id=request.session_id,
                                owner_id=request.owner_id,
                                owner_epoch=request.owner_epoch,
                                engine_request=engine_request,
                                proposal=proposal,
                            )
                        )
                    if isinstance(result, CASConflictCommitResult):
                        current_state = result.current_state
                        continue
                    committed = _committed_result(result)
                    if isinstance(committed, FailedOutcome):
                        return _with_steps(
                            committed,
                            steps_taken,
                            fallback_state=current_state,
                        )
                    current_state = committed.state_version
                    await guarded_facts.emit_all(committed.notices)
                    if propagate_task_cancellation and isinstance(
                        step_input,
                        EffectSettled,
                    ):
                        raise asyncio.CancelledError

                    if isinstance(step_input, (EffectSettled, ApprovalResolved)):
                        snapshot = control_probe.observe()
                        if snapshot.raised:
                            return _safe_yield(current_state, snapshot, steps_taken)

                action = proposal.next_action
                if isinstance(action, TerminalAction):
                    return CompletedOutcome(
                        state_version=current_state,
                        final_message=action.final_message,
                        steps_taken=steps_taken,
                        stop_reason=action.stop_reason,
                    )
                if isinstance(action, BlockedAction):
                    return BlockedOutcome(
                        state_version=current_state,
                        reason=action.reason,
                        effect=action.effect,
                        steps_taken=steps_taken,
                    )
                if isinstance(action, SafeYieldAction):
                    return SafeYieldOutcome(
                        state_version=current_state,
                        reason=action.reason,
                        steps_taken=steps_taken,
                    )
                if isinstance(action, ModelGenerationAction):
                    if steps_taken >= request.max_rounds:
                        return RoundLimitOutcome(
                            state_version=current_state,
                            steps_taken=steps_taken,
                        )
                    snapshot = control_probe.observe()
                    if snapshot.raised:
                        return _safe_yield(current_state, snapshot, steps_taken)
                    model_result, interruption = await self._generate(
                        action,
                        control_probe=control_probe,
                        snapshot=snapshot,
                        frame_sink=guarded_frames,
                    )
                    if interruption is not None:
                        return SafeYieldOutcome(
                            state_version=current_state,
                            reason=interruption,
                            steps_taken=steps_taken,
                        )
                    if model_result is None:
                        raise RuntimeError("model adapter returned no result")
                    if model_result.request_id != action.request.request_id:
                        return FailedOutcome(
                            state_version=current_state,
                            error=FailureReport(
                                code="model_request_id_mismatch",
                                message=(
                                    "model result request_id does not match the "
                                    "active model request"
                                ),
                                details={
                                    "expected_request_id": action.request.request_id,
                                    "actual_request_id": model_result.request_id,
                                },
                            ),
                            steps_taken=steps_taken,
                        )
                    steps_taken += 1
                    step_input = ModelGenerationCompleted(result=model_result)
                    continue
                if isinstance(action, PreparedEffectAction):
                    if action.action_kind is PreparedEffectActionKind.APPROVAL_WAIT:
                        return BlockedOutcome(
                            state_version=current_state,
                            reason="approval_required",
                            effect=_effect_reference(action.effect_plan),
                            steps_taken=steps_taken,
                        )

                    snapshot = control_probe.observe()
                    if snapshot.raised:
                        return _safe_yield(current_state, snapshot, steps_taken)
                    authorization_engine_request = EngineStepRequest(
                        state_version=current_state,
                        step_input=step_input,
                    )
                    authorization_proposal = _authorization_proposal(
                        proposal,
                        current_state=current_state,
                        preserve_pending=defer_commit_to_authorization,
                    )
                    authorization_proposal = _with_active_effect_authorization(
                        authorization_proposal,
                        owner_epoch=request.owner_epoch,
                    )
                    authorization_request = DispatchAuthorizationRequest(
                        session_id=request.session_id,
                        owner_id=request.owner_id,
                        owner_epoch=request.owner_epoch,
                        mailbox_cut=mailbox_cut,
                        engine_request=authorization_engine_request,
                        proposal=authorization_proposal,
                        effect_plan=action.effect_plan,
                        effect_mutation=EffectMutation(
                            effect_id=action.effect_plan.effect_id,
                            attempt_id=action.effect_plan.attempt_id,
                            expected_status=EffectStatus.PREPARED,
                            status=EffectStatus.DISPATCHED,
                            payload={
                                "authorization_transition_id": (
                                    authorization_proposal.transition_id
                                ),
                                "owner_epoch": request.owner_epoch,
                            },
                        ),
                        approval_settlement=approval_for_authorization,
                    )
                    while True:
                        authorization_result = (
                            await self._commit_port.authorize_dispatch(
                                authorization_request
                            )
                        )
                        if not isinstance(
                            authorization_result,
                            StaleMailboxCutCommitResult,
                        ):
                            break
                        snapshot = control_probe.observe()
                        if snapshot.raised:
                            return _safe_yield(
                                current_state,
                                snapshot,
                                steps_taken,
                            )
                        mailbox_cut = max(
                            authorization_result.current_mailbox_cut,
                            snapshot.generation.value,
                        )
                        authorization_request = replace(
                            authorization_request,
                            mailbox_cut=mailbox_cut,
                        )
                    authorized = _authorized_result(authorization_result)
                    if isinstance(authorized, FailedOutcome):
                        return _with_steps(
                            authorized,
                            steps_taken,
                            fallback_state=current_state,
                        )
                    current_state = authorized.state_version
                    _validate_dispatch_permit(
                        authorized.permit,
                        session_id=request.session_id,
                        owner_epoch=request.owner_epoch,
                        plan=action.effect_plan,
                        authorization_transition_id=(
                            authorization_proposal.transition_id
                        ),
                    )
                    await guarded_facts.emit_all(authorized.notices)

                    snapshot = control_probe.observe()
                    cancellation = _CancellationSignal()
                    if snapshot.raised:
                        if snapshot.reason is None:
                            raise ValueError(
                                "raised control snapshot requires a reason"
                            )
                        execution_result: EffectExecutionResult = (
                            EffectIndeterminateResult(
                                reason_code="control_after_dispatch",
                                message=snapshot.reason,
                            )
                        )
                    else:
                        authorized.permit.claim()
                        try:
                            execution_result = await self._effect_executor.execute(
                                authorized.permit,
                                cancellation,
                            )
                        except asyncio.CancelledError:
                            propagate_task_cancellation = True
                            execution_result = EffectIndeterminateResult(
                                reason_code="effect_executor_cancelled",
                                message="effect executor cancelled after dispatch",
                            )
                        except Exception as exc:
                            execution_result = EffectIndeterminateResult(
                                reason_code="effect_executor_error",
                                message=_exception_message(exc),
                            )
                    step_input = EffectSettled(
                        settlement=_effect_settlement(
                            plan=action.effect_plan,
                            authorization_transition_id=(
                                authorized.permit.authorization_transition_id
                            ),
                            owner_epoch=request.owner_epoch,
                            result=execution_result,
                        )
                    )
                    continue

                raise TypeError("unsupported next action")
        except Exception as exc:
            return FailedOutcome(
                state_version=current_state,
                error=FailureReport(
                    code="segment_coordinator_error",
                    message=_exception_message(exc),
                ),
                steps_taken=steps_taken,
            )

    async def _generate(
        self,
        action: ModelGenerationAction,
        *,
        control_probe: ControlProbe,
        snapshot: ControlSnapshot,
        frame_sink: FrameSink,
    ):
        cancellation = _CancellationSignal()
        generate_task = asyncio.create_task(
            self._model_adapter.generate(
                action.request,
                frame_sink,
                cancellation,
            )
        )
        wait_task = asyncio.create_task(control_probe.wait(snapshot.generation))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {generate_task, wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_task in done:
                    control = wait_task.result()
                    if control.raised:
                        cancellation.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await generate_task
                        return None, control.reason
                    wait_task = asyncio.create_task(
                        control_probe.wait(control.generation)
                    )
                    continue
                return generate_task.result(), None
        finally:
            if not wait_task.done():
                wait_task.cancel()
                with suppress(asyncio.CancelledError):
                    await wait_task
            if not generate_task.done():
                generate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await generate_task


async def _reconcile_and_run_segment(
    coordinator: SegmentCoordinator,
    *,
    session_id: str,
    owner_id: str,
    owner_epoch: int,
    state_version: OperationStateVersion,
    record: ReconciliationRecord,
    max_rounds: int,
    control_probe: ControlProbe,
    frame_sink: FrameSink,
    committed_fact_sink: CommittedFactSink,
) -> SegmentOutcome:
    current_state = state_version
    while True:
        try:
            reconciliation_request = CommitReconciliationRequest(
                session_id=session_id,
                owner_id=owner_id,
                owner_epoch=owner_epoch,
                state_version=current_state,
                record=record,
            )
        except (TypeError, ValueError) as exc:
            return FailedOutcome(
                state_version=current_state,
                error=FailureReport(
                    code="invalid_reconciliation_state",
                    message=_exception_message(exc),
                ),
                steps_taken=0,
            )
        result = await coordinator._commit_port.commit_reconciliation(
            reconciliation_request
        )
        if isinstance(result, CASConflictCommitResult):
            current_state = result.current_state
            continue
        committed = _committed_result(result)
        if isinstance(committed, FailedOutcome):
            return committed
        current_state = committed.state_version
        emitter = _GuardedCommittedFactSink(
            committed_fact_sink,
            coordinator._delivery_failures,
        )
        await emitter.emit_all(committed.notices)
        break

    try:
        step_input = _reconciled_effect_input(
            current_state,
            record=record,
            owner_epoch=owner_epoch,
        )
        request = RunSegmentRequest(
            session_id=session_id,
            owner_id=owner_id,
            owner_epoch=owner_epoch,
            state_version=current_state,
            step_input=step_input,
            max_rounds=max_rounds,
        )
    except (TypeError, ValueError) as exc:
        return FailedOutcome(
            state_version=current_state,
            error=FailureReport(
                code="invalid_reconciled_effect",
                message=_exception_message(exc),
            ),
            steps_taken=0,
        )
    return await coordinator.run(
        request,
        control_probe=control_probe,
        frame_sink=frame_sink,
        committed_fact_sink=committed_fact_sink,
    )


def _reconciled_effect_input(
    state_version: OperationStateVersion,
    *,
    record: ReconciliationRecord,
    owner_epoch: int,
) -> EffectSettled:
    runtime_state = state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        raise ValueError("reconciled state is missing runtime metadata")
    marker = runtime_state.get("reconciled_effect")
    if not isinstance(marker, Mapping):
        raise ValueError("reconciled state is missing the reconciled effect")
    if (
        marker.get("effect_id") != record.effect_id
        or marker.get("attempt_id") != record.attempt_id
        or marker.get("evidence_ref") != record.evidence_ref
        or marker.get("reconciliation_owner_epoch") != record.owner_epoch
        or marker.get("reconciliation_transition_id") != record.transition_id
        or marker.get("outcome") != record.observed_outcome.value
        or state_version.commit_ref.transition_id != record.transition_id
    ):
        raise ValueError("reconciled effect does not match reconciliation record")
    input_id = _required_reconciled_marker_str(marker, "reconciled_input_id")
    indeterminate_input_id = _required_reconciled_marker_str(
        marker,
        "indeterminate_input_id",
    )
    if input_id == indeterminate_input_id:
        raise ValueError("reconciled input must differ from indeterminate input")
    settlement = EffectSettlement(
        input_id=input_id,
        tool_call_id=_required_reconciled_marker_str(marker, "tool_call_id"),
        tool_name=_required_reconciled_marker_str(marker, "tool_name"),
        effect_id=record.effect_id,
        attempt_id=record.attempt_id,
        authorization_transition_id=_required_reconciled_marker_str(
            marker,
            "authorization_transition_id",
        ),
        owner_epoch=owner_epoch,
        outcome=EffectSettlementOutcome(marker["outcome"]),
        result=marker.get("result"),
        reason_code=_optional_reconciled_marker_str(marker, "reason_code"),
        reason_message=_optional_reconciled_marker_str(marker, "reason_message"),
    )
    return EffectSettled(settlement=settlement)


def _required_reconciled_marker_str(
    marker: Mapping[str, object],
    field_name: str,
) -> str:
    value = marker.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"reconciled effect {field_name} must be a non-empty string")
    return value


def _optional_reconciled_marker_str(
    marker: Mapping[str, object],
    field_name: str,
) -> str | None:
    value = marker.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"reconciled effect {field_name} must be a non-empty string or None"
        )
    return value


def _mailbox_cut(request: RunSegmentRequest) -> int:
    if isinstance(request.step_input, Initial):
        return request.step_input.mailbox_cut
    runtime_state = request.state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        raise ValueError("resumed segment state is missing the mailbox cut")
    mailbox_cut = runtime_state.get("mailbox_cut")
    if isinstance(mailbox_cut, bool) or not isinstance(mailbox_cut, int):
        raise ValueError("resumed segment mailbox cut must be an integer")
    if mailbox_cut < 0:
        raise ValueError("resumed segment mailbox cut must be non-negative")
    return mailbox_cut


def _approved_settlement(step_input) -> ApprovalSettlement | None:
    if isinstance(step_input, ApprovalResolved) and step_input.settlement.approved:
        return step_input.settlement
    return None


def _settlement(step_input) -> EffectSettlement | ApprovalSettlement:
    if isinstance(step_input, EffectSettled):
        return step_input.settlement
    if isinstance(step_input, ApprovalResolved):
        return step_input.settlement
    raise TypeError("step input does not carry a settlement")


def _settlement_mutation(
    settlement: EffectSettlement | ApprovalSettlement,
) -> EffectMutation:
    if isinstance(settlement, ApprovalSettlement):
        if settlement.approved:
            raise ValueError("approved settlement commits with dispatch authorization")
        return EffectMutation(
            effect_id=settlement.effect_id,
            attempt_id=settlement.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.REJECTED,
            payload={
                "reason_code": settlement.rejection_reason_code,
                "message": settlement.rejection_message,
            },
        )
    status_by_outcome = {
        EffectSettlementOutcome.COMPLETED: EffectStatus.COMPLETED,
        EffectSettlementOutcome.FAILED: EffectStatus.FAILED,
        EffectSettlementOutcome.INDETERMINATE: EffectStatus.UNKNOWN,
    }
    return EffectMutation(
        effect_id=settlement.effect_id,
        attempt_id=settlement.attempt_id,
        expected_status=EffectStatus.DISPATCHED,
        status=status_by_outcome[settlement.outcome],
        payload={
            "result": settlement.result,
            "reason_code": settlement.reason_code,
            "reason_message": settlement.reason_message,
        },
    )


def _committed_result(result):
    if isinstance(result, CommittedCommitResult):
        return result
    if isinstance(result, ExactReplayCommitResult):
        return CommittedCommitResult(
            state_version=result.state_version,
            notices=result.receipt.facts,
            receipt=result.receipt,
        )
    return _commit_failure(result)


def _authorized_result(result):
    if isinstance(result, DispatchAuthorizedResult):
        return result
    return _commit_failure(result)


def _commit_failure(result) -> FailedOutcome:
    if isinstance(result, ExactReplayCommitResult):
        report = FailureReport(
            code="exact_replay_requires_recovery",
            message="exact replay requires coordinator recovery",
        )
        state = result.state_version
    elif isinstance(result, CASConflictCommitResult):
        report = FailureReport(
            code="cas_conflict",
            message="operation state compare-and-swap conflict",
        )
        state = result.current_state
    elif isinstance(result, StaleOwnerCommitResult):
        report = FailureReport(code="stale_owner", message="owner epoch is stale")
        state = None
    elif isinstance(result, StaleMailboxCutCommitResult):
        report = FailureReport(
            code="stale_mailbox_cut",
            message="mailbox cut is stale",
        )
        state = None
    elif isinstance(result, InvalidTransitionCommitResult):
        report = FailureReport(
            code=result.reason_code,
            message=result.message,
        )
        state = None
    elif isinstance(result, StorageFailureCommitResult):
        report = result.error
        state = None
    else:
        raise TypeError("commit port returned an unsupported result")
    return FailedOutcome(state_version=state, error=report, steps_taken=0)


def _with_steps(
    outcome: FailedOutcome,
    steps_taken: int,
    *,
    fallback_state: OperationStateVersion,
) -> FailedOutcome:
    state_version = outcome.state_version
    if state_version is None:
        state_version = fallback_state
    return FailedOutcome(
        state_version=state_version,
        error=outcome.error,
        steps_taken=steps_taken,
    )


def _safe_yield(
    state_version: OperationStateVersion,
    snapshot: ControlSnapshot,
    steps_taken: int,
) -> SafeYieldOutcome:
    if snapshot.reason is None:
        raise ValueError("raised control snapshot requires a reason")
    return SafeYieldOutcome(
        state_version=state_version,
        reason=snapshot.reason,
        steps_taken=steps_taken,
    )


def _authorization_proposal(
    prepared: TransitionProposal,
    *,
    current_state: OperationStateVersion,
    preserve_pending: bool = False,
) -> TransitionProposal:
    action = prepared.next_action
    if not isinstance(action, PreparedEffectAction):
        raise ValueError("dispatch authorization requires a prepared effect action")
    plan = action.effect_plan
    prepared_transition_id = current_state.commit_ref.transition_id
    return TransitionProposal(
        transition_id=(
            f"{prepared_transition_id}:dispatch:{plan.effect_id}:{plan.attempt_id}"
        ),
        state_value=prepared.state_value if preserve_pending else current_state.value,
        next_action=action,
        pending_facts=prepared.pending_facts if preserve_pending else (),
        dispositions=prepared.dispositions if preserve_pending else (),
        effect_plans=prepared.effect_plans if preserve_pending else (),
    )


def _with_active_effect_authorization(
    proposal: TransitionProposal,
    *,
    owner_epoch: int,
) -> TransitionProposal:
    action = proposal.next_action
    if not isinstance(action, PreparedEffectAction):
        raise ValueError("dispatch authorization requires a prepared effect action")
    plan = action.effect_plan
    tool_call_id = plan.payload.get("tool_call_id")
    tool_name = plan.payload.get("tool_name")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ValueError("effect plan is missing tool_call_id")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("effect plan is missing tool_name")
    state_value = dict(proposal.state_value)
    raw_runtime = state_value.get("_agentkit_runtime", {})
    if not isinstance(raw_runtime, Mapping):
        raise TypeError("engine runtime state must be a mapping")
    runtime_state = dict(raw_runtime)
    runtime_state["active_effect_authorization"] = {
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "authorization_transition_id": proposal.transition_id,
        "dispatch_owner_epoch": owner_epoch,
    }
    state_value["_agentkit_runtime"] = runtime_state
    return TransitionProposal(
        transition_id=proposal.transition_id,
        state_value=state_value,
        next_action=proposal.next_action,
        pending_facts=proposal.pending_facts,
        dispositions=proposal.dispositions,
        effect_plans=proposal.effect_plans,
    )


def _is_post_reconciliation_settlement(
    state_version: OperationStateVersion,
    settlement: EffectSettlement | ApprovalSettlement,
) -> bool:
    if not isinstance(settlement, EffectSettlement):
        return False
    runtime_state = state_version.value.get("_agentkit_runtime")
    if not isinstance(runtime_state, Mapping):
        return False
    marker = runtime_state.get("reconciled_effect")
    return (
        isinstance(marker, Mapping)
        and marker.get("reconciled_input_id") == settlement.input_id
    )


def _effect_reference(plan: EffectPlan) -> EffectReference:
    return EffectReference.from_plan(plan)


def _validate_dispatch_permit(
    permit: DispatchPermit,
    *,
    session_id: str,
    owner_epoch: int,
    plan: EffectPlan,
    authorization_transition_id: str,
) -> None:
    expected = (
        session_id,
        plan.effect_id,
        plan.attempt_id,
        authorization_transition_id,
        owner_epoch,
        plan.idempotency_key,
    )
    actual = (
        permit.session_id,
        permit.effect_id,
        permit.attempt_id,
        permit.authorization_transition_id,
        permit.owner_epoch,
        permit.idempotency_key,
    )
    if actual != expected:
        raise ValueError("dispatch permit binding does not match authorization")


def _effect_settlement(
    *,
    plan: EffectPlan,
    authorization_transition_id: str,
    owner_epoch: int,
    result: EffectExecutionResult,
) -> EffectSettlement:
    tool_call_id = plan.payload.get("tool_call_id")
    tool_name = plan.payload.get("tool_name")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ValueError("effect plan is missing tool_call_id")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("effect plan is missing tool_name")
    input_id = f"{authorization_transition_id}:settlement:{plan.attempt_id}"
    if isinstance(result, EffectCompletedResult):
        return EffectSettlement.completed(
            input_id=input_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            authorization_transition_id=authorization_transition_id,
            owner_epoch=owner_epoch,
            result=result.result,
        )
    if isinstance(result, EffectFailedResult):
        return EffectSettlement(
            input_id=input_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            authorization_transition_id=authorization_transition_id,
            owner_epoch=owner_epoch,
            outcome=EffectSettlementOutcome.FAILED,
            result=result.error.message,
            reason_code=result.error.code,
            reason_message=result.error.message,
        )
    if isinstance(result, EffectIndeterminateResult):
        return EffectSettlement(
            input_id=input_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            authorization_transition_id=authorization_transition_id,
            owner_epoch=owner_epoch,
            outcome=EffectSettlementOutcome.INDETERMINATE,
            result={"evidence_ref": result.evidence_ref},
            reason_code=result.reason_code,
            reason_message=result.message,
        )
    raise TypeError("effect executor returned an unsupported result")


def _exception_message(exc: BaseException) -> str:
    message = str(exc)
    if message.strip():
        return message
    cause = exc.__cause__
    if cause is not None:
        return f"{type(exc).__name__}: {_exception_message(cause)}"
    return type(exc).__name__
