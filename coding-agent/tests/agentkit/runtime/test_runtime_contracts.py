from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from typing import get_args

import pytest

from agentkit.runtime.contracts import (
    ApprovalResolved,
    ApprovalSettlement,
    AppliedCommandDisposition,
    BlockedAction,
    BlockedOutcome,
    CancellationToken,
    CancelledOutcome,
    CASConflictCommitResult,
    CommitReconciliationResult,
    CommitRef,
    CommitSettlementResult,
    CommitTransitionResult,
    CommittedCommitResult,
    CommittedFactNotice,
    CompletedOutcome,
    ControlGeneration,
    DispatchAuthorizationResult,
    DispatchPermit,
    EffectCompletedResult,
    EffectExecutionResult,
    EffectFailedResult,
    EffectIndeterminateResult,
    EffectMutation,
    EffectPlan,
    EffectReference,
    EffectSettled,
    EffectSettlement,
    EffectStatus,
    EngineStepInput,
    ExactReplayCommitResult,
    FailedOutcome,
    FailureReport,
    Initial,
    InvalidTransitionCommitResult,
    ModelGenerationAction,
    ModelGenerationCompleted,
    ModelGenerationResult,
    ModelRequest,
    ModelUsage,
    NextAction,
    OperationStateCAS,
    OperationStateVersion,
    PreparedEffectAction,
    PreparedEffectActionKind,
    ProviderStopMetadata,
    ReconciliationOutcome,
    ReconciliationRecord,
    PendingFact,
    RejectedCommandDisposition,
    RoundLimitOutcome,
    RuntimeCommand,
    SafeYieldAction,
    SafeYieldOutcome,
    SegmentOutcome,
    StaleMailboxCutCommitResult,
    StaleOwnerCommitResult,
    StorageFailureCommitResult,
    StreamFrame,
    StreamFrameKind,
    SupersededCommandDisposition,
    TerminalAction,
    TransitionProposal,
    TransitionReceipt,
)


def _state() -> OperationStateVersion:
    return OperationStateVersion(
        run_id="run-1",
        revision=0,
        projection_epoch=3,
        commit_ref=CommitRef(transition_id="admission"),
        value={"context": {"messages": []}},
    )


def _model_request() -> ModelRequest:
    return ModelRequest(
        request_id="model-request-1",
        run_id="run-1",
        round_index=1,
        commands=(),
        context={"messages": []},
    )


def _effect_plan(*, approval: bool = False) -> EffectPlan:
    return EffectPlan(
        effect_id="effect-1",
        attempt_id="attempt-1",
        effect_kind="tool",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "read",
            "arguments": {"path": "README.md"},
        },
        requires_approval=approval,
        approval_request_id="approval-1" if approval else None,
    )


def test_transition_proposal_next_action_is_explicit_union() -> None:
    request = _model_request()
    plan = _effect_plan()
    variants = (
        ModelGenerationAction(request=request),
        PreparedEffectAction(
            effect_plan=plan,
            action_kind=PreparedEffectActionKind.DISPATCH,
        ),
        TerminalAction(final_message="done", stop_reason="no_tool_calls"),
        BlockedAction(
            reason="approval_required", effect=EffectReference.from_plan(plan)
        ),
        SafeYieldAction(reason="interrupt"),
    )

    for index, action in enumerate(variants):
        proposal = TransitionProposal(
            transition_id=f"transition-{index}",
            state_value={},
            next_action=action,
        )
        assert proposal.next_action is action

    assert {type(action) for action in variants} == set(get_args(NextAction))
    with pytest.raises(TypeError):
        TransitionProposal(  # type: ignore[call-arg]
            transition_id="missing-action",
            state_value={},
        )


