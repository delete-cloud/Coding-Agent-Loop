from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentkit.runtime.contracts import (
    AppliedCommandDisposition,
    EffectMutation,
    EffectPlan,
    EffectStatus,
    OperationStateCAS,
    RejectedCommandDisposition,
    RuntimeCommand,
    SupersededCommandDisposition,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    CommandDispositionConflictError,
    EffectLedgerSlot,
    EventRecord,
    InvalidDispatchAuthorizationError,
    MailboxDispositionSlot,
    StaleMailboxCutError,
    StateVersionConflictError,
    TransitionFingerprintMismatchError,
    transition_mutation_fingerprint,
)

SESSION_ID = "session-phase-b"
OWNER_ID = "owner-phase-b"
SESSION_STATE = {
    "id": SESSION_ID,
    "session_id": SESSION_ID,
    "tape_id": None,
    "status": "active",
}
STAMP = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


async def _open_store(tmp_path: Path) -> tuple[SQLiteLocalDurableStore, object]:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner = await store.acquire_owner(SESSION_ID, OWNER_ID)
    await store.save_session(owner, SESSION_STATE)
    return store, owner


def _fact(suffix: str, *, kind: str = "assistant_message") -> EventRecord:
    return EventRecord(
        event_id=f"fact-{suffix}",
        session_id=SESSION_ID,
        event_kind=kind,
        payload={"suffix": suffix},
        created_at=STAMP,
    )


def _transition(
    transition_id: str,
    *,
    revision: int,
    facts: tuple[EventRecord, ...] = (),
    state_value: dict[str, object] | None = None,
    dispositions: tuple[object, ...] = (),
    effect_mutation: EffectMutation | None = None,
    expected_mailbox_cut: str | None = None,
) -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=None,
        session_state={**SESSION_STATE, "phase": transition_id},
        transition_id=transition_id,
        state_cas=OperationStateCAS(
            run_id="run-phase-b",
            revision=revision,
            projection_epoch=0,
        ),
        state_value=(
            {"transition": transition_id} if state_value is None else state_value
        ),
        facts=facts,
        dispositions=dispositions,
        effect_mutation=effect_mutation,
        expected_mailbox_cut=expected_mailbox_cut,
    )


def _prepared_effect(effect_id: str = "effect-phase-b") -> EffectMutation:
    return EffectMutation.prepare(
        EffectPlan(
            effect_id=effect_id,
            attempt_id=f"attempt-{effect_id}",
            effect_kind="tool",
            payload={"name": "read", "arguments": {"path": "README.md"}},
        )
    )


def _dispatched_effect(effect_id: str = "effect-phase-b") -> EffectMutation:
    return EffectMutation(
        effect_id=effect_id,
        attempt_id=f"attempt-{effect_id}",
        expected_status=EffectStatus.PREPARED,
        status=EffectStatus.DISPATCHED,
        payload={"authorization_transition_id": "authorization-phase-b"},
    )


def _seed_commands(
    store: SQLiteLocalDurableStore,
    *command_ids: str,
    disposition: str = "pending",
) -> None:
    with store._connect() as connection:
        connection.executemany(
            """
            INSERT INTO session_mailbox_slots (
                session_id, slot_id, lane, disposition, payload
            )
            VALUES (?, ?, 'runtime', ?, '{}')
            """,
            [(SESSION_ID, command_id, disposition) for command_id in command_ids],
        )


def test_dispatch_authorization_requires_mailbox_cut() -> None:
    with pytest.raises(
        InvalidDispatchAuthorizationError,
        match="expected_mailbox_cut is required",
    ):
        _transition(
            "transition-dispatch-missing-cut",
            revision=0,
            effect_mutation=_dispatched_effect(),
        )


@pytest.mark.parametrize(
    "expected_mailbox_cut",
    ["", "-1", "01", str(2**64)],
)
def test_dispatch_authorization_rejects_invalid_mailbox_cut(
    expected_mailbox_cut: str,
) -> None:
    with pytest.raises(InvalidDispatchAuthorizationError):
        _transition(
            "transition-dispatch-invalid-cut",
            revision=0,
            effect_mutation=_dispatched_effect(),
            expected_mailbox_cut=expected_mailbox_cut,
        )


