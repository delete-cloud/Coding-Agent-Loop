"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
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
    RawCursor,
    effect_status_may_replace,
    format_u64,
    parse_u64,
    receipt_generation_may_replace,
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

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        _require_payload_session(authority, unit.session_state)
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("run target belongs to another owner")

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
