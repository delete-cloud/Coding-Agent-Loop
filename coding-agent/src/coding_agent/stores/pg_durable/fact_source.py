"""Harness fact-source load, replay, floor, and handoff."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from coding_agent.stores.runtime_store import (
    CursorEpochMismatchError,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EventRecord,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    ProjectionCursor,
    RawCursor,
    RetentionFloorReplay,
    SessionFactSourceState,
    TrustedHandoff,
    _require_non_empty,
    _require_positive_int,
    assert_projection_binding,
    assert_raw_cursor_not_expired,
    assert_trusted_handoff,
    parse_u64,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.pg_durable.fact_source_rows import (
    _PgFactSource,
    _effect_from_pg_row,
    _event_record_from_pg_row,
    _fact_source_from_pg_row,
    _mailbox_from_pg_row,
    _receipt_from_pg_row,
)
from coding_agent.stores.pg_durable.helpers import _required_row


class PgFactSourceMixin:
    async def load_session_fact_source(
        self,
        session_id: str,
    ) -> SessionFactSourceState | None:
        _require_non_empty("session_id", session_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, session_id)
        if row is None:
            return None
        return _fact_source_from_pg_row(dict(row)).state

    async def load_event_record(
        self,
        session_id: str,
        session_seq: str,
    ) -> EventRecord | None:
        _require_non_empty("session_id", session_id)
        seq = parse_u64(session_seq, field_name="session_seq")
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_SESSION_EVENT_SQL, session_id, seq)
        if row is None:
            return None
        return _event_record_from_pg_row(dict(row))

    async def load_mailbox_slot(
        self,
        session_id: str,
        slot_id: str,
    ) -> MailboxDispositionSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("slot_id", slot_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_MAILBOX_SLOT_SQL, session_id, slot_id)
        if row is None:
            return None
        return _mailbox_from_pg_row(dict(row))

    async def load_effect_slot(
        self,
        session_id: str,
        effect_id: str,
    ) -> EffectLedgerSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("effect_id", effect_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_EFFECT_SLOT_SQL, session_id, effect_id)
        if row is None:
            return None
        return _effect_from_pg_row(dict(row))

    async def load_receipt_slot(
        self,
        session_id: str,
        receipt_id: str,
    ) -> OperationReceiptSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("receipt_id", receipt_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_RECEIPT_SLOT_SQL, session_id, receipt_id)
        if row is None:
            return None
        return _receipt_from_pg_row(dict(row))

    async def replay_raw(
        self,
        cursor: RawCursor,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, cursor.session_id)
        if fact_row is None:
            return []
        fact = _fact_source_from_pg_row(dict(fact_row))
        assert_raw_cursor_not_expired(cursor, fact.state.retention_floor)
        after = parse_u64(cursor.session_seq, field_name="session_seq")
        rows = await pool.fetch(
            self._REPLAY_SESSION_EVENTS_AFTER_SQL,
            cursor.session_id,
            after,
            limit,
        )
        return [_event_record_from_pg_row(dict(row)) for row in rows]

    async def replay_from_retention_floor(
        self,
        session_id: str,
        *,
        limit: int = 1000,
    ) -> RetentionFloorReplay:
        _require_non_empty("session_id", session_id)
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, session_id)
        if fact_row is None:
            return RetentionFloorReplay.from_page(
                session_id=session_id,
                events=[],
                limit=limit,
                retention_floor=0,
                head_session_seq="0",
            )
        fact = _fact_source_from_pg_row(dict(fact_row))
        rows = await pool.fetch(
            self._REPLAY_SESSION_EVENTS_FROM_SQL,
            session_id,
            fact.retention_floor_int,
            limit,
        )
        events = [_event_record_from_pg_row(dict(row)) for row in rows]
        return RetentionFloorReplay.from_page(
            session_id=session_id,
            events=events,
            limit=limit,
            retention_floor=fact.retention_floor_int,
            head_session_seq=fact.state.session_seq,
        )

    async def replay_projection(
        self,
        cursor: ProjectionCursor,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, cursor.session_id)
        if fact_row is None:
            raise CursorEpochMismatchError(
                f"projection cursor bound to epoch {cursor.epoch}, current is missing"
            )
        fact = _fact_source_from_pg_row(dict(fact_row))
        assert_projection_binding(cursor, fact.state)
        assert_raw_cursor_not_expired(
            RawCursor(session_id=cursor.session_id, session_seq=cursor.session_seq),
            fact.state.retention_floor,
        )
        after = parse_u64(cursor.session_seq, field_name="session_seq")
        epoch = parse_u64(cursor.epoch, field_name="epoch")
        rows = await pool.fetch(
            self._REPLAY_PROJECTION_EVENTS_AFTER_SQL,
            cursor.session_id,
            after,
            epoch,
            limit,
        )
        return [_event_record_from_pg_row(dict(row)) for row in rows]

    async def raise_retention_floor(
        self,
        authority: OwnerAuthority,
        retention_floor: str,
    ) -> SessionFactSourceState:
        floor = parse_u64(retention_floor, field_name="retention_floor")

        async def body(connection: Any) -> SessionFactSourceState:
            await self._require_owner(connection, authority)
            fact = await self._ensure_fact_source(connection, authority.session_id)
            if floor < fact.retention_floor_int:
                raise ValueError("retention_floor cannot move backwards")
            if floor > fact.session_seq_int + 1:
                raise ValueError("retention_floor cannot pass the physical log")
            row = await connection.fetchrow(
                self._UPDATE_RETENTION_FLOOR_SQL,
                authority.session_id,
                floor,
            )
            return _fact_source_from_pg_row(_required_row(row, "retention floor")).state

        return cast(SessionFactSourceState, await self._with_transaction(body))

    async def accept_trusted_handoff(
        self,
        authority: OwnerAuthority,
        handoff: TrustedHandoff,
    ) -> SessionFactSourceState:
        if handoff.session_id != authority.session_id:
            raise SessionOwnershipConflictError("handoff belongs to another session")

        async def body(connection: Any) -> SessionFactSourceState:
            await self._require_owner(connection, authority)
            row = await connection.fetchrow(
                self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
                authority.session_id,
            )
            if row is None:
                raise CursorEpochMismatchError(
                    f"trusted handoff bound to epoch {handoff.epoch}, current is missing"
                )
            fact = _fact_source_from_pg_row(dict(row))
            assert_trusted_handoff(handoff, fact.state)
            updated = await connection.fetchrow(
                self._UPDATE_TRUSTED_HANDOFF_SQL,
                authority.session_id,
                parse_u64(handoff.session_seq, field_name="session_seq"),
                parse_u64(handoff.epoch, field_name="epoch"),
                handoff.projection,
                handoff.payload,
                datetime.now(UTC),
            )
            return _fact_source_from_pg_row(
                _required_row(updated, "trusted handoff")
            ).state

        return cast(SessionFactSourceState, await self._with_transaction(body))

    async def _ensure_fact_source(
        self,
        connection: Any,
        session_id: str,
    ) -> _PgFactSource:
        existing = await connection.fetchrow(
            self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
            session_id,
        )
        if existing is not None:
            return _fact_source_from_pg_row(dict(existing))
        inserted = await connection.fetchrow(
            self._INSERT_FACT_SOURCE_SQL,
            session_id,
            0,
            0,
            DEFAULT_HARNESS_PROJECTION,
            0,
        )
        return _fact_source_from_pg_row(
            _required_row(inserted, "session fact source insert")
        )

    async def _open_projection_epoch(
        self,
        connection: Any,
        session_id: str,
    ) -> None:
        existing = await connection.fetchrow(
            self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
            session_id,
        )
        if existing is None:
            _ = await connection.fetchrow(
                self._INSERT_FACT_SOURCE_SQL,
                session_id,
                0,
                0,
                DEFAULT_HARNESS_PROJECTION,
                1,
            )
            return
        _ = await connection.fetchrow(self._BUMP_PROJECTION_EPOCH_SQL, session_id)