def test_mailbox_cut_is_forbidden_outside_dispatch_authorization() -> None:
    with pytest.raises(
        InvalidDispatchAuthorizationError,
        match="expected_mailbox_cut is forbidden",
    ):
        _transition(
            "transition-nondispatch-cut",
            revision=0,
            expected_mailbox_cut="0",
        )


def test_dispatch_authorization_cut_changes_mutation_fingerprint() -> None:
    first = _transition(
        "transition-dispatch-fingerprint",
        revision=0,
        effect_mutation=_dispatched_effect(),
        expected_mailbox_cut="0",
    )
    second = _transition(
        "transition-dispatch-fingerprint",
        revision=0,
        effect_mutation=_dispatched_effect(),
        expected_mailbox_cut="1",
    )

    assert transition_mutation_fingerprint(first) != (
        transition_mutation_fingerprint(second)
    )


@pytest.mark.asyncio
async def test_uow_commits_state_facts_dispositions_and_effect_ledger_atomically_sqlite(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    _seed_commands(store, "command-applied", "command-rejected")
    _seed_commands(store, "command-old", disposition="admitted")
    unit = _transition(
        "transition-1",
        revision=0,
        facts=(
            _fact("assistant"),
            _fact("thinking", kind="finalized_thinking"),
        ),
        dispositions=(
            AppliedCommandDisposition(command_id="command-applied"),
            RejectedCommandDisposition(
                command_id="command-rejected",
                reason_code="not_applicable",
            ),
            SupersededCommandDisposition(
                command_id="command-old",
                superseded_by_command_id="command-new",
            ),
        ),
        effect_mutation=_prepared_effect(),
    )

    committed = await store.commit_authoritative_uow(owner, unit)

    assert committed.state_version is not None
    assert (
        await store.load_operation_state(SESSION_ID, "run-phase-b")
        == committed.state_version
    )
    assert (
        await store.load_transition_receipt(SESSION_ID, 0, "transition-1")
        == committed.transition_receipt
    )
    assert committed.state_version.revision == 1
    assert committed.state_version.commit_ref.transition_id == "transition-1"
    assert committed.state_version.commit_ref.fact_seq_start == "1"
    assert committed.state_version.commit_ref.fact_seq_end == "2"
    assert [fact.session_seq for fact in committed.facts] == ["1", "2"]
    assert committed.transition_receipt is not None
    assert committed.transition_receipt.state_version == committed.state_version
    assert (await store.load_event_record(SESSION_ID, "2")).event_kind == (
        "finalized_thinking"
    )
    rejected = await store.load_mailbox_slot(SESSION_ID, "command-rejected")
    assert rejected is not None
    assert rejected.disposition == "rejected"
    assert rejected.payload == {"reason_code": "not_applicable"}
    superseded = await store.load_mailbox_slot(SESSION_ID, "command-old")
    assert superseded is not None
    assert superseded.payload == {"superseded_by_command_id": "command-new"}
    effect = await store.load_effect_slot(SESSION_ID, "effect-phase-b")
    assert effect is not None
    assert effect.status == "prepared"


@pytest.mark.asyncio
async def test_uow_commits_finalized_thinking_event_record_with_transition(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)

    committed = await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-thinking",
            revision=0,
            facts=(_fact("final-thinking", kind="finalized_thinking"),),
        ),
    )

    assert committed.facts[0].event_kind == "finalized_thinking"
    stored = await store.load_event_record(SESSION_ID, "1")
    assert stored == committed.facts[0]


