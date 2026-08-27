"""Red contracts for the intended ``coding_agent.harness.bootstrap`` module."""

from __future__ import annotations


def test_first_session_allocator_starts_once_and_advances_monotonically() -> None:
    from coding_agent.harness.bootstrap import HarnessStore

    store = HarnessStore(first_session_number=1)

    first = store.allocate_session(owner_id="daemon-a")
    second = store.allocate_session(owner_id="daemon-a")
    replay = store.allocate_session(owner_id="daemon-a", request_id=first.request_id)

    assert first.session_id == "session-1"
    assert second.session_id == "session-2"
    assert replay.session_id == "session-1"
    assert store.next_session_number == 3


def test_first_session_uses_epoch_zero() -> None:
    from coding_agent.harness.bootstrap import HarnessStore

    store = HarnessStore(first_session_number=1)

    session = store.allocate_session(owner_id="daemon-a")
    fence = store.session_fence(session.session_id)

    assert fence.as_dict() == {
        "session_id": "session-1",
        "owner_id": "daemon-a",
        "epoch": 0,
    }
    assert store.projection_epoch(session.session_id) == 0


def test_authoritative_uow_commits_event_record_state_mailbox_effects_and_receipts() -> None:
    from coding_agent.harness.bootstrap import HarnessStore

    store = HarnessStore(first_session_number=1)
    session = store.allocate_session(owner_id="daemon-a")

    result = store.commit_authoritative_uow(
        fence={
            "session_id": session.session_id,
            "owner_id": "daemon-a",
            "epoch": 0,
        },
        event_record={"session_seq": 1, "kind": "approval_wait"},
        state={"run_id": "run-1", "status": "waiting_for_approval"},
        mailbox={"lane": "approval", "sequence": 1, "disposition": "pending"},
        effect={"effect_id": 40, "state": "prepared"},
        receipt={"client_key": "client-op-9", "effect_id": 40},
    )

    assert result.transaction_count == 1
    assert result.committed_tables == (
        "event_records",
        "session_run_state",
        "mailbox_dispositions",
        "effect_ledger",
        "operation_receipts",
    )
    assert store.event_record("session-1", 1).kind == "approval_wait"
    assert store.run_state("run-1").status == "waiting_for_approval"
    assert store.mailbox_item("approval", 1).disposition == "pending"
    assert store.effect(40).state == "prepared"
    assert store.receipt("client-op-9").effect_id == 40


def test_authoritative_uow_rolls_back_all_five_record_families_together() -> None:
    from coding_agent.harness.bootstrap import HarnessStore
    from coding_agent.harness.bootstrap import InjectedCommitFailure

    store = HarnessStore(first_session_number=1)
    session = store.allocate_session(owner_id="daemon-a")

    error = store.capture_error(
        InjectedCommitFailure,
        store.commit_authoritative_uow,
        fence={
            "session_id": session.session_id,
            "owner_id": "daemon-a",
            "epoch": 0,
        },
        event_record={"session_seq": 1, "kind": "approval_wait"},
        state={"run_id": "run-1", "status": "waiting_for_approval"},
        mailbox={"lane": "approval", "sequence": 1, "disposition": "pending"},
        effect={"effect_id": 40, "state": "prepared"},
        receipt={"client_key": "client-op-9", "effect_id": 40},
        fail_after="effect_ledger",
    )

    assert error.failed_after == "effect_ledger"
    assert store.table_counts() == {
        "event_records": 0,
        "session_run_state": 0,
        "mailbox_dispositions": 0,
        "effect_ledger": 0,
        "operation_receipts": 0,
    }


def test_authoritative_uow_rejects_stale_epoch_inside_transaction() -> None:
    from coding_agent.harness.bootstrap import HarnessStore
    from coding_agent.harness.bootstrap import StaleFenceError

    store = HarnessStore(first_session_number=1)
    session = store.allocate_session(owner_id="daemon-a")

    error = store.capture_error(
        StaleFenceError,
        store.commit_authoritative_uow,
        fence={
            "session_id": session.session_id,
            "owner_id": "daemon-a",
            "epoch": -1,
        },
        event_record={"session_seq": 1, "kind": "turn_started"},
        state=None,
        mailbox=None,
        effect=None,
        receipt=None,
    )

    assert error.code == "stale_epoch"
    assert error.rejected_inside == "authoritative_transaction"
    assert error.committed_mutation_count == 0
    assert store.table_counts() == {
        "event_records": 0,
        "session_run_state": 0,
        "mailbox_dispositions": 0,
        "effect_ledger": 0,
        "operation_receipts": 0,
    }


def test_authoritative_uow_rejects_cross_session_target_inside_transaction() -> None:
    from coding_agent.harness.bootstrap import HarnessStore
    from coding_agent.harness.bootstrap import TargetOwnershipError

    store = HarnessStore(first_session_number=1)
    authority_session = store.allocate_session(owner_id="daemon-a")
    target_session = store.allocate_session(owner_id="daemon-a")

    error = store.capture_error(
        TargetOwnershipError,
        store.commit_authoritative_uow,
        fence={
            "session_id": authority_session.session_id,
            "owner_id": "daemon-a",
            "epoch": 0,
        },
        event_record={
            "session_id": target_session.session_id,
            "session_seq": 1,
            "kind": "turn_started",
        },
        state=None,
        mailbox=None,
        effect=None,
        receipt=None,
    )

    assert error.code == "cross_session_target"
    assert error.rejected_inside == "authoritative_transaction"
    assert error.committed_mutation_count == 0
    assert store.table_counts() == {
        "event_records": 0,
        "session_run_state": 0,
        "mailbox_dispositions": 0,
        "effect_ledger": 0,
        "operation_receipts": 0,
    }


def test_jsonl_tape_is_derived_export_not_authoritative() -> None:
    from coding_agent.harness.bootstrap import HarnessStore
    from coding_agent.harness.bootstrap import NonAuthoritativeImportError

    store = HarnessStore(first_session_number=1)
    session = store.allocate_session(owner_id="daemon-a")
    store.commit_authoritative_uow(
        fence={
            "session_id": session.session_id,
            "owner_id": "daemon-a",
            "epoch": 0,
        },
        event_record={"session_seq": 1, "kind": "turn_started"},
        state={"run_id": "run-1", "status": "running"},
        mailbox=None,
        effect=None,
        receipt=None,
    )

    exported = store.export_jsonl(session_id="session-1")
    error = store.capture_error(
        NonAuthoritativeImportError,
        store.restore_from_jsonl,
        session_id="session-1",
        jsonl='{"session_seq": "99", "kind": "forged"}\n',
    )

    assert exported == (
        '{"kind":"turn_started","session_id":"session-1","session_seq":"1"}\n'
    )
    assert error.code == "jsonl_is_derived_export"
    assert store.last_session_seq("session-1") == 1
    assert store.event_record("session-1", 1).kind == "turn_started"
