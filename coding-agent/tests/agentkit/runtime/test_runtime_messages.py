from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentkit.runtime import (
    DuplicateRuntimeMessageError,
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)
from agentkit.runtime.messages import (
    AppliedCommandDisposition,
    CommitRef,
    CommittedFactNotice,
    EffectMutation,
    EffectStatus,
    EffectPlan,
    OperationStateCAS,
    OperationStateVersion,
    PendingFact,
    ReconciliationOutcome,
    ReconciliationRecord,
    RejectedCommandDisposition,
    RuntimeCommand,
    StreamFrame,
    StreamFrameKind,
    SupersededCommandDisposition,
    TransitionProposal,
)


def test_runtime_message_kinds_cover_pr4_controls() -> None:
    assert {kind.value for kind in RuntimeMessageKind} == {
        "interrupt",
        "user_steer",
        "approval_decision",
        "subagent_message",
        "system_notice",
    }


@pytest.mark.asyncio
async def test_runtime_message_bus_consumes_by_cursor_idempotently() -> None:
    bus = InMemoryRuntimeMessageBus()

    first = await bus.publish(
        RuntimeMessage(
            message_id="msg-1",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "Prefer small steps"},
        )
    )
    second = await bus.publish(
        RuntimeMessage(
            message_id="msg-2",
            kind=RuntimeMessageKind.SYSTEM_NOTICE,
            payload={"text": "Checkpoint restored"},
        )
    )

    assert first.sequence == 1
    assert second.sequence == 2

    start = RuntimeMessageCursor()
    batch = await bus.consume_after(start)
    repeat = await bus.consume_after(start)
    empty = await bus.consume_after(batch.cursor)

    assert [item.message.message_id for item in batch.messages] == ["msg-1", "msg-2"]
    assert batch.cursor.sequence == 2
    assert repeat == batch
    assert empty.messages == ()
    assert empty.cursor == batch.cursor


@pytest.mark.asyncio
async def test_runtime_message_bus_honors_limit() -> None:
    bus = InMemoryRuntimeMessageBus()
    await bus.publish(
        RuntimeMessage(message_id="msg-1", kind=RuntimeMessageKind.USER_STEER)
    )
    await bus.publish(
        RuntimeMessage(message_id="msg-2", kind=RuntimeMessageKind.SYSTEM_NOTICE)
    )

    first = await bus.consume_after(RuntimeMessageCursor(), limit=1)
    second = await bus.consume_after(first.cursor)

    assert [item.message.message_id for item in first.messages] == ["msg-1"]
    assert first.cursor.sequence == 1
    assert [item.message.message_id for item in second.messages] == ["msg-2"]
    assert second.cursor.sequence == 2


@pytest.mark.asyncio
async def test_runtime_message_bus_filters_kinds_with_independent_cursors() -> None:
    bus = InMemoryRuntimeMessageBus()
    await bus.publish(
        RuntimeMessage(
            message_id="msg-approval",
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={"request_id": "req-1", "approved": True},
        )
    )
    await bus.publish(
        RuntimeMessage(
            message_id="msg-steer",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "Prefer short answers"},
        )
    )

    pipeline_batch = await bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.USER_STEER},
    )
    approval_batch = await bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.APPROVAL_DECISION},
    )

    assert [item.message.message_id for item in pipeline_batch.messages] == [
        "msg-steer"
    ]
    assert pipeline_batch.cursor.sequence == 2
    assert [item.message.message_id for item in approval_batch.messages] == [
        "msg-approval"
    ]
    assert approval_batch.cursor.sequence == 1


@pytest.mark.asyncio
async def test_runtime_message_bus_rejects_duplicate_message_ids() -> None:
    bus = InMemoryRuntimeMessageBus()
    message = RuntimeMessage(
        message_id="msg-1",
        kind=RuntimeMessageKind.INTERRUPT,
        payload={"reason": "stop"},
    )

    await bus.publish(message)

    with pytest.raises(DuplicateRuntimeMessageError) as exc_info:
        await bus.publish(message)
    assert exc_info.value.message_id == "msg-1"


def test_runtime_message_rejects_empty_message_id() -> None:
    with pytest.raises(ValueError, match="message_id must be non-empty"):
        RuntimeMessage(
            message_id="",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "invalid"},
        )


def test_runtime_message_rejects_unknown_kind_with_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown runtime message kind: unknown"):
        RuntimeMessage(
            message_id="msg-1",
            kind="unknown",  # pyright: ignore[reportArgumentType]
        )


