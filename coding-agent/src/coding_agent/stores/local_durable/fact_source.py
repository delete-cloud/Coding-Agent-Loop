"""Harness fact-source load, replay, floor, and handoff."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from coding_agent.events.connected_chat import (
    CHAT_EVENT_KINDS,
    CONNECTED_CHAT_PROJECTION,
    ChatSnapshot,
    ConnectedChatCursor,
    encode_chat_cursor,
    project_chat_event,
)
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
    _agent_run_from_sqlite_row,
    _json_object_from_sql,
    _json_to_sql,
    _sqlite_optional_str,
    _require_positive_int,
    _sqlite_required_str,
    assert_projection_binding,
    assert_raw_cursor_not_expired,
    assert_trusted_handoff,
    parse_u64,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
    _datetime_to_sqlite_text,
)
from coding_agent.stores.local_durable.fact_source_rows import (
    _SqliteFactSource,
    _event_record_from_sqlite_row,
    _fact_source_from_sqlite_row,
)
from coding_agent.stores.local_durable.helpers import _require_non_empty


class LocalFactSourceMixin:
    async def load_session_fact_source(
        self,
        session_id: str,
    ) -> SessionFactSourceState | None:
        _require_non_empty("session_id", session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_fact_source WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return _fact_source_from_sqlite_row(row).state

    async def load_event_record(
        self,
        session_id: str,
        session_seq: str,
    ) -> EventRecord | None:
        _require_non_empty("session_id", session_id)
        seq = parse_u64(session_seq, field_name="session_seq")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE session_id = ? AND session_seq = ?
                """,
                (session_id, seq),
            ).fetchone()
        if row is None:
            return None
        return _event_record_from_sqlite_row(row)

    async def load_mailbox_slot(
        self,
        session_id: str,
        slot_id: str,
    ) -> MailboxDispositionSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("slot_id", slot_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_mailbox_slots
                WHERE session_id = ? AND slot_id = ?
                """,
                (session_id, slot_id),
            ).fetchone()
        if row is None:
            return None
        return MailboxDispositionSlot(
            slot_id=_sqlite_required_str(row, "slot_id", context="mailbox slot"),
            lane=_sqlite_required_str(row, "lane", context="mailbox slot"),
            disposition=_sqlite_required_str(
                row, "disposition", context="mailbox slot"
            ),
            payload=_json_object_from_sql(row["payload"], context="mailbox slot"),
        )

    async def load_effect_slot(
        self,
        session_id: str,
        effect_id: str,
    ) -> EffectLedgerSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("effect_id", effect_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_effect_slots
                WHERE session_id = ? AND effect_id = ?
                """,
                (session_id, effect_id),
            ).fetchone()
        if row is None:
            return None
        return EffectLedgerSlot(
            effect_id=_sqlite_required_str(row, "effect_id", context="effect slot"),
            status=_sqlite_required_str(row, "status", context="effect slot"),
            payload=_json_object_from_sql(row["payload"], context="effect slot"),
        )

    async def load_receipt_slot(
        self,
        session_id: str,
        receipt_id: str,
    ) -> OperationReceiptSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("receipt_id", receipt_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_receipt_slots
                WHERE session_id = ? AND receipt_id = ?
                """,
                (session_id, receipt_id),
            ).fetchone()
        if row is None:
            return None
        return OperationReceiptSlot(
            receipt_id=_sqlite_required_str(row, "receipt_id", context="receipt slot"),
            generation=_sqlite_required_str(row, "generation", context="receipt slot"),
            payload=_json_object_from_sql(row["payload"], context="receipt slot"),
            compensation_effect_id=_sqlite_optional_str(
                row,
                "compensation_effect_id",
                context="receipt slot",
            ),
        )

    async def replay_raw(
        self,
        cursor: RawCursor,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_positive_int("limit", limit)
        with self._lock, self._connect() as connection:
            fact = self._load_fact_source_row(connection, cursor.session_id)
            if fact is None:
                return []
            assert_raw_cursor_not_expired(cursor, fact.state.retention_floor)
            after = parse_u64(cursor.session_seq, field_name="session_seq")
            rows = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE session_id = ? AND session_seq > ?
                ORDER BY session_seq
                LIMIT ?
                """,
                (cursor.session_id, after, limit),
            ).fetchall()
        return [_event_record_from_sqlite_row(row) for row in rows]

    async def replay_from_retention_floor(
        self,
        session_id: str,
        *,
        limit: int = 1000,
    ) -> RetentionFloorReplay:
        _require_non_empty("session_id", session_id)
        _require_positive_int("limit", limit)
        with self._lock, self._connect() as connection:
            fact = self._load_fact_source_row(connection, session_id)
            if fact is None:
                return RetentionFloorReplay.from_page(
                    session_id=session_id,
                    events=[],
                    limit=limit,
                    retention_floor=0,
                    head_session_seq="0",
                )
            rows = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE session_id = ? AND session_seq >= ?
                ORDER BY session_seq
                LIMIT ?
                """,
                (session_id, fact.retention_floor_int, limit),
            ).fetchall()
            events = [_event_record_from_sqlite_row(row) for row in rows]
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
        with self._lock, self._connect() as connection:
            fact = self._load_fact_source_row(connection, cursor.session_id)
            if fact is None:
                raise CursorEpochMismatchError(
                    f"projection cursor bound to epoch {cursor.epoch}, current is missing"
                )
            assert_projection_binding(cursor, fact.state)
            assert_raw_cursor_not_expired(
                RawCursor(session_id=cursor.session_id, session_seq=cursor.session_seq),
                fact.state.retention_floor,
            )
            after = parse_u64(cursor.session_seq, field_name="session_seq")
            epoch = parse_u64(cursor.epoch, field_name="epoch")
            rows = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE session_id = ? AND session_seq > ?
                  AND projection_epoch = ?
                ORDER BY session_seq
                LIMIT ?
                """,
                (cursor.session_id, after, epoch, limit),
            ).fetchall()
        return [_event_record_from_sqlite_row(row) for row in rows]

    async def snapshot_chat_events(
        self,
        session_id: str,
        cursor: ConnectedChatCursor | None,
        limit: int,
    ) -> ChatSnapshot:
        _require_non_empty("session_id", session_id)
        _require_positive_int("limit", limit)
        with self._lock, self._connect() as connection:
            fact = self._ensure_fact_source(connection, session_id)
            state = fact.state
            if cursor is None:
                after = max(0, fact.retention_floor_int - 1)
                high_water = fact.session_seq_int
            else:
                from coding_agent.events.connected_chat import decode_chat_cursor

                cursor = decode_chat_cursor(
                    encode_chat_cursor(cursor),
                    expected_session_id=session_id,
                    fact_state=state,
                )
                after = parse_u64(cursor.after_seq, field_name="after_seq")
                high_water = parse_u64(
                    cursor.high_water_seq, field_name="high_water_seq"
                )
            events = []
            scanned_after = after
            scanned_rows = 0
            max_scan = max(1024, limit * 64)
            chunk_size = max(16, min(256, limit * 2))
            kind_placeholders = ",".join("?" * len(CHAT_EVENT_KINDS))
            while (
                scanned_after < high_water
                and len(events) < limit
                and scanned_rows < max_scan
            ):
                rows = connection.execute(
                    f"""
                    SELECT event.*, run.*
                    FROM session_event_records AS event
                    LEFT JOIN agent_runs AS run
                      ON run.run_id = json_extract(event.payload, '$.run_id')
                     AND run.session_id = event.session_id
                    WHERE event.session_id = ?
                      AND event.session_seq > ?
                      AND event.session_seq <= ?
                      AND event.event_kind IN ({kind_placeholders})
                    ORDER BY event.session_seq
                    LIMIT ?
                    """,
                    (
                        session_id,
                        scanned_after,
                        high_water,
                        *CHAT_EVENT_KINDS,
                        chunk_size,
                    ),
                ).fetchall()
                if not rows:
                    scanned_after = high_water
                    break
                scanned_rows += len(rows)
                for row in rows:
                    record = _event_record_from_sqlite_row(row)
                    if record.session_seq is None:
                        raise ValueError("stored event must include session_seq")
                    scanned_after = parse_u64(
                        record.session_seq, field_name="session_seq"
                    )
                    run = (
                        None
                        if row["run_id"] is None
                        else _agent_run_from_sqlite_row(row)
                    )
                    projected = project_chat_event(record, run)
                    if projected is not None:
                        events.append(projected)
                    if len(events) == limit:
                        break
        snapshot_cursor = ConnectedChatCursor(
            v=1,
            kind="chat",
            session_id=session_id,
            projection=CONNECTED_CHAT_PROJECTION,
            epoch=state.projection_epoch,
            after_seq=str(max(0, fact.retention_floor_int - 1)),
            high_water_seq=str(high_water),
        )
        next_cursor = None
        if scanned_after < high_water:
            next_cursor = encode_chat_cursor(
                ConnectedChatCursor(
                    v=1,
                    kind="chat",
                    session_id=session_id,
                    projection=CONNECTED_CHAT_PROJECTION,
                    epoch=state.projection_epoch,
                    after_seq=str(scanned_after),
                    high_water_seq=str(high_water),
                )
            )
        return ChatSnapshot(
            session_id=session_id,
            projection=CONNECTED_CHAT_PROJECTION,
            projection_epoch=state.projection_epoch,
            snapshot_cursor=encode_chat_cursor(snapshot_cursor),
            next_cursor=next_cursor,
            events=tuple(events),
        )

    async def raise_retention_floor(
        self,
        authority: OwnerAuthority,
        retention_floor: str,
    ) -> SessionFactSourceState:
        floor = parse_u64(retention_floor, field_name="retention_floor")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact = self._ensure_fact_source(connection, authority.session_id)
            if floor < fact.retention_floor_int:
                raise ValueError("retention_floor cannot move backwards")
            if floor > fact.session_seq_int + 1:
                raise ValueError("retention_floor cannot pass the physical log")
            connection.execute(
                """
                UPDATE session_fact_source
                SET retention_floor = ?
                WHERE session_id = ?
                """,
                (floor, authority.session_id),
            )
            updated = self._load_fact_source_row(connection, authority.session_id)
            if updated is None:
                raise RuntimeError("failed to reload session fact source")
        return updated.state

    async def accept_trusted_handoff(
        self,
        authority: OwnerAuthority,
        handoff: TrustedHandoff,
    ) -> SessionFactSourceState:
        if handoff.session_id != authority.session_id:
            raise SessionOwnershipConflictError("handoff belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            fact = self._load_fact_source_row(connection, authority.session_id)
            if fact is None:
                raise CursorEpochMismatchError(
                    f"trusted handoff bound to epoch {handoff.epoch}, current is missing"
                )
            assert_trusted_handoff(handoff, fact.state)
            accepted_at = _datetime_to_sqlite_text(datetime.now(UTC))
            connection.execute(
                """
                UPDATE session_fact_source
                SET trusted_handoff_seq = ?,
                    trusted_handoff_epoch = ?,
                    trusted_handoff_projection = ?,
                    trusted_handoff_payload = ?,
                    trusted_handoff_accepted_at = ?
                WHERE session_id = ?
                """,
                (
                    parse_u64(handoff.session_seq, field_name="session_seq"),
                    parse_u64(handoff.epoch, field_name="epoch"),
                    handoff.projection,
                    _json_to_sql(handoff.payload),
                    accepted_at,
                    authority.session_id,
                ),
            )
            updated = self._load_fact_source_row(connection, authority.session_id)
            if updated is None:
                raise RuntimeError("failed to persist trusted handoff")
        return updated.state

    def _ensure_fact_source(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> _SqliteFactSource:
        existing = self._load_fact_source_row(connection, session_id)
        if existing is not None:
            return existing
        connection.execute(
            """
            INSERT INTO session_fact_source (
                session_id, session_seq, retention_floor, projection, projection_epoch
            )
            VALUES (?, 0, 0, ?, 0)
            """,
            (session_id, DEFAULT_HARNESS_PROJECTION),
        )
        created = self._load_fact_source_row(connection, session_id)
        if created is None:
            raise RuntimeError("failed to allocate session fact source")
        return created

    def _ensure_harness_fact_source_columns(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(session_fact_source)"
            ).fetchall()
        }
        migrations = (
            ("trusted_handoff_seq", "INTEGER"),
            ("trusted_handoff_epoch", "INTEGER"),
            ("trusted_handoff_projection", "TEXT"),
            ("trusted_handoff_payload", "TEXT"),
            ("trusted_handoff_accepted_at", "TEXT"),
        )
        for name, decl in migrations:
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE session_fact_source ADD COLUMN {name} {decl}"
                )

    def _load_fact_source_row(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> _SqliteFactSource | None:
        row = connection.execute(
            "SELECT * FROM session_fact_source WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return _fact_source_from_sqlite_row(row)

    def _open_projection_epoch(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> None:
        existing = self._load_fact_source_row(connection, session_id)
        if existing is None:
            connection.execute(
                """
                INSERT INTO session_fact_source (
                    session_id, session_seq, retention_floor, projection,
                    projection_epoch
                )
                VALUES (?, 0, 0, ?, 1)
                """,
                (session_id, DEFAULT_HARNESS_PROJECTION),
            )
            return
        connection.execute(
            """
            UPDATE session_fact_source
            SET projection_epoch = projection_epoch + 1
            WHERE session_id = ?
            """,
            (session_id,),
        )
