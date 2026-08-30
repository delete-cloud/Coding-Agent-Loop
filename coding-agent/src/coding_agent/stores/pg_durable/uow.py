"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from agentkit.runtime.contracts import (
    CommitRef,
    CommittedFactNotice,
    OperationStateVersion,
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
    CommandDispositionConflictError,
    EffectMutationConflictError,
    EventRecord,
    JSONObject,
    RawCursor,
    effect_status_may_replace,
    format_u64,
    parse_u64,
    receipt_generation_may_replace,
    StateVersionConflictError,
    TransitionFingerprintMismatchError,
    _operation_state_from_row,
    _plain_json,
    _transition_receipt_from_row,
    _require_non_empty,
    effect_slot_from_mutation,
    mailbox_slot_from_disposition,
    transition_commit_from_payload,
    transition_commit_payload,
    transition_mutation_fingerprint,
    snapshot_transition_unit,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.fact_source_rows import _event_record_from_pg_row
from coding_agent.stores.pg_durable.helpers import (
    _require_payload_session,
    _required_owned_row,
    _required_row,
    _required_str,
)


class PgUnitOfWorkMixin:
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
        from coding_agent.stores.runtime_store import PGRuntimeStore

        run = await PGRuntimeStore(pool=self._pool).load_agent_run(run_id)
        if run is None or run.session_id != authority.session_id:
            raise KeyError(f"root run not found: {run_id}")
        pool = await self._pool.get_pool()
        session_row = await pool.fetchrow(
            self._SELECT_SESSION_FOR_UPDATE_SQL, authority.session_id
        )
        if session_row is None:
            raise KeyError(f"session not found: {authority.session_id}")
        session = dict(session_row).get("payload")
        if not isinstance(session, dict):
            raise TypeError("durable session payload must be an object")
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

    async def load_operation_state(
        self,
        session_id: str,
        run_id: str,
    ) -> OperationStateVersion | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("run_id", run_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(
            self._SELECT_OPERATION_STATE_SQL,
            session_id,
            run_id,
        )
        return None if row is None else _operation_state_from_row(dict(row))

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
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(
            self._SELECT_TRANSITION_RECEIPT_SQL,
            session_id,
            projection_epoch,
            transition_id,
        )
        if row is None:
            return None
        fingerprint, result_payload = _transition_receipt_from_row(dict(row))
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

        async def body(connection: Any) -> AuthoritativeCommit:
            await self._require_owner(connection, authority)
            fact_source = await self._ensure_fact_source(
                connection,
                authority.session_id,
            )
            if state_cas.projection_epoch != fact_source.projection_epoch_int:
                raise StateVersionConflictError(
                    "operation state projection epoch is stale"
                )
            receipt_row = await connection.fetchrow(
                self._SELECT_TRANSITION_RECEIPT_SQL,
                authority.session_id,
                state_cas.projection_epoch,
                transition_id,
            )
            if receipt_row is not None:
                stored_fingerprint, result_payload = _transition_receipt_from_row(
                    dict(receipt_row)
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

            current_state_row = await connection.fetchrow(
                self._SELECT_OPERATION_STATE_FOR_UPDATE_SQL,
                authority.session_id,
                state_cas.run_id,
            )
            if current_state_row is None:
                if state_cas.revision != 0:
                    raise StateVersionConflictError("operation state revision is stale")
            else:
                current_state = _operation_state_from_row(dict(current_state_row))
                if current_state.cas != state_cas:
                    raise StateVersionConflictError(
                        "operation state compare-and-swap conflict"
                    )

            disposition_slots = {}
            for disposition in unit.dispositions:
                slot = mailbox_slot_from_disposition(disposition)
                existing_mailbox_row = await connection.fetchrow(
                    self._SELECT_MAILBOX_SLOT_SQL,
                    authority.session_id,
                    slot.slot_id,
                )
                if existing_mailbox_row is None:
                    raise CommandDispositionConflictError(
                        "command disposition requires an admitted mailbox row"
                    )
                existing_mailbox = dict(existing_mailbox_row)
                if _required_str(existing_mailbox, "disposition") not in {
                    "pending",
                    "admitted",
                }:
                    raise CommandDispositionConflictError(
                        "command mailbox row is already terminal"
                    )
                existing_payload = existing_mailbox.get("payload")
                if not isinstance(existing_payload, dict):
                    raise TypeError("mailbox slot payload must be an object")
                disposition_slots[slot.slot_id] = replace(
                    slot,
                    lane=_required_str(existing_mailbox, "lane"),
                    payload={**existing_payload, **slot.payload},
                )

            effect_slot = (
                None
                if unit.effect_mutation is None
                else effect_slot_from_mutation(unit.effect_mutation)
            )
            if unit.effect_mutation is not None:
                current_effect_row = await connection.fetchrow(
                    self._SELECT_EFFECT_SLOT_SQL,
                    authority.session_id,
                    unit.effect_mutation.effect_id,
                )
                expected_status = unit.effect_mutation.expected_status
                if expected_status is None:
                    if current_effect_row is not None:
                        raise EffectMutationConflictError("effect already exists")
                elif current_effect_row is None:
                    raise EffectMutationConflictError("effect does not exist")
                else:
                    current_effect = dict(current_effect_row)
                    if current_effect.get("status") != expected_status.value:
                        raise EffectMutationConflictError(
                            "effect status precondition failed"
                        )
                    current_payload = current_effect.get("payload")
                    if not isinstance(current_payload, dict):
                        raise TypeError("effect slot payload must be an object")
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
                event_row = await connection.fetchrow(
                    self._INSERT_SESSION_EVENT_SQL,
                    authority.session_id,
                    session_seq,
                    fact.event_id,
                    fact.event_kind,
                    fact.payload,
                    fact.created_at,
                    fact_source.projection_epoch_int,
                )
                committed_facts.append(
                    _event_record_from_pg_row(
                        _required_row(event_row, "transition fact insert")
                    )
                )
            last_seq = (
                fact_source.session_seq_int + len(committed_facts)
                if committed_facts
                else None
            )
            if last_seq is not None:
                _ = await connection.fetchrow(
                    self._UPDATE_FACT_SOURCE_SEQ_SQL,
                    authority.session_id,
                    last_seq,
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
            state_row = await connection.fetchrow(
                self._UPSERT_OPERATION_STATE_SQL,
                authority.session_id,
                state_version.run_id,
                state_version.revision,
                state_version.projection_epoch,
                transition_id,
                first_seq,
                last_seq,
                _plain_json(state_version.value),
            )
            _ = _operation_state_from_row(
                _required_row(state_row, "operation state upsert")
            )
            for slot in disposition_slots.values():
                _ = await connection.fetchrow(
                    self._UPSERT_MAILBOX_SLOT_SQL,
                    authority.session_id,
                    slot.slot_id,
                    slot.lane,
                    slot.disposition,
                    slot.payload,
                )
            if effect_slot is not None:
                _ = await connection.fetchrow(
                    self._UPSERT_EFFECT_SLOT_SQL,
                    authority.session_id,
                    effect_slot.effect_id,
                    effect_slot.status,
                    effect_slot.payload,
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
            receipt_inserted = await connection.fetchrow(
                self._INSERT_TRANSITION_RECEIPT_SQL,
                authority.session_id,
                state_cas.projection_epoch,
                transition_id,
                mutation_fingerprint,
                result_payload,
            )
            if receipt_inserted is None:
                raise RuntimeError("transition receipt insert returned no row")
            return AuthoritativeCommit(
                event=None,
                projection=fact_source.projection,
                projection_epoch=format_u64(fact_source.projection_epoch_int),
                raw_cursor=raw_cursor,
                state_version=state_version,
                facts=tuple(committed_facts),
                transition_receipt=receipt,
            )

        return cast(AuthoritativeCommit, await self._with_transaction(body))

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        _require_payload_session(authority, unit.session_state)
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("run target belongs to another owner")
        if unit.is_transition:
            snapshot = snapshot_transition_unit(unit)
            return await self._commit_transition(authority, snapshot)
        if unit.event is None:
            raise ValueError("legacy unit of work requires an event")
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")

        async def body(connection: Any) -> AuthoritativeCommit:
            await self._require_owner(connection, authority)
            if unit.require_unsettled_root_run_id is not None:
                current_row = await connection.fetchrow(
                    "SELECT status FROM agent_runs WHERE run_id = $1 AND session_id = $2 FOR UPDATE",
                    unit.require_unsettled_root_run_id,
                    authority.session_id,
                )
                if current_row is None:
                    raise KeyError(
                        f"root run not found: {unit.require_unsettled_root_run_id}"
                    )
                current_status = dict(current_row).get("status")
                if current_status in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    if unit.event.event_kind != "root_terminal":
                        raise RootRunAlreadySettledError(
                            "chat event after root settlement"
                        )
                    existing_row = await connection.fetchrow(
                        self._SELECT_SESSION_EVENT_BY_ID_SQL, unit.event.event_id
                    )
                    event = _event_record_from_pg_row(
                        _required_row(existing_row, "root terminal event")
                    )
                    if event.payload != unit.event.payload:
                        raise RootRunAlreadySettledError(
                            "root run settled with a different outcome"
                        )
                    fact = await self._ensure_fact_source(
                        connection, authority.session_id
                    )
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
                existing_receipt_row = await connection.fetchrow(
                    self._SELECT_RECEIPT_SLOT_SQL,
                    authority.session_id,
                    unit.receipt.receipt_id,
                )
                if existing_receipt_row is not None:
                    existing_receipt = dict(existing_receipt_row)
                    if (
                        existing_receipt.get("generation") != unit.receipt.generation
                        or existing_receipt.get("payload") != unit.receipt.payload
                        or existing_receipt.get("compensation_effect_id")
                        != unit.receipt.compensation_effect_id
                    ):
                        raise ChatCommandConflictError(
                            "command ID was reused with different input"
                        )
                    event_row = await connection.fetchrow(
                        self._SELECT_SESSION_EVENT_BY_ID_SQL, unit.event.event_id
                    )
                    event = _event_record_from_pg_row(
                        _required_row(event_row, "chat receipt admission event")
                    )
                    if event.session_id != authority.session_id:
                        raise SessionOwnershipConflictError(
                            "chat receipt event belongs to another session"
                        )
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored admission event is incomplete")
                    fact = await self._ensure_fact_source(
                        connection, authority.session_id
                    )
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

                session_row = await connection.fetchrow(
                    "SELECT payload FROM agent_http_sessions WHERE session_id = $1 FOR UPDATE",
                    authority.session_id,
                )
                if session_row is not None:
                    durable_session = dict(session_row).get("payload")
                    if not isinstance(durable_session, dict):
                        raise TypeError("durable session payload must be an object")
                    active_run_id = durable_session.get("turn_id")
                    if active_run_id is not None:
                        if not isinstance(active_run_id, str):
                            raise ValueError("durable turn_id must be a string")
                        active_run = await connection.fetchrow(
                            "SELECT status FROM agent_runs WHERE run_id = $1 AND session_id = $2 FOR UPDATE",
                            active_run_id,
                            authority.session_id,
                        )
                        if active_run is None or dict(active_run).get("status") in {
                            "queued",
                            "requested",
                            "claimed",
                            "running",
                            "cancelling",
                        }:
                            raise TurnInProgressError("a root turn is already active")

            run_state = unit.run_state
            if unit.require_settled_parent_run_id is not None:
                parent_row = await connection.fetchrow(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = $1 AND superseded_by_checkpoint_id IS NULL
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1 FOR UPDATE
                    """,
                    authority.session_id,
                )
                parent = None if parent_row is None else dict(parent_row)
                if (
                    parent is None
                    or parent.get("run_id") != unit.require_settled_parent_run_id
                    or parent.get("status")
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
                parent_metadata = parent.get("metadata")
                if not isinstance(parent_metadata, dict):
                    raise TypeError("run metadata must be an object")
                previous_event_id = parent_metadata.get("last_event_id")
                if previous_event_id is not None:
                    if not isinstance(previous_event_id, str) or not previous_event_id:
                        raise ValueError("last_event_id must be a non-empty string")
                    metadata["resume_from_event_id"] = previous_event_id
                run_state = replace(run_state, metadata=metadata)
            if tape_id:
                await self._bind_tape(connection, authority.session_id, tape_id)
            if run_state is not None:
                if run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                await self._require_stable_tape(
                    connection, authority, run_state.tape_id
                )
            fact = await self._ensure_fact_source(connection, authority.session_id)
            existing_row = await connection.fetchrow(
                self._SELECT_SESSION_EVENT_BY_ID_SQL,
                unit.event.event_id,
            )
            idempotent = False
            if existing_row is not None:
                existing_event = _event_record_from_pg_row(dict(existing_row))
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
                    promoted_row = await connection.fetchrow(
                        self._PROMOTE_SESSION_EVENT_EPOCH_SQL,
                        unit.event.event_id,
                        fact.projection_epoch_int,
                    )
                    event = _event_record_from_pg_row(
                        _required_row(promoted_row, "session event epoch promote")
                    )
                else:
                    event = existing_event
                idempotent = True
            else:
                next_seq = fact.session_seq_int + 1
                _ = await connection.fetchrow(
                    self._UPDATE_FACT_SOURCE_SEQ_SQL,
                    authority.session_id,
                    next_seq,
                )
                event_row = await connection.fetchrow(
                    self._INSERT_SESSION_EVENT_SQL,
                    authority.session_id,
                    next_seq,
                    unit.event.event_id,
                    unit.event.event_kind,
                    unit.event.payload,
                    unit.event.created_at,
                    fact.projection_epoch_int,
                )
                event = _event_record_from_pg_row(
                    _required_row(event_row, "session event insert")
                )
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                unit.session_state,
            )
            if run_state is not None:
                run_row = await connection.fetchrow(
                    self._UPSERT_OWNED_RUN_SQL,
                    run_state.run_id,
                    run_state.session_id,
                    run_state.tape_id,
                    run_state.parent_run_id,
                    run_state.agent_id,
                    run_state.status,
                    run_state.started_at,
                    run_state.ended_at,
                    run_state.metadata,
                    run_state.result,
                    run_state.error,
                    run_state.superseded_by_checkpoint_id,
                    run_state.superseded_at,
                )
                _required_owned_row(run_row, "run target belongs to another owner")
            if unit.mailbox is not None:
                _ = await connection.fetchrow(
                    self._UPSERT_MAILBOX_SLOT_SQL,
                    authority.session_id,
                    unit.mailbox.slot_id,
                    unit.mailbox.lane,
                    unit.mailbox.disposition,
                    unit.mailbox.payload,
                )
            if unit.effect is not None:
                existing_effect = await connection.fetchrow(
                    self._SELECT_EFFECT_SLOT_SQL,
                    authority.session_id,
                    unit.effect.effect_id,
                )
                if existing_effect is None or effect_status_may_replace(
                    current=_required_str(dict(existing_effect), "status"),
                    incoming=unit.effect.status,
                ):
                    _ = await connection.fetchrow(
                        self._UPSERT_EFFECT_SLOT_SQL,
                        authority.session_id,
                        unit.effect.effect_id,
                        unit.effect.status,
                        unit.effect.payload,
                    )
            if unit.receipt is not None:
                existing_receipt = await connection.fetchrow(
                    self._SELECT_RECEIPT_SLOT_SQL,
                    authority.session_id,
                    unit.receipt.receipt_id,
                )
                if existing_receipt is None or receipt_generation_may_replace(
                    current=_required_str(dict(existing_receipt), "generation"),
                    incoming=unit.receipt.generation,
                ):
                    _ = await connection.fetchrow(
                        self._UPSERT_RECEIPT_SLOT_SQL,
                        authority.session_id,
                        unit.receipt.receipt_id,
                        unit.receipt.generation,
                        unit.receipt.payload,
                        unit.receipt.compensation_effect_id,
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

        return cast(AuthoritativeCommit, await self._with_transaction(body))