@pytest.mark.asyncio
async def test_cas_conflict_or_failure_writes_no_part_of_transition(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    await store.commit_authoritative_uow(
        owner,
        _transition("transition-base", revision=0, facts=(_fact("base"),)),
    )
    conflict = _transition(
        "transition-conflict",
        revision=0,
        facts=(_fact("conflict"),),
        dispositions=(AppliedCommandDisposition(command_id="command-conflict"),),
        effect_mutation=_prepared_effect("effect-conflict"),
    )

    with pytest.raises(StateVersionConflictError):
        await store.commit_authoritative_uow(owner, conflict)

    assert await store.load_event_record(SESSION_ID, "2") is None
    assert await store.load_mailbox_slot(SESSION_ID, "command-conflict") is None
    assert await store.load_effect_slot(SESSION_ID, "effect-conflict") is None

    duplicate_fact = _fact("duplicate")
    _seed_commands(store, "command-failure")
    failed = _transition(
        "transition-failure",
        revision=1,
        facts=(duplicate_fact, duplicate_fact),
        dispositions=(AppliedCommandDisposition(command_id="command-failure"),),
        effect_mutation=_prepared_effect("effect-failure"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        await store.commit_authoritative_uow(owner, failed)

    assert await store.load_event_record(SESSION_ID, "2") is None
    failed_command = await store.load_mailbox_slot(
        SESSION_ID,
        "command-failure",
    )
    assert failed_command is not None
    assert failed_command.disposition == "pending"
    assert await store.load_effect_slot(SESSION_ID, "effect-failure") is None
    state = await store.load_operation_state(SESSION_ID, "run-phase-b")
    assert state is not None
    assert state.revision == 1


@pytest.mark.asyncio
async def test_host_allocates_continuous_session_sequences(tmp_path: Path) -> None:
    store, owner = await _open_store(tmp_path)

    committed = await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-contiguous",
            revision=0,
            facts=(_fact("one"), _fact("two"), _fact("three")),
        ),
    )

    assert [fact.session_seq for fact in committed.facts] == ["1", "2", "3"]
    assert committed.state_version is not None
    assert committed.state_version.commit_ref.fact_seq_start == "1"
    assert committed.state_version.commit_ref.fact_seq_end == "3"


@pytest.mark.asyncio
async def test_commit_ref_is_transition_anchor_not_session_log_head(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    transition = await store.commit_authoritative_uow(
        owner,
        _transition("transition-anchor", revision=0, facts=(_fact("anchor"),)),
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=_fact("mailbox-admission", kind="command_admitted"),
            session_state=SESSION_STATE,
            mailbox=MailboxDispositionSlot(
                slot_id="command-pending",
                lane="control",
                disposition="pending",
            ),
        ),
    )

    assert transition.state_version is not None
    assert transition.state_version.commit_ref.fact_seq_end == "1"
    fact_source = await store.load_session_fact_source(SESSION_ID)
    assert fact_source is not None
    assert fact_source.session_seq == "2"


@pytest.mark.asyncio
async def test_mailbox_admission_advances_session_seq_without_engine_revision(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    first = await store.commit_authoritative_uow(
        owner,
        _transition("transition-before-mailbox", revision=0, facts=(_fact("one"),)),
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=_fact("admitted", kind="command_admitted"),
            session_state=SESSION_STATE,
        ),
    )
    second = await store.commit_authoritative_uow(
        owner,
        _transition("transition-after-mailbox", revision=1, facts=(_fact("three"),)),
    )

    assert first.state_version is not None
    assert first.state_version.revision == 1
    assert second.state_version is not None
    assert second.state_version.revision == 2
    assert second.state_version.commit_ref.fact_seq_start == "3"


@pytest.mark.asyncio
async def test_transition_receipt_is_first_write_wins_keyed_by_session_projection_epoch_and_transition_id(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    original = _transition(
        "transition-receipt",
        revision=0,
        facts=(_fact("receipt"),),
    )
    first = await store.commit_authoritative_uow(owner, original)

    replay = await store.commit_authoritative_uow(owner, original)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version
    assert replay.facts == first.facts
    assert replay.transition_receipt == first.transition_receipt
    with pytest.raises(TransitionFingerprintMismatchError):
        await store.commit_authoritative_uow(
            owner,
            _transition(
                "transition-receipt",
                revision=0,
                facts=(_fact("receipt"),),
                state_value={"transition": "changed"},
            ),
        )
    assert await store.load_event_record(SESSION_ID, "2") is None


@pytest.mark.asyncio
async def test_transition_receipt_same_epoch_retry_returns_stored_commit_before_cas_and_before_any_write_sqlite(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    original = _transition(
        "transition-original",
        revision=0,
        facts=(_fact("original"),),
    )
    first = await store.commit_authoritative_uow(owner, original)
    later = await store.commit_authoritative_uow(
        owner,
        _transition("transition-later", revision=1, facts=(_fact("later"),)),
    )

    replay = await store.commit_authoritative_uow(owner, original)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version
    assert replay.facts == first.facts
    assert later.state_version is not None
    assert later.state_version.revision == 2
    assert await store.load_event_record(SESSION_ID, "3") is None


@pytest.mark.asyncio
async def test_stale_dispatch_authorization_writes_nothing_sqlite(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    effect_id = "effect-dispatch-cut"
    await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispatch-prepare",
            revision=0,
            effect_mutation=_prepared_effect(effect_id),
        ),
    )
    first_cancel = await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="cancel-before-dispatch",
            command_kind="cancel",
            payload={},
        ),
    )
    assert first_cancel.mailbox_cut == "1"
    stale = _transition(
        "transition-dispatch-authorize",
        revision=1,
        effect_mutation=_dispatched_effect(effect_id),
        expected_mailbox_cut="0",
    )

    with pytest.raises(StaleMailboxCutError) as stale_error:
        await store.commit_authoritative_uow(owner, stale)

    assert stale_error.value.expected_mailbox_cut == 0
    assert stale_error.value.current_mailbox_cut == 1
    state_after_stale = await store.load_operation_state(
        SESSION_ID,
        "run-phase-b",
    )
    assert state_after_stale is not None
    assert state_after_stale.revision == 1
    effect_after_stale = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect_after_stale is not None
    assert effect_after_stale.status == "prepared"
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "transition-dispatch-authorize",
        )
        is None
    )

    authorized = _transition(
        "transition-dispatch-authorize",
        revision=1,
        effect_mutation=_dispatched_effect(effect_id),
        expected_mailbox_cut="1",
    )
    first = await store.commit_authoritative_uow(owner, authorized)
    second_cancel = await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="cancel-after-dispatch",
            command_kind="cancel",
            payload={},
        ),
    )
    assert second_cancel.mailbox_cut == "2"

    replay = await store.commit_authoritative_uow(owner, authorized)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version
    effect_after_replay = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect_after_replay is not None
    assert effect_after_replay.status == "dispatched"