def test_engine_step_input_is_a_consume_once_union_with_stable_input_identity() -> None:
    model_result = ModelGenerationResult(
        result_id="model-result-1",
        request_id="model-request-1",
        assistant_content="done",
        finalized_thinking=None,
        tool_calls=(),
        usage=ModelUsage(input_tokens=2, output_tokens=1),
        provider_stop=ProviderStopMetadata(reason="stop"),
    )
    settlement = EffectSettlement.completed(
        input_id="effect-input-1",
        tool_call_id="call-1",
        tool_name="read",
        effect_id="effect-1",
        attempt_id="attempt-1",
        authorization_transition_id="dispatch-1",
        owner_epoch=4,
        result={"content": "ok"},
    )
    approval = ApprovalSettlement(
        input_id="approval-input-1",
        command_id="command-approval-1",
        tool_call_id="call-1",
        tool_name="read",
        effect_id="effect-1",
        attempt_id="attempt-1",
        transition_id="approval-transition-1",
        owner_epoch=4,
        approved=True,
    )
    variants: tuple[EngineStepInput, ...] = (
        Initial(input_id="initial-1", command_batch=(), mailbox_cut=7),
        ModelGenerationCompleted(result=model_result),
        EffectSettled(settlement=settlement),
        ApprovalResolved(settlement=approval),
    )

    assert [item.input_id for item in variants] == [
        "initial-1",
        "model-result-1",
        "effect-input-1",
        "approval-input-1",
    ]
    assert set(get_args(EngineStepInput)) == {
        Initial,
        ModelGenerationCompleted,
        EffectSettled,
        ApprovalResolved,
    }


def test_stream_frame_carries_only_ephemeral_token_thinking_heartbeat() -> None:
    assert {kind.value for kind in StreamFrameKind} == {
        "token_delta",
        "thinking_delta",
        "heartbeat",
    }
    assert {item.name for item in fields(StreamFrame)} == {
        "frame_id",
        "kind",
        "payload",
    }
    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.TOKEN_DELTA,
        payload={"text": "partial"},
    )
    assert not hasattr(frame, "tool_calls")
    assert not hasattr(frame, "usage")
    with pytest.raises(ValueError, match="cannot carry"):
        StreamFrame(
            frame_id="invalid-frame",
            kind=StreamFrameKind.HEARTBEAT,
            payload={"usage": {"input_tokens": 1}},
        )


def test_segment_outcome_is_a_discriminated_union_with_exact_fields() -> None:
    state = _state()
    disposition = AppliedCommandDisposition(command_id="cancel-1")
    effect = EffectReference.from_plan(_effect_plan())
    error = FailureReport(code="provider_error", message="provider failed")
    outcomes: tuple[SegmentOutcome, ...] = (
        CompletedOutcome(
            state_version=state,
            final_message="done",
            steps_taken=1,
            stop_reason="no_tool_calls",
        ),
        BlockedOutcome(
            state_version=state,
            reason="approval_required",
            effect=effect,
            steps_taken=1,
        ),
        SafeYieldOutcome(state_version=state, reason="interrupt", steps_taken=1),
        CancelledOutcome(
            state_version=state,
            command_disposition=disposition,
            steps_taken=1,
        ),
        RoundLimitOutcome(state_version=state, steps_taken=4),
        FailedOutcome(state_version=state, error=error, steps_taken=1),
    )

    assert set(get_args(SegmentOutcome)) == {
        CompletedOutcome,
        BlockedOutcome,
        SafeYieldOutcome,
        CancelledOutcome,
        RoundLimitOutcome,
        FailedOutcome,
    }
    assert [{item.name for item in fields(type(outcome))} for outcome in outcomes] == [
        {"kind", "state_version", "final_message", "steps_taken", "stop_reason"},
        {"kind", "state_version", "reason", "effect", "steps_taken"},
        {"kind", "state_version", "reason", "steps_taken"},
        {"kind", "state_version", "command_disposition", "steps_taken"},
        {"kind", "state_version", "steps_taken"},
        {"kind", "state_version", "error", "steps_taken"},
    ]


def test_commit_port_results_distinguish_committed_exact_replay_cas_conflict_stale_owner_stale_mailbox_cut_invalid_transition_and_storage_failure() -> (
    None
):
    expected = {
        CommittedCommitResult,
        ExactReplayCommitResult,
        CASConflictCommitResult,
        StaleOwnerCommitResult,
        StaleMailboxCutCommitResult,
        InvalidTransitionCommitResult,
        StorageFailureCommitResult,
    }
    assert set(get_args(CommitTransitionResult)) == expected
    assert set(get_args(CommitSettlementResult)) == expected
    assert set(get_args(CommitReconciliationResult)) == expected
    assert expected - {CommittedCommitResult} < set(
        get_args(DispatchAuthorizationResult)
    )


