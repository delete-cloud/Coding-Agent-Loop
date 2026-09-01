from __future__ import annotations

from datetime import UTC, datetime
import pytest

from agentkit.runtime.contracts import (
    EffectMutation,
    EffectStatus,
    OperationStateCAS,
    RuntimeCommand,
)

from coding_agent.stores.rtstore.harness import (
    AuthoritativeUnitOfWork,
    CommandMailboxEntry,
    ChildExecutionBinding,
    EventRecord,
    ExecutorAttemptRecord,
    RecoveredChildExecutionLease,
    RecoveryGuardKind,
    RecoveryTransitionGuard,
    StaleRecoveryGuardError,
    adopt_parent_settlement_receipt,
    EffectMutationConflictError,
    UnstartedDispatchCloseoutGuard,
    assert_recovery_guard_shape,
    recovered_child_lease_payload,
    snapshot_transition_unit,
    transition_mutation_fingerprint,
)

STAMP = datetime(2026, 9, 1, tzinfo=UTC)


def _entry(
    *, command_id: str, session_seq: str, generation: str
) -> CommandMailboxEntry:
    return CommandMailboxEntry(
        command=RuntimeCommand(
            command_id=command_id,
            command_kind="approval_decision",
            payload={
                "approved": True,
                "request_id": "approval-1",
                "target_run_id": "child-1",
                "target_effect_id": "effect-1",
            },
        ),
        admitted_session_seq=session_seq,
        admitted_dispatch_generation=generation,
        disposition="admitted",
    )


def _unit(**changes: object) -> AuthoritativeUnitOfWork:
    values: dict[str, object] = {
        "event": None,
        "session_state": {"id": "session-1"},
        "transition_id": "transition-1",
        "state_cas": OperationStateCAS(
            run_id="run-1",
            revision=0,
            projection_epoch=0,
        ),
        "state_value": {"phase": "settled"},
        "facts": (
            EventRecord(
                event_id="fact-1",
                session_id="session-1",
                event_kind="tool_result",
                payload={"value": "unknown"},
                created_at=STAMP,
            ),
        ),
        "effect_mutations": (
            EffectMutation(
                effect_id="effect-1",
                attempt_id="attempt-1",
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.UNKNOWN,
                payload={"authorization_transition_id": "authorization-1"},
            ),
        ),
    }
    values.update(changes)
    return AuthoritativeUnitOfWork(**values)  # type: ignore[arg-type]


def _binding() -> ChildExecutionBinding:
    return ChildExecutionBinding(
        session_id="session-1",
        parent_run_id="parent-run",
        parent_effect_id="effect-1",
        parent_attempt_id="attempt-1",
        child_run_id="child-1",
        authorization_transition_id="authorization-1",
        live_parent_settlement_transition_id="parent-settlement",
    )


def _recovery_guard(kind: RecoveryGuardKind) -> RecoveryTransitionGuard:
    return RecoveryTransitionGuard(
        lease_id="lease-1",
        child_run_id="child-1",
        resume_generation=1,
        expected_recovery_cut="7",
        kind=kind,
    )


def test_executor_attempt_persists_both_authorization_watermarks() -> None:
    attempt = ExecutorAttemptRecord(
        session_id="session-1",
        effect_id="effect-1",
        attempt_id="attempt-1",
        authorization_transition_id="authorization-1",
        dispatch_owner_epoch=3,
        status="authorized_unclaimed",
        authorization_mailbox_cut="7",
        authorization_mailbox_session_seq="19",
    )

    assert attempt.payload()["authorization_mailbox_cut"] == "7"
    assert attempt.payload()["authorization_mailbox_session_seq"] == "19"


def test_recovery_lease_persists_authorization_cut_mailbox_snapshot() -> None:
    entry = _entry(command_id="allow-1", session_seq="19", generation="7")
    lease = RecoveredChildExecutionLease(
        session_id="session-1",
        child_run_id="child-1",
        lease_id="lease-1",
        resume_generation=2,
        resume_cut="7",
        owner_epoch=3,
        prior_session_seq="11",
        resume_session_seq="19",
        mailbox_snapshot=(entry,),
    )

    payload = recovered_child_lease_payload(lease)

    assert payload is not None
    assert payload["prior_session_seq"] == "11"
    assert payload["resume_session_seq"] == "19"
    assert payload["mailbox_snapshot"] == [
        {
            "command_id": "allow-1",
            "command_kind": "approval_decision",
            "command_payload": {
                "approved": True,
                "request_id": "approval-1",
                "target_run_id": "child-1",
                "target_effect_id": "effect-1",
            },
            "admitted_session_seq": "19",
            "admitted_dispatch_generation": "7",
            "disposition": "admitted",
        }
    ]