def test_operation_state_version_is_immutable_and_carries_logical_cas_identity() -> (
    None
):
    version = OperationStateVersion(
        run_id="run-1",
        revision=7,
        projection_epoch=3,
        commit_ref=CommitRef(
            transition_id="transition-7",
            fact_seq_start="41",
            fact_seq_end="42",
        ),
        value={"phase": "waiting", "nested": {"round": 4}},
    )

    assert version.cas == OperationStateCAS(
        run_id="run-1",
        revision=7,
        projection_epoch=3,
    )
    assert version.commit_ref.transition_id == "transition-7"
    assert version.commit_ref.fact_seq_start == "41"
    assert version.commit_ref.fact_seq_end == "42"
    with pytest.raises(FrozenInstanceError):
        version.revision = 8  # type: ignore[misc]
    with pytest.raises(TypeError):
        version.value["phase"] = "completed"  # type: ignore[index]
    with pytest.raises(TypeError):
        version.value["nested"]["round"] = 5  # type: ignore[index]


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
)
def test_runtime_contracts_reject_non_finite_json_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="finite floats"):
        RuntimeCommand(
            command_id="command-non-finite",
            command_kind="steer",
            payload={"value": value},
        )
    with pytest.raises(ValueError, match="finite floats"):
        RuntimeMessage(
            message_id="message-non-finite",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"value": value},
        )


def test_transition_proposal_carries_pending_facts_typed_dispositions_and_effect_mutation() -> (
    None
):
    command = RuntimeCommand(
        command_id="command-1",
        command_kind="interrupt",
        payload={"source": "user"},
    )
    fact = PendingFact(
        fact_id="fact-1",
        fact_kind="finalized_thinking",
        payload={"text": "checked constraints"},
    )
    disposition = AppliedCommandDisposition(command_id=command.command_id)
    effect_plan = EffectPlan(
        effect_id="effect-1",
        attempt_id="attempt-1",
        effect_kind="tool",
        payload={"name": "read", "arguments": {"path": "README.md"}},
    )
    effect_mutation = EffectMutation.prepare(effect_plan)

    proposal = TransitionProposal(
        transition_id="transition-1",
        state_value={"phase": "prepared"},
        pending_facts=(fact,),
        dispositions=(disposition,),
        effect_mutation=effect_mutation,
    )

    assert proposal.pending_facts == (fact,)
    assert proposal.dispositions == (disposition,)
    assert proposal.effect_mutation == effect_mutation
    with pytest.raises(TypeError):
        proposal.state_value["phase"] = "changed"  # type: ignore[index]


def test_stream_frame_and_committed_fact_notice_are_distinct_typed_contracts() -> None:
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
        projection_epoch=2,
    )

    assert frame.kind is StreamFrameKind.THINKING_DELTA
    assert notice.session_seq == "12"
    assert not hasattr(frame, "session_seq")
    assert not hasattr(notice, "kind")


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
        state_value={"phase": "waiting"},
    )

    assert command.command_id not in {
        disposition.command_id for disposition in proposal.dispositions
    }
    disposition_values = {
        AppliedCommandDisposition(command_id="applied").kind.value,
        RejectedCommandDisposition(
            command_id="rejected",
            reason_code="invalid",
        ).kind.value,
        SupersededCommandDisposition(
            command_id="superseded",
            superseded_by_command_id="replacement",
        ).kind.value,
    }
    assert disposition_values == {"applied", "rejected", "superseded"}


def test_explicit_effect_mutation_graph_requires_reconciliation_from_unknown() -> None:
    with pytest.raises(ValueError, match="invalid effect transition"):
        EffectMutation(
            effect_id="effect-1",
            attempt_id="attempt-1",
            expected_status=EffectStatus.PREPARED,
            status=EffectStatus.COMPLETED,
            payload={},
        )
    with pytest.raises(ValueError, match="requires a reconciliation record"):
        EffectMutation(
            effect_id="effect-1",
            attempt_id="attempt-1",
            expected_status=EffectStatus.UNKNOWN,
            status=EffectStatus.COMPLETED,
            payload={},
        )

    reconciliation = ReconciliationRecord(
        effect_id="effect-1",
        attempt_id="attempt-1",
        observed_outcome=ReconciliationOutcome.COMPLETED,
        evidence_ref="evidence-1",
        actor_id="reconciler-1",
        owner_epoch=4,
        transition_id="transition-reconcile",
    )
    mutation = EffectMutation(
        effect_id="effect-1",
        attempt_id="attempt-1",
        expected_status=EffectStatus.UNKNOWN,
        status=EffectStatus.COMPLETED,
        payload={"result": "ok"},
        reconciliation=reconciliation,
    )

    assert mutation.reconciliation == reconciliation