def test_effect_executor_result_distinguishes_completed_failed_and_indeterminate_dispatch() -> (
    None
):
    assert set(get_args(EffectExecutionResult)) == {
        EffectCompletedResult,
        EffectFailedResult,
        EffectIndeterminateResult,
    }


def test_cancellation_token_after_dispatch_cannot_claim_non_execution() -> None:
    assert set(get_args(EffectExecutionResult)) == {
        EffectCompletedResult,
        EffectFailedResult,
        EffectIndeterminateResult,
    }
    assert "cancelled" not in {
        variant.__name__.casefold() for variant in get_args(EffectExecutionResult)
    }
    assert hasattr(CancellationToken, "cancelled")


def test_reconciliation_record_carries_attempt_evidence_actor_epoch_and_transition_identity() -> (
    None
):
    record = ReconciliationRecord(
        effect_id="effect-1",
        attempt_id="attempt-2",
        observed_outcome=ReconciliationOutcome.FAILED,
        evidence_ref="log://worker/7",
        actor_id="reconciler-1",
        owner_epoch=8,
        transition_id="reconciliation-1",
    )
    assert (
        record.effect_id,
        record.attempt_id,
        record.evidence_ref,
        record.actor_id,
        record.owner_epoch,
        record.transition_id,
    ) == (
        "effect-1",
        "attempt-2",
        "log://worker/7",
        "reconciler-1",
        8,
        "reconciliation-1",
    )


def test_dispatch_permit_is_opaque_single_use_and_bound_to_session_attempt_transition_and_epoch() -> (
    None
):
    permit = DispatchPermit.issue(
        opaque_token="opaque-token",
        session_id="session-1",
        effect_id="effect-1",
        attempt_id="attempt-1",
        authorization_transition_id="dispatch-1",
        owner_epoch=9,
        idempotency_key="external-1",
    )

    assert permit.session_id == "session-1"
    assert permit.effect_id == "effect-1"
    assert permit.attempt_id == "attempt-1"
    assert permit.authorization_transition_id == "dispatch-1"
    assert permit.owner_epoch == 9
    assert permit.idempotency_key == "external-1"
    assert "opaque-token" not in repr(permit)
    permit.claim()
    with pytest.raises(RuntimeError, match="already claimed"):
        permit.claim()


def test_contract_values_are_transitively_immutable() -> None:
    command_payload = {"nested": {"items": [1, 2]}}
    command = RuntimeCommand(
        command_id="command-1",
        command_kind="steer",
        payload=command_payload,
    )
    command_payload["nested"]["items"].append(3)  # type: ignore[index,union-attr]

    assert command.payload["nested"]["items"] == (1, 2)  # type: ignore[index]
    with pytest.raises(TypeError):
        command.payload["nested"]["new"] = True  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        command.command_id = "changed"  # type: ignore[misc]


def test_prepared_effect_action_represents_dispatch_and_approval_wait() -> None:
    dispatch_plan = _effect_plan()
    approval_plan = _effect_plan(approval=True)
    dispatch = PreparedEffectAction(
        effect_plan=dispatch_plan,
        action_kind=PreparedEffectActionKind.DISPATCH,
    )
    wait = PreparedEffectAction(
        effect_plan=approval_plan,
        action_kind=PreparedEffectActionKind.APPROVAL_WAIT,
    )

    assert dispatch.action_kind is PreparedEffectActionKind.DISPATCH
    assert wait.action_kind is PreparedEffectActionKind.APPROVAL_WAIT
    with pytest.raises(ValueError, match="requires_approval"):
        PreparedEffectAction(
            effect_plan=dispatch_plan,
            action_kind=PreparedEffectActionKind.APPROVAL_WAIT,
        )


def test_command_dispositions_have_no_deferred_variant() -> None:
    dispositions = (
        AppliedCommandDisposition(command_id="applied"),
        RejectedCommandDisposition(command_id="rejected", reason_code="invalid"),
        SupersededCommandDisposition(
            command_id="superseded",
            superseded_by_command_id="replacement",
        ),
    )
    assert {item.kind.value for item in dispositions} == {
        "applied",
        "rejected",
        "superseded",
    }


