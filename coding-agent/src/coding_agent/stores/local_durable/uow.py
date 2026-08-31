"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

import json
from dataclasses import replace

from agentkit.runtime.contracts import (
    CommitRef,
    CommittedFactNotice,
    OperationStateVersion,
    RuntimeCommand,
    TransitionReceipt,
)

from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    RootRunAlreadySettledError,
    TurnInProgressError,
    build_chat_admission,
    build_root_settlement,
)
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    CommandMailboxEntry,
    CommandDispositionConflictError,
    RuntimeCommandAdmission,
    RuntimeCommandAdmissionConflictError,
    EventRecord,
    EffectMutationConflictError,
    JSONObject,
    RawCursor,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _json_object_from_sql,
    _json_to_sql,
    effect_status_may_replace,
    format_u64,
    runtime_command_from_mailbox_payload,
    runtime_command_invalidates_dispatch,
    runtime_command_mailbox_payload,
    runtime_command_mailbox_payloads_equal,
    parse_u64,
    StateVersionConflictError,
    _sqlite_required_int,
    _sqlite_required_str,
    TransitionFingerprintMismatchError,
    _operation_state_from_sqlite_row,
    _plain_json,
    _transition_receipt_from_sqlite_row,
    effect_slot_from_mutation,
    mailbox_slot_from_disposition,
    transition_commit_from_payload,
    transition_commit_payload,
    transition_mutation_fingerprint,
    snapshot_transition_unit,
    receipt_generation_may_replace,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.fact_source_rows import (
    _event_record_from_sqlite_row,
)
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
)


