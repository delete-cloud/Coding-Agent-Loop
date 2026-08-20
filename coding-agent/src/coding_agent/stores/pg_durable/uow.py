"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

from typing import Any, cast
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
            if tape_id:
                await self._bind_tape(connection, authority.session_id, tape_id)
            if unit.run_state is not None:
                if unit.run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                await self._require_stable_tape(
                    connection, authority, unit.run_state.tape_id
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
            if unit.run_state is not None:
                run_row = await connection.fetchrow(
                    self._UPSERT_OWNED_RUN_SQL,
                    unit.run_state.run_id,
                    unit.run_state.session_id,
                    unit.run_state.tape_id,
                    unit.run_state.parent_run_id,
                    unit.run_state.agent_id,
                    unit.run_state.status,
                    unit.run_state.started_at,
                    unit.run_state.ended_at,
                    unit.run_state.metadata,
                    unit.run_state.result,
                    unit.run_state.error,
                    unit.run_state.superseded_by_checkpoint_id,
                    unit.run_state.superseded_at,
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
