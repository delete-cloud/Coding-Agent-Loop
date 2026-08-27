"""Red contracts for ``coding_agent.harness.compensation``.

The public ``ledger.effect(session_id, effect_id)`` lookup is session-scoped.
"""

from __future__ import annotations


FENCE = {"session_id": "session-7", "owner_id": "daemon-a", "epoch": 4}


def test_effect_id_allocated_when_approval_wait_is_established() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=40)

    wait = ledger.establish_approval_wait(
        fence=FENCE,
        run_id="run-11",
        approval_id="approval-3",
    )

    assert wait.effect_id == 40
    assert ledger.effect("session-7", 40).state == "prepared"


def test_restore_then_reapprove_reuses_same_effect_id() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=40)
    wait = ledger.establish_approval_wait(
        fence=FENCE,
        run_id="run-11",
        approval_id="approval-3",
    )

    ledger.restore(checkpoint_id="checkpoint-2", projection_epoch=1)
    result = ledger.approve(
        fence={**FENCE, "epoch": 5},
        approval_id="approval-3",
    )

    assert wait.effect_id == 40
    assert result.effect_id == 40
    assert result.dispatch_count == 1
    assert ledger.next_effect_id == 41


def test_dispatched_or_unknown_blocks_normal_approval_dispatch() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    for state in ("dispatched", "unknown"):
        ledger = CompensationLedger(first_effect_id=40)
        ledger.establish_approval_wait(
            fence=FENCE,
            run_id="run-11",
            approval_id="approval-3",
        )
        ledger.set_attempt_state(effect_id=40, state=state)

        result = ledger.approve(fence=FENCE, approval_id="approval-3")

        assert result.effect_id == 40
        assert result.dispatched is False
        assert result.reason == f"attempt_{state}_blocks_redispatch"
        assert ledger.effect("session-7", 40).dispatch_count == 1


def test_compensation_receipt_identity_is_immutable_and_status_is_live() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=80)
    first = ledger.compensate(
        fence=FENCE,
        original_effect_id=40,
        client_key="client-op-9",
        generation=3,
    )
    ledger.set_attempt_state(effect_id=80, state="completed")
    ledger.resolve_settlement(effect_id=80)
    replay = ledger.compensate(
        fence=FENCE,
        original_effect_id=40,
        client_key="client-op-9",
        generation=99,
    )

    assert first.as_dict() == {
        "generation": 3,
        "compensation_effect_id": 80,
        "attempt_state": "prepared",
        "settlement": "absent",
    }
    assert replay.as_dict() == {
        "generation": 3,
        "compensation_effect_id": 80,
        "attempt_state": "completed",
        "settlement": "resolved",
    }


def test_a_completed_without_b_is_repair_only_and_rejects_c2() -> None:
    from coding_agent.harness.compensation import CompensationAdmissionError
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=80)
    ledger.seed_attempt(
        original_effect_id=40,
        compensation_effect_id=80,
        generation=3,
        state="completed",
        settlement="absent",
        session_id="session-7",
    )

    classification = ledger.classify(original_effect_id=40)
    error = ledger.capture_error(
        CompensationAdmissionError,
        ledger.compensate,
        fence=FENCE,
        original_effect_id=40,
        client_key="client-op-c2",
        generation=4,
    )

    assert classification == "repair_only"
    assert error.code == "repair_only"
    assert ledger.next_effect_id == 81


def test_repair_and_c2_concurrent_when_a_completed_b_absent_rejects_c2_and_writes_b_only() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=81)
    ledger.seed_attempt(
        original_effect_id=40,
        compensation_effect_id=80,
        generation=3,
        state="completed",
        settlement="absent",
        session_id="session-7",
    )

    result = ledger.race_repair_and_admission(
        fence=FENCE,
        original_effect_id=40,
        new_client_key="client-op-c2",
        new_generation=4,
    )

    assert result.admission == "rejected_repair_only"
    assert result.committed_writes == (
        ("settlement_b", 80, "resolved"),
    )
    assert ledger.attempt_ids(original_effect_id=40) == (80,)
    assert ledger.compensation_cas(original_effect_id=40) == 1


def test_failed_a_does_not_lift_quiescent_and_allows_generation_plus_one() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=81)
    ledger.seed_attempt(
        original_effect_id=40,
        compensation_effect_id=80,
        generation=3,
        state="failed",
        settlement="absent",
        session_id="session-7",
    )

    receipt = ledger.compensate(
        fence=FENCE,
        original_effect_id=40,
        client_key="client-op-c2",
        generation=4,
    )

    assert ledger.quiescent(original_effect_id=40) is True
    assert receipt.generation == 4
    assert receipt.compensation_effect_id == 81
    assert receipt.attempt_state == "prepared"
    assert receipt.settlement == "absent"


def test_only_b_resolved_lifts_quiescent_and_forbids_further_compensate() -> None:
    from coding_agent.harness.compensation import CompensationAdmissionError
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=81)
    ledger.seed_attempt(
        original_effect_id=40,
        compensation_effect_id=80,
        generation=3,
        state="completed",
        settlement="absent",
        session_id="session-7",
    )
    before = ledger.quiescent(original_effect_id=40)
    ledger.resolve_settlement(effect_id=80)
    after = ledger.quiescent(original_effect_id=40)
    error = ledger.capture_error(
        CompensationAdmissionError,
        ledger.compensate,
        fence=FENCE,
        original_effect_id=40,
        client_key="client-op-c2",
        generation=4,
    )

    assert before is True
    assert after is False
    assert error.code == "already_resolved"
    assert ledger.next_effect_id == 81


def test_admission_repair_and_c2_linearize_on_compensation_cas() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    ledger = CompensationLedger(first_effect_id=80)

    trace = ledger.run_serialized_operations(
        fence=FENCE,
        original_effect_id=40,
        operations=("admission_c1", "repair", "admission_c2"),
    )

    assert trace == (
        ("admission_c1", 0, 1, "admitted"),
        ("repair", 1, 2, "classified"),
        ("admission_c2", 2, 3, "admitted"),
    )
    assert ledger.compensation_cas(original_effect_id=40) == 3


def test_crash_repair_atomic_write_set_for_unobserved_failed_and_completed() -> None:
    from coding_agent.harness.compensation import CompensationLedger

    cases = (
        ("unobserved", "unknown", ()),
        ("observed_failed", "dispatched", (("attempt_a", "failed"),)),
        (
            "observed_completed",
            "dispatched",
            (("attempt_a", "completed"), ("settlement_b", "resolved")),
        ),
        (
            "observed_completed",
            "completed",
            (("settlement_b", "resolved"),),
        ),
    )

    for observation, initial_state, expected_writes in cases:
        ledger = CompensationLedger(first_effect_id=81)
        ledger.seed_attempt(
            original_effect_id=40,
            compensation_effect_id=80,
            generation=3,
            state=initial_state,
            settlement="absent",
            session_id="session-7",
        )

        result = ledger.repair(
            fence=FENCE,
            original_effect_id=40,
            observation=observation,
        )

        assert result.atomic_writes == expected_writes
        assert result.transaction_count == (0 if not expected_writes else 1)
        assert ledger.attempt_ids(original_effect_id=40) == (80,)