def test_closeout_guard_and_terminal_action_are_snapshotted_and_fingerprinted() -> None:
    guard = UnstartedDispatchCloseoutGuard(
        effect_id="effect-1",
        attempt_id="attempt-1",
        authorization_transition_id="authorization-1",
        executor_id="coordinator-unstarted",
        evidence_ref="control-after-authorization",
        closed_at=STAMP,
    )
    unit = _unit(terminal_action=True, unstarted_dispatch_closeout=guard)

    snapshot = snapshot_transition_unit(unit)

    assert snapshot.terminal_action is True
    assert snapshot.unstarted_dispatch_closeout == guard
    assert transition_mutation_fingerprint(unit) != transition_mutation_fingerprint(
        _unit(terminal_action=False, unstarted_dispatch_closeout=guard)
    )
    assert transition_mutation_fingerprint(unit) != transition_mutation_fingerprint(
        _unit(
            terminal_action=True,
            unstarted_dispatch_closeout=UnstartedDispatchCloseoutGuard(
                effect_id="effect-1",
                attempt_id="attempt-1",
                authorization_transition_id="authorization-1",
                executor_id="coordinator-unstarted",
                evidence_ref="different-evidence",
                closed_at=STAMP,
            ),
        )
    )


def test_recovery_guard_shape_uses_terminal_action() -> None:
    recovery = RecoveryTransitionGuard(
        lease_id="lease-1",
        child_run_id="child-1",
        resume_generation=1,
        expected_recovery_cut="7",
        kind=RecoveryGuardKind.CHILD_TERMINAL,
    )

    unit = _unit(recovery_guard=recovery, terminal_action=True)

    assert unit.terminal_action is True


def test_snapshot_preserves_every_recovery_field_explicitly() -> None:
    guard = _recovery_guard(RecoveryGuardKind.CHILD_TERMINAL)
    snapshot = snapshot_transition_unit(
        _unit(
            state_cas=OperationStateCAS("child-1", 0, 0),
            recovery_guard=guard,
            terminal_action=True,
            effect_mutations=(),
            adopt_transition_ids=("parent-settlement",),
        )
    )

    assert snapshot.recovery_guard == guard
    assert snapshot.terminal_action is True
    assert snapshot.adopt_transition_ids == ("parent-settlement",)


def test_transition_adoption_ids_reject_non_string_values() -> None:
    with pytest.raises(TypeError, match="strings"):
        _unit(adopt_transition_ids=(1,))


def test_child_terminal_guard_rejects_nonterminal_transition() -> None:
    unit = _unit(
        state_cas=OperationStateCAS("child-1", 0, 0),
        recovery_guard=_recovery_guard(RecoveryGuardKind.CHILD_TERMINAL),
        terminal_action=False,
        effect_mutations=(),
    )
    with pytest.raises(StaleRecoveryGuardError):
        assert_recovery_guard_shape(unit, _binding())


def test_child_terminal_guard_rejects_parent_effect_mutation() -> None:
    unit = _unit(
        state_cas=OperationStateCAS("child-1", 0, 0),
        recovery_guard=_recovery_guard(RecoveryGuardKind.CHILD_TERMINAL),
        terminal_action=True,
    )
    with pytest.raises(StaleRecoveryGuardError):
        assert_recovery_guard_shape(unit, _binding())


def _parent_settlement_unit(**changes: object) -> AuthoritativeUnitOfWork:
    values: dict[str, object] = {
        "state_cas": OperationStateCAS("parent-run", 0, 0),
        "recovery_guard": _recovery_guard(RecoveryGuardKind.PARENT_SETTLEMENT),
        "terminal_action": False,
    }
    values.update(changes)
    return _unit(**values)


def test_parent_settlement_guard_rejects_terminal_action() -> None:
    with pytest.raises(StaleRecoveryGuardError):
        assert_recovery_guard_shape(
            _parent_settlement_unit(terminal_action=True),
            _binding(),
        )


def test_parent_settlement_guard_rejects_wrong_run() -> None:
    with pytest.raises(StaleRecoveryGuardError):
        assert_recovery_guard_shape(
            _parent_settlement_unit(state_cas=OperationStateCAS("other-run", 0, 0)),
            _binding(),
        )


def test_parent_settlement_guard_requires_exactly_one_terminal_effect_mutation() -> (
    None
):
    assert_recovery_guard_shape(_parent_settlement_unit(), _binding())


def test_parent_settlement_guard_rejects_additional_mutation() -> None:
    extra = EffectMutation(
        effect_id="effect-extra",
        attempt_id="attempt-extra",
        expected_status=EffectStatus.DISPATCHED,
        status=EffectStatus.UNKNOWN,
        payload={},
    )
    with pytest.raises(StaleRecoveryGuardError):
        assert_recovery_guard_shape(
            _parent_settlement_unit(
                effect_mutations=(
                    *_unit().normalized_effect_mutations,
                    extra,
                )
            ),
            _binding(),
        )


def test_parent_settlement_receipt_helper_detects_dual_and_terminal_corruption() -> (
    None
):
    with pytest.raises(
        EffectMutationConflictError,
        match="live and recovery parent settlement receipts both exist",
    ):
        adopt_parent_settlement_receipt(
            current_id="live",
            current_row="live-row",
            adopted_rows=(("recovery", "recovery-row"),),
            parent_effect_status="dispatched",
        )

    with pytest.raises(
        EffectMutationConflictError,
        match="terminal parent effect has no live or recovery settlement receipt",
    ):
        adopt_parent_settlement_receipt(
            current_id="live",
            current_row=None,
            adopted_rows=(("recovery", None),),
            parent_effect_status="completed",
        )

    assert adopt_parent_settlement_receipt(
        current_id="live",
        current_row=None,
        adopted_rows=(("recovery", "recovery-row"),),
        parent_effect_status="dispatched",
    ) == ("recovery", "recovery-row")