def test_control_generation_is_an_immutable_non_negative_value() -> None:
    generation = ControlGeneration(4)
    assert generation.value == 4
    with pytest.raises(FrozenInstanceError):
        generation.value = 5  # type: ignore[misc]


def test_operation_state_version_carries_host_neutral_cas_and_commit_ref() -> None:
    state = _state()
    assert state.cas == OperationStateCAS(
        run_id="run-1",
        revision=0,
        projection_epoch=3,
    )
    assert state.commit_ref.transition_id == "admission"


def test_explicit_phase_b_effect_mutation_graph_remains_available_to_store_uow() -> (
    None
):
    plan = _effect_plan()
    prepared = EffectMutation.prepare(plan)
    assert prepared.status is EffectStatus.PREPARED
    assert prepared.payload == {
        "effect_kind": "tool",
        "payload": plan.payload,
        "idempotency_key": None,
    }
    with pytest.raises(ValueError, match="invalid effect transition"):
        EffectMutation(
            effect_id=plan.effect_id,
            attempt_id=plan.attempt_id,
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.COMPLETED,
            payload={},
        )


def test_stream_frame_and_committed_fact_notice_are_separate_surfaces() -> None:
    frame = StreamFrame(
        frame_id="frame-1",
        kind=StreamFrameKind.THINKING_DELTA,
        payload={"text": "ephemeral"},
    )
    notice = CommittedFactNotice(
        fact_id="fact-1",
        fact_kind="finalized_thinking",
        payload={"text": "durable"},
        session_seq="12",
        projection_epoch=3,
        event_record_id="event-12",
    )
    assert not hasattr(frame, "session_seq")
    assert not hasattr(notice, "kind")
    assert notice.event_record_id == "event-12"


def test_transition_receipt_covers_complete_ordered_effect_plan_collection() -> None:
    plans = (
        _effect_plan(),
        EffectPlan(
            effect_id="effect-2",
            attempt_id="attempt-2",
            effect_kind="tool",
            payload={
                "tool_call_id": "call-2",
                "tool_name": "write",
                "arguments": {"path": "out.txt", "content": "x"},
            },
        ),
    )
    state = OperationStateVersion(
        run_id="run-1",
        revision=1,
        projection_epoch=3,
        commit_ref=CommitRef(transition_id="transition-1"),
        value={},
    )
    receipt = TransitionReceipt(
        session_id="session-1",
        projection_epoch=3,
        transition_id="transition-1",
        mutation_fingerprint="sha256:ordered-plans",
        state_version=state,
        facts=(),
        effect_plans=plans,
    )
    assert receipt.effect_plans == plans
    assert [plan.payload["tool_call_id"] for plan in receipt.effect_plans] == [
        "call-1",
        "call-2",
    ]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runtime_contracts_reject_non_finite_json_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="finite floats"):
        PendingFact(
            fact_id="fact-non-finite",
            fact_kind="test",
            payload={"value": value},
        )


def test_rejected_disposition_requires_stable_reason_code() -> None:
    with pytest.raises(ValueError, match="reason_code must be non-empty"):
        RejectedCommandDisposition(command_id="command-1", reason_code="")


def test_superseded_disposition_requires_superseded_by_command_id() -> None:
    with pytest.raises(ValueError, match="superseded_by_command_id must be non-empty"):
        SupersededCommandDisposition(
            command_id="command-1",
            superseded_by_command_id="",
        )


def test_absent_command_disposition_remains_pending_and_no_deferred_exists() -> None:
    command = RuntimeCommand(
        command_id="command-pending",
        command_kind="steer",
        payload={},
    )
    proposal = TransitionProposal(
        transition_id="transition-pending",
        state_value={},
        next_action=SafeYieldAction(reason="pending"),
    )
    assert command.command_id not in {
        disposition.command_id for disposition in proposal.dispositions
    }
    assert {
        AppliedCommandDisposition(command_id="applied").kind.value,
        RejectedCommandDisposition(
            command_id="rejected",
            reason_code="invalid",
        ).kind.value,
        SupersededCommandDisposition(
            command_id="superseded",
            superseded_by_command_id="replacement",
        ).kind.value,
    } == {"applied", "rejected", "superseded"}