@pytest.mark.asyncio
async def test_dispatch_authorization_exact_replay_precedes_newer_cut_sqlite(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    effect_id = "effect-dispatch-replay"
    await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispatch-replay-prepare",
            revision=0,
            effect_mutation=_prepared_effect(effect_id),
        ),
    )
    authorized = _transition(
        "transition-dispatch-replay-authorize",
        revision=1,
        effect_mutation=_dispatched_effect(effect_id),
        expected_mailbox_cut="0",
    )
    first = await store.commit_authoritative_uow(owner, authorized)
    await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="cancel-after-replay-authorization",
            command_kind="cancel",
            payload={},
        ),
    )

    replay = await store.commit_authoritative_uow(owner, authorized)

    assert replay.idempotent is True
    assert replay.state_version == first.state_version


@pytest.mark.asyncio
async def test_stale_dispatch_authorization_writes_no_receipt(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    effect_id = "effect-dispatch-no-receipt"
    await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispatch-no-receipt-prepare",
            revision=0,
            effect_mutation=_prepared_effect(effect_id),
        ),
    )
    await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="cancel-before-no-receipt-authorization",
            command_kind="cancel",
            payload={},
        ),
    )
    stale = _transition(
        "transition-dispatch-no-receipt-authorize",
        revision=1,
        effect_mutation=_dispatched_effect(effect_id),
        expected_mailbox_cut="0",
    )

    with pytest.raises(StaleMailboxCutError):
        await store.commit_authoritative_uow(owner, stale)

    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "transition-dispatch-no-receipt-authorize",
        )
        is None
    )