class LocalUnitOfWorkMixin:
    async def settle_root_run(
        self,
        authority: OwnerAuthority,
        *,
        run_id: str,
        outcome: str,
        result: str | None,
        error: str | None,
        result_payload: JSONObject | None = None,
        extra_metadata: JSONObject | None = None,
    ) -> AuthoritativeCommit:
        from coding_agent.stores.runtime_store import SQLiteRuntimeStore

        run = await SQLiteRuntimeStore(self._path).load_agent_run(run_id)
        if run is None or run.session_id != authority.session_id:
            raise KeyError(f"root run not found: {run_id}")
        session = self.load_session(authority.session_id)
        if session is None:
            raise KeyError(f"session not found: {authority.session_id}")
        unit = build_root_settlement(
            run=run,
            session_state=session,
            outcome=outcome,
            result=result,
            error=error,
            result_payload=result_payload,
            extra_metadata=extra_metadata,
        )
        return await self.commit_authoritative_uow(authority, unit)

    async def admit_chat_command(
        self,
        authority: OwnerAuthority,
        *,
        prompt: str,
        command_id: str,
        parent_run_id: str | None,
        session_state: dict[str, object],
    ) -> ChatCommandAdmission:
        unit, admission = build_chat_admission(
            session_id=authority.session_id,
            prompt=prompt,
            command_id=command_id,
            parent_run_id=parent_run_id,
            session_state=session_state,
        )
        commit = await self.commit_authoritative_uow(authority, unit)
        return replace(
            admission,
            session_seq=commit.event.session_seq,
            idempotent=commit.idempotent,
        )

    async def admit_runtime_command(
        self,
        authority: OwnerAuthority,
        command: RuntimeCommand,
    ) -> RuntimeCommandAdmission:
        command_payload = runtime_command_mailbox_payload(command)
        invalidates_dispatch = runtime_command_invalidates_dispatch(command)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(connection, authority.session_id)
            existing = connection.execute(
                """
                SELECT * FROM session_mailbox_slots
                WHERE session_id = ? AND slot_id = ?
                """,
                (authority.session_id, command.command_id),
            ).fetchone()
            if existing is not None:
                admitted_session_seq = existing["admitted_session_seq"]
                admitted_generation = existing["admitted_dispatch_generation"]
                if admitted_session_seq is None or admitted_generation is None:
                    raise RuntimeCommandAdmissionConflictError(
                        "runtime command identity collides with a legacy mailbox slot"
                    )
                existing_payload = _json_object_from_sql(
                    existing["payload"],
                    context="runtime command mailbox slot",
                )
                if not runtime_command_mailbox_payloads_equal(
                    existing_payload,
                    command_payload,
                ):
                    raise RuntimeCommandAdmissionConflictError(
                        "runtime command identity was reused with different content"
                    )
                existing_command = runtime_command_from_mailbox_payload(
                    command_id=command.command_id,
                    payload=existing_payload,
                )
                return RuntimeCommandAdmission(
                    entry=CommandMailboxEntry(
                        command=existing_command,
                        admitted_session_seq=format_u64(
                            _sqlite_required_int(
                                existing,
                                "admitted_session_seq",
                                context="runtime command mailbox slot",
                            )
                        ),
                        admitted_dispatch_generation=format_u64(
                            _sqlite_required_int(
                                existing,
                                "admitted_dispatch_generation",
                                context="runtime command mailbox slot",
                            )
                        ),
                        disposition=_sqlite_required_str(
                            existing,
                            "disposition",
                            context="runtime command mailbox slot",
                        ),
                    ),
                    mailbox_cut=format_u64(fact_source.dispatch_generation_int),
                    idempotent=True,
                )

            admitted_session_seq = fact_source.session_seq_int + 1
            dispatch_generation = fact_source.dispatch_generation_int + int(
                invalidates_dispatch
            )
            connection.execute(
                """
                UPDATE session_fact_source
                SET session_seq = ?, dispatch_generation = ?
                WHERE session_id = ?
                """,
                (
                    admitted_session_seq,
                    dispatch_generation,
                    authority.session_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO session_mailbox_slots (
                    session_id,
                    slot_id,
                    lane,
                    disposition,
                    admitted_session_seq,
                    admitted_dispatch_generation,
                    payload
                )
                VALUES (?, ?, 'runtime', 'pending', ?, ?, ?)
                """,
                (
                    authority.session_id,
                    command.command_id,
                    admitted_session_seq,
                    dispatch_generation,
                    _json_to_sql(command_payload),
                ),
            )
            return RuntimeCommandAdmission(
                entry=CommandMailboxEntry(
                    command=command,
                    admitted_session_seq=format_u64(admitted_session_seq),
                    admitted_dispatch_generation=format_u64(dispatch_generation),
                    disposition="pending",
                ),
                mailbox_cut=format_u64(dispatch_generation),
            )

    async def load_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> OperationStateVersion | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_operation_states
                WHERE session_id = ? AND run_id = ?
                """,
                (session_id, run_id),
            ).fetchone()
        return None if row is None else _operation_state_from_sqlite_row(row)

    async def load_transition_receipt(
        self,
        session_id: str,
        projection_epoch: int,
        transition_id: str,
    ) -> TransitionReceipt | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("transition_id", transition_id)
        if (
            isinstance(projection_epoch, bool)
            or not isinstance(projection_epoch, int)
            or projection_epoch < 0
        ):
            raise ValueError("projection_epoch must be a non-negative integer")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_transition_receipts
                WHERE session_id = ? AND projection_epoch = ? AND transition_id = ?
                """,
                (session_id, projection_epoch, transition_id),
            ).fetchone()
        if row is None:
            return None
        fingerprint, result_payload = _transition_receipt_from_sqlite_row(row)
        _, _, receipt, _ = transition_commit_from_payload(
            session_id=session_id,
            transition_id=transition_id,
            projection_epoch=projection_epoch,
            mutation_fingerprint=fingerprint,
            payload=result_payload,
        )
        return receipt

    async def _commit_transition(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        state_cas = unit.state_cas
        transition_id = unit.transition_id
        state_value = unit.state_value
        if state_cas is None or transition_id is None or state_value is None:
            raise ValueError("typed transition is incomplete")
        for fact in unit.facts:
            if fact.session_id != authority.session_id:
                raise SessionOwnershipConflictError(
                    "transition fact belongs to another session"
                )
        reconciliation = (
            None
            if unit.effect_mutation is None
            else unit.effect_mutation.reconciliation
        )
        if reconciliation is not None and reconciliation.owner_epoch != authority.epoch:
            raise SessionOwnershipConflictError(
                "reconciliation owner epoch does not match authority"
            )
        mutation_fingerprint = transition_mutation_fingerprint(unit)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact_source = self._ensure_fact_source(connection, authority.session_id)
            if state_cas.projection_epoch != fact_source.projection_epoch_int:
                raise StateVersionConflictError(
                    "operation state projection epoch is stale"
                )
            receipt_row = connection.execute(
                """
                SELECT * FROM session_transition_receipts
                WHERE session_id = ? AND projection_epoch = ? AND transition_id = ?
                """,
                (
                    authority.session_id,
                    state_cas.projection_epoch,
                    transition_id,
                ),
            ).fetchone()
            if receipt_row is not None:
                stored_fingerprint, result_payload = (
                    _transition_receipt_from_sqlite_row(receipt_row)
                )
                if stored_fingerprint != mutation_fingerprint:
                    raise TransitionFingerprintMismatchError(
                        "transition mutation fingerprint mismatch"
                    )
                state_version, facts, receipt, raw_cursor = (
                    transition_commit_from_payload(
                        session_id=authority.session_id,
                        transition_id=transition_id,
                        projection_epoch=state_cas.projection_epoch,
                        mutation_fingerprint=stored_fingerprint,
                        payload=result_payload,
                    )
                )
                return AuthoritativeCommit(
                    event=None,
                    projection=fact_source.projection,
                    projection_epoch=format_u64(fact_source.projection_epoch_int),
                    raw_cursor=raw_cursor,
                    idempotent=True,
                    state_version=state_version,
                    facts=facts,
                    transition_receipt=receipt,
                )

            current_state_row = connection.execute(
                """
                SELECT * FROM session_operation_states
                WHERE session_id = ? AND run_id = ?
                """,
                (authority.session_id, state_cas.run_id),
            ).fetchone()
            if current_state_row is None:
                if state_cas.revision != 0:
                    raise StateVersionConflictError("operation state revision is stale")
            else:
                current_state = _operation_state_from_sqlite_row(current_state_row)
                if current_state.cas != state_cas:
                    raise StateVersionConflictError(
                        "operation state compare-and-swap conflict"
                    )

            disposition_slots = {}
            for disposition in unit.dispositions:
                slot = mailbox_slot_from_disposition(disposition)
                existing_mailbox = connection.execute(
                    """
                    SELECT lane, disposition, payload FROM session_mailbox_slots
                    WHERE session_id = ? AND slot_id = ?
                    """,
                    (authority.session_id, slot.slot_id),
                ).fetchone()
                if existing_mailbox is None:
                    raise CommandDispositionConflictError(
                        "command disposition requires an admitted mailbox row"
                    )
                if existing_mailbox["disposition"] not in {"pending", "admitted"}:
                    raise CommandDispositionConflictError(
                        "command mailbox row is already terminal"
                    )
                existing_payload = _json_object_from_sql(
                    existing_mailbox["payload"],
                    context="mailbox slot",
                )
                disposition_slots[slot.slot_id] = replace(
                    slot,
                    lane=existing_mailbox["lane"],
                    payload={**existing_payload, **slot.payload},
                )

            effect_slot = (
                None
                if unit.effect_mutation is None
                else effect_slot_from_mutation(unit.effect_mutation)
            )
            if unit.effect_mutation is not None:
                current_effect = connection.execute(
                    """
                    SELECT status, payload FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, unit.effect_mutation.effect_id),
                ).fetchone()
                expected_status = unit.effect_mutation.expected_status
                if expected_status is None:
                    if current_effect is not None:
                        raise EffectMutationConflictError("effect already exists")
                elif current_effect is None:
                    raise EffectMutationConflictError("effect does not exist")
                else:
                    current_payload = _json_object_from_sql(
                        current_effect["payload"],
                        context="effect slot",
                    )
                    if current_effect["status"] != expected_status.value:
                        raise EffectMutationConflictError(
                            "effect status precondition failed"
                        )
                    if (
                        current_payload.get("attempt_id")
                        != unit.effect_mutation.attempt_id
                    ):
                        raise EffectMutationConflictError(
                            "effect attempt precondition failed"
                        )

                    if effect_slot is None:
                        raise RuntimeError("effect mutation slot was not built")
                    effect_slot = replace(
                        effect_slot,
                        payload={**current_payload, **effect_slot.payload},
                    )
            first_seq = fact_source.session_seq_int + 1 if unit.facts else None
            committed_facts: list[EventRecord] = []
            for offset, fact in enumerate(unit.facts):
                session_seq = fact_source.session_seq_int + offset + 1
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        session_seq,
                        fact.event_id,
                        fact.event_kind,
                        _json_to_sql(fact.payload),
                        _datetime_to_json(fact.created_at),
                        fact_source.projection_epoch_int,
                    ),
                )
                committed_facts.append(
                    EventRecord(
                        event_id=fact.event_id,
                        session_id=authority.session_id,
                        event_kind=fact.event_kind,
                        payload=fact.payload,
                        created_at=fact.created_at,
                        session_seq=format_u64(session_seq),
                        projection_epoch=format_u64(fact_source.projection_epoch_int),
                    )
                )
            last_seq = (
                fact_source.session_seq_int + len(committed_facts)
                if committed_facts
                else None
            )
            if last_seq is not None:
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (last_seq, authority.session_id),
                )
            raw_cursor = RawCursor(
                session_id=authority.session_id,
                session_seq=format_u64(
                    fact_source.session_seq_int if last_seq is None else last_seq
                ),
            )
            commit_ref = CommitRef(
                transition_id=transition_id,
                fact_seq_start=(None if first_seq is None else format_u64(first_seq)),
                fact_seq_end=None if last_seq is None else format_u64(last_seq),
            )
            state_version = OperationStateVersion(
                run_id=state_cas.run_id,
                revision=state_cas.revision + 1,
                projection_epoch=state_cas.projection_epoch,
                commit_ref=commit_ref,
                value=state_value,
            )
            connection.execute(
                """
                INSERT INTO session_operation_states (
                    session_id, run_id, revision, projection_epoch, transition_id,
                    fact_seq_start, fact_seq_end, value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, run_id)
                DO UPDATE SET
                    revision = excluded.revision,
                    projection_epoch = excluded.projection_epoch,
                    transition_id = excluded.transition_id,
                    fact_seq_start = excluded.fact_seq_start,
                    fact_seq_end = excluded.fact_seq_end,
                    value = excluded.value
                """,
                (
                    authority.session_id,
                    state_version.run_id,
                    state_version.revision,
                    state_version.projection_epoch,
                    transition_id,
                    first_seq,
                    last_seq,
                    _json_to_sql(_plain_json(state_version.value)),
                ),
            )
            for slot in disposition_slots.values():
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        slot.slot_id,
                        slot.lane,
                        slot.disposition,
                        _json_to_sql(slot.payload),
                    ),
                )
            if effect_slot is not None:
                connection.execute(
                    """
                    INSERT INTO session_effect_slots (
                        session_id, effect_id, status, payload
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, effect_id)
                    DO UPDATE SET
                        status = excluded.status,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        effect_slot.effect_id,
                        effect_slot.status,
                        _json_to_sql(effect_slot.payload),
                    ),
                )
            notices = tuple(
                CommittedFactNotice(
                    fact_id=fact.event_id,
                    fact_kind=fact.event_kind,
                    payload=fact.payload,
                    session_seq=fact.session_seq,
                    projection_epoch=state_cas.projection_epoch,
                )
                for fact in committed_facts
            )
            receipt = TransitionReceipt(
                session_id=authority.session_id,
                projection_epoch=state_cas.projection_epoch,
                transition_id=transition_id,
                mutation_fingerprint=mutation_fingerprint,
                state_version=state_version,
                facts=notices,
            )
            result_payload = transition_commit_payload(
                state_version=state_version,
                facts=tuple(committed_facts),
                raw_cursor=raw_cursor,
            )
            connection.execute(
                """
                INSERT INTO session_transition_receipts (
                    session_id, projection_epoch, transition_id,
                    mutation_fingerprint, result
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    authority.session_id,
                    state_cas.projection_epoch,
                    transition_id,
                    mutation_fingerprint,
                    _json_to_sql(result_payload),
                ),
            )
        return AuthoritativeCommit(
            event=None,
            projection=fact_source.projection,
            projection_epoch=format_u64(fact_source.projection_epoch_int),
            raw_cursor=raw_cursor,
            state_version=state_version,
            facts=tuple(committed_facts),
            transition_receipt=receipt,
        )

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        _require_json_object("session payload", unit.session_state)
        if unit.session_state.get("id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        payload_session_id = unit.session_state.get("session_id")
        if (
            payload_session_id is not None
            and payload_session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("agent run belongs to another session")
        if unit.is_transition:
            snapshot = snapshot_transition_unit(unit)
            return await self._commit_transition(authority, snapshot)
        if unit.event is None:
            raise ValueError("legacy unit of work requires an event")
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            if unit.require_unsettled_root_run_id is not None:
                current_run = connection.execute(
                    "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                    (unit.require_unsettled_root_run_id, authority.session_id),
                ).fetchone()
                if current_run is None:
                    raise KeyError(
                        f"root run not found: {unit.require_unsettled_root_run_id}"
                    )
                if current_run["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    if unit.event.event_kind != "root_terminal":
                        raise RootRunAlreadySettledError(
                            "chat event after root settlement"
                        )
                    existing_terminal = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_terminal is None:
                        raise ValueError("settled root run is missing root_terminal")
                    event = _event_record_from_sqlite_row(existing_terminal)
                    if event.payload != unit.event.payload:
                        raise RootRunAlreadySettledError(
                            "root run settled with a different outcome"
                        )
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored root_terminal is incomplete")
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )
            chat_receipt = (
                unit.receipt is not None
                and unit.receipt.receipt_id.startswith("chat-command:")
            )
            if chat_receipt:
                existing_receipt_row = connection.execute(
                    """
                    SELECT generation, payload, compensation_effect_id
                    FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt_row is not None:
                    if (
                        existing_receipt_row["generation"] != unit.receipt.generation
                        or _json_object_from_sql(
                            existing_receipt_row["payload"], context="receipt slot"
                        )
                        != unit.receipt.payload
                        or existing_receipt_row["compensation_effect_id"]
                        != unit.receipt.compensation_effect_id
                    ):
                        raise ChatCommandConflictError(
                            "command ID was reused with different input"
                        )
                    existing_event_row = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_event_row is None:
                        raise ValueError(
                            "chat receipt references a missing admission event"
                        )
                    event = _event_record_from_sqlite_row(existing_event_row)
                    if event.session_id != authority.session_id:
                        raise SessionOwnershipConflictError(
                            "chat receipt event belongs to another session"
                        )
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored admission event is incomplete")
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )

                session_row = connection.execute(
                    "SELECT payload FROM agent_http_sessions WHERE session_id = ?",
                    (authority.session_id,),
                ).fetchone()
                if session_row is not None:
                    durable_session = _json_object_from_sql(
                        session_row["payload"], context="session payload"
                    )
                    active_run_id = durable_session.get("turn_id")
                    if active_run_id is not None:
                        if not isinstance(active_run_id, str):
                            raise ValueError("durable turn_id must be a string")
                        active_run = connection.execute(
                            "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                            (active_run_id, authority.session_id),
                        ).fetchone()
                        if active_run is None or active_run["status"] in {
                            "queued",
                            "requested",
                            "claimed",
                            "running",
                            "cancelling",
                        }:
                            raise TurnInProgressError("a root turn is already active")

            run_state = unit.run_state
            if unit.require_settled_parent_run_id is not None:
                parent = connection.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = ? AND superseded_by_checkpoint_id IS NULL
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (authority.session_id,),
                ).fetchone()
                if (
                    parent is None
                    or parent["run_id"] != unit.require_settled_parent_run_id
                    or parent["status"]
                    not in {"completed", "failed", "cancelled", "interrupted"}
                ):
                    raise ResumeSourceUnsettledError(
                        "resume source is not the latest active settled run"
                    )
                if run_state is None:
                    raise ValueError("resume admission requires a run")
                metadata = dict(run_state.metadata)
                metadata.update(
                    {
                        "previous_run_id": parent["run_id"],
                        "resume_from_run_id": parent["run_id"],
                        "resume_reason": "user_resume",
                        "resume_context_injected": True,
                    }
                )
                parent_metadata = _json_object_from_sql(
                    parent["metadata"], context="run metadata"
                )
                previous_event_id = parent_metadata.get("last_event_id")
                if previous_event_id is not None:
                    if not isinstance(previous_event_id, str) or not previous_event_id:
                        raise ValueError("last_event_id must be a non-empty string")
                    metadata["resume_from_event_id"] = previous_event_id
                run_state = replace(run_state, metadata=metadata)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            if run_state is not None:
                if run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                self._assert_tape_belongs_to_session(
                    connection,
                    tape_id=run_state.tape_id,
                    session_id=authority.session_id,
                )
            fact = self._ensure_fact_source(connection, authority.session_id)
            existing_row = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE event_id = ?
                """,
                (unit.event.event_id,),
            ).fetchone()
            idempotent = False
            if existing_row is not None:
                existing_event = _event_record_from_sqlite_row(existing_row)
                if existing_event.session_id != authority.session_id:
                    raise SessionOwnershipConflictError(
                        "event belongs to another session"
                    )
                if existing_event.session_seq is None:
                    raise ValueError("existing event must include session_seq")
                if existing_event.projection_epoch is None:
                    raise ValueError("existing event must include projection_epoch")
                next_seq = parse_u64(
                    existing_event.session_seq, field_name="session_seq"
                )
                existing_epoch = parse_u64(
                    existing_event.projection_epoch, field_name="projection_epoch"
                )
                if existing_epoch != fact.projection_epoch_int:
                    connection.execute(
                        """
                        UPDATE session_event_records
                        SET projection_epoch = ?
                        WHERE event_id = ?
                        """,
                        (fact.projection_epoch_int, unit.event.event_id),
                    )
                    promoted_row = connection.execute(
                        """
                        SELECT * FROM session_event_records
                        WHERE event_id = ?
                        """,
                        (unit.event.event_id,),
                    ).fetchone()
                    if promoted_row is None:
                        raise RuntimeError("failed to promote event projection_epoch")
                    event = _event_record_from_sqlite_row(promoted_row)
                else:
                    event = existing_event
                idempotent = True
            else:
                next_seq = fact.session_seq_int + 1
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (next_seq, authority.session_id),
                )
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        next_seq,
                        unit.event.event_id,
                        unit.event.event_kind,
                        _json_to_sql(unit.event.payload),
                        _datetime_to_json(unit.event.created_at),
                        fact.projection_epoch_int,
                    ),
                )
                event = EventRecord(
                    event_id=unit.event.event_id,
                    session_id=authority.session_id,
                    event_kind=unit.event.event_kind,
                    payload=unit.event.payload,
                    created_at=unit.event.created_at,
                    session_seq=format_u64(next_seq),
                    projection_epoch=format_u64(fact.projection_epoch_int),
                )
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(unit.session_state, sort_keys=True)),
            )
            if run_state is not None:
                existing = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE run_id = ?",
                    (run_state.run_id,),
                ).fetchone()
                if (
                    existing is not None
                    and existing["session_id"] != authority.session_id
                ):
                    raise SessionOwnershipConflictError(
                        "agent run target belongs to another session"
                    )
                connection.execute(
                    """
                    INSERT INTO agent_runs (
                        run_id, session_id, tape_id, parent_run_id, agent_id, status,
                        started_at, ended_at, metadata, result, error,
                        superseded_by_checkpoint_id, superseded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id)
                    DO UPDATE SET
                        tape_id = excluded.tape_id,
                        parent_run_id = excluded.parent_run_id,
                        agent_id = excluded.agent_id,
                        status = excluded.status,
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        metadata = excluded.metadata,
                        result = excluded.result,
                        error = excluded.error
                    """,
                    _agent_run_sqlite_values(run_state),
                )
            if unit.mailbox is not None:
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        unit.mailbox.slot_id,
                        unit.mailbox.lane,
                        unit.mailbox.disposition,
                        _json_to_sql(unit.mailbox.payload),
                    ),
                )
            if unit.effect is not None:
                existing_effect = connection.execute(
                    """
                    SELECT status FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, unit.effect.effect_id),
                ).fetchone()
                if existing_effect is None or effect_status_may_replace(
                    current=existing_effect["status"],
                    incoming=unit.effect.status,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_effect_slots (
                            session_id, effect_id, status, payload
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id, effect_id)
                        DO UPDATE SET
                            status = excluded.status,
                            payload = excluded.payload
                        """,
                        (
                            authority.session_id,
                            unit.effect.effect_id,
                            unit.effect.status,
                            _json_to_sql(unit.effect.payload),
                        ),
                    )
            if unit.receipt is not None:
                existing_receipt = connection.execute(
                    """
                    SELECT generation FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt is None or receipt_generation_may_replace(
                    current=existing_receipt["generation"],
                    incoming=unit.receipt.generation,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_receipt_slots (
                            session_id, receipt_id, generation, payload,
                            compensation_effect_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, receipt_id)
                        DO UPDATE SET
                            generation = excluded.generation,
                            payload = excluded.payload,
                            compensation_effect_id = excluded.compensation_effect_id
                        """,
                        (
                            authority.session_id,
                            unit.receipt.receipt_id,
                            unit.receipt.generation,
                            _json_to_sql(unit.receipt.payload),
                            unit.receipt.compensation_effect_id,
                        ),
                    )
        return AuthoritativeCommit(
            event=event,
            projection=fact.projection,
            projection_epoch=format_u64(fact.projection_epoch_int),
            raw_cursor=RawCursor(
                session_id=authority.session_id,
                session_seq=format_u64(next_seq),
            ),
            idempotent=idempotent,
        )