@pytest.mark.asyncio
async def test_fresh_cut_can_commit_after_stale_zero_write_refusal(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    effect_id = "effect-dispatch-fresh-cut"
    await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispatch-fresh-cut-prepare",
            revision=0,
            effect_mutation=_prepared_effect(effect_id),
        ),
    )
    admission = await store.admit_runtime_command(
        owner,
        RuntimeCommand(
            command_id="cancel-before-fresh-cut-authorization",
            command_kind="cancel",
            payload={},
        ),
    )
    stale = _transition(
        "transition-dispatch-fresh-cut-authorize",
        revision=1,
        effect_mutation=_dispatched_effect(effect_id),
        expected_mailbox_cut="0",
    )
    with pytest.raises(StaleMailboxCutError):
        await store.commit_authoritative_uow(owner, stale)

    committed = await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispatch-fresh-cut-authorize",
            revision=1,
            effect_mutation=_dispatched_effect(effect_id),
            expected_mailbox_cut=admission.mailbox_cut,
        ),
    )

    assert committed.idempotent is False
    effect = await store.load_effect_slot(SESSION_ID, effect_id)
    assert effect is not None
    assert effect.status == "dispatched"


@pytest.mark.asyncio
async def test_transition_receipt_fingerprint_mismatch_fails_deterministically(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    await store.commit_authoritative_uow(
        owner,
        _transition("transition-fingerprint", revision=0),
    )

    with pytest.raises(
        TransitionFingerprintMismatchError,
        match="transition mutation fingerprint mismatch",
    ):
        await store.commit_authoritative_uow(
            owner,
            _transition(
                "transition-fingerprint",
                revision=0,
                state_value={"different": True},
            ),
        )


@pytest.mark.asyncio
async def test_transition_receipt_retry_after_later_transition_writes_nothing(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    original = _transition(
        "transition-retry",
        revision=0,
        facts=(_fact("retry"),),
    )
    await store.commit_authoritative_uow(owner, original)
    later = await store.commit_authoritative_uow(
        owner,
        _transition("transition-later", revision=1, facts=(_fact("later"),)),
    )

    replay = await store.commit_authoritative_uow(owner, original)

    assert replay.idempotent is True
    assert later.state_version is not None
    assert later.state_version.revision == 2
    assert await store.load_event_record(SESSION_ID, "3") is None


@pytest.mark.asyncio
async def test_transition_receipt_retry_after_epoch_change_writes_nothing(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)
    original = _transition(
        "transition-old-epoch",
        revision=0,
        facts=(_fact("old-epoch"),),
    )
    await store.commit_authoritative_uow(owner, original)
    with store._connect() as connection:  # simulate the restore-owned epoch change
        connection.execute(
            "UPDATE session_fact_source SET projection_epoch = 1 WHERE session_id = ?",
            (SESSION_ID,),
        )

    with pytest.raises(StateVersionConflictError, match="projection epoch"):
        await store.commit_authoritative_uow(owner, original)

    assert await store.load_event_record(SESSION_ID, "2") is None


@pytest.mark.asyncio
async def test_phase_b_keeps_settled_alias_while_live_writers_remain(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(tmp_path)

    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=_fact("legacy-settled"),
            session_state=SESSION_STATE,
            effect=EffectLedgerSlot(
                effect_id="legacy-effect",
                status="settled",
                payload={"approved": True},
            ),
        ),
    )

    effect = await store.load_effect_slot(SESSION_ID, "legacy-effect")
    assert effect is not None
    assert effect.status == "settled"


@pytest.mark.asyncio
async def test_dispositions_commit_atomically_with_transition(tmp_path: Path) -> None:
    store, owner = await _open_store(tmp_path)
    _seed_commands(store, "command-applied", "command-rejected")

    committed = await store.commit_authoritative_uow(
        owner,
        _transition(
            "transition-dispositions",
            revision=0,
            dispositions=(
                AppliedCommandDisposition(command_id="command-applied"),
                RejectedCommandDisposition(
                    command_id="command-rejected",
                    reason_code="invalid_for_state",
                ),
            ),
        ),
    )

    assert committed.state_version is not None
    assert committed.state_version.revision == 1
    applied = await store.load_mailbox_slot(SESSION_ID, "command-applied")
    rejected = await store.load_mailbox_slot(SESSION_ID, "command-rejected")
    assert applied is not None
    assert applied.disposition == "applied"
    assert rejected is not None
    assert rejected.disposition == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_disposition",
    [None, "applied", "rejected", "superseded"],
)
async def test_sqlite_disposition_requires_pending_admitted_command_and_rolls_back(
    tmp_path: Path,
    durable_disposition: str | None,
) -> None:
    store, owner = await _open_store(tmp_path)
    if durable_disposition is not None:
        _seed_commands(
            store,
            "command-target",
            disposition=durable_disposition,
        )
    fact_source_before = await store.load_session_fact_source(SESSION_ID)

    with pytest.raises(CommandDispositionConflictError):
        await store.commit_authoritative_uow(
            owner,
            _transition(
                "transition-invalid-disposition",
                revision=0,
                facts=(_fact("invalid-disposition"),),
                dispositions=(AppliedCommandDisposition(command_id="command-target"),),
                effect_mutation=_prepared_effect("effect-invalid-disposition"),
            ),
        )

    assert await store.load_operation_state(SESSION_ID, "run-phase-b") is None
    assert await store.load_event_record(SESSION_ID, "1") is None
    assert await store.load_session_fact_source(SESSION_ID) == fact_source_before
    assert (
        await store.load_effect_slot(SESSION_ID, "effect-invalid-disposition") is None
    )
    assert (
        await store.load_transition_receipt(
            SESSION_ID,
            0,
            "transition-invalid-disposition",
        )
        is None
    )
    mailbox = await store.load_mailbox_slot(SESSION_ID, "command-target")
    if durable_disposition is None:
        assert mailbox is None
    else:
        assert mailbox is not None
        assert mailbox.disposition == durable_disposition


@pytest.mark.asyncio
async def test_sqlite_phase_b_schema_upgrade_preserves_existing_store(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.sqlite3"
    store = SQLiteLocalDurableStore(path)
    owner = await store.acquire_owner(SESSION_ID, OWNER_ID)
    await store.save_session(owner, SESSION_STATE)
    with store._connect() as connection:
        connection.execute("DROP TABLE session_operation_states")
        connection.execute("DROP TABLE session_transition_receipts")

    reopened = SQLiteLocalDurableStore(path)

    assert reopened.load_session(SESSION_ID) == SESSION_STATE
    with reopened._connect() as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'session_%'
                """
            ).fetchall()
        }
    assert "session_operation_states" in table_names
    assert "session_transition_receipts" in table_names


@pytest.mark.asyncio
async def test_sqlite_d3a_schema_upgrade_preserves_legacy_mailbox_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.sqlite3"
    store, owner = await _open_store(tmp_path)
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=_fact("legacy-mailbox", kind="command_admitted"),
            session_state=SESSION_STATE,
            mailbox=MailboxDispositionSlot(
                slot_id="turn:legacy",
                lane="turn",
                disposition="settled",
                payload={"legacy": True},
            ),
        ),
    )
    with store._connect() as connection:
        connection.execute(
            "ALTER TABLE session_fact_source DROP COLUMN dispatch_generation"
        )
        connection.execute(
            "ALTER TABLE session_mailbox_slots DROP COLUMN admitted_session_seq"
        )
        connection.execute(
            "ALTER TABLE session_mailbox_slots DROP COLUMN admitted_dispatch_generation"
        )

    reopened = SQLiteLocalDurableStore(path)

    fact_source = await reopened.load_session_fact_source(SESSION_ID)
    legacy_slot = await reopened.load_mailbox_slot(SESSION_ID, "turn:legacy")
    assert fact_source is not None
    assert fact_source.dispatch_generation == "0"
    assert legacy_slot is not None
    assert legacy_slot.payload == {"legacy": True}
    with reopened._connect() as connection:
        fact_source_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(session_fact_source)"
            ).fetchall()
        }
        mailbox_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(session_mailbox_slots)"
            ).fetchall()
        }
    assert "dispatch_generation" in fact_source_columns
    assert {
        "admitted_session_seq",
        "admitted_dispatch_generation",
    } <= mailbox_columns
