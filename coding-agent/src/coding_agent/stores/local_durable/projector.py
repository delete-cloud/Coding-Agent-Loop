"""Owner-fenced projector cursor and sink receipts for SQLite."""

from __future__ import annotations

import json

from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.local_durable.fact_source_rows import (
    _event_record_from_sqlite_row,
)
from coding_agent.stores.runtime_store import EventRecord


class LocalProjectorMixin:
    async def load_projector_cursor(self, session_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT last_session_seq FROM session_projector_cursors
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return 0
        return int(row["last_session_seq"])

    async def list_session_events_after(
        self,
        session_id: str,
        after_seq: int,
    ) -> tuple[EventRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE session_id = ? AND session_seq > ?
                ORDER BY session_seq
                """,
                (session_id, after_seq),
            ).fetchall()
        return tuple(_event_record_from_sqlite_row(row) for row in rows)

    async def upsert_projector_sink(
        self,
        authority: OwnerAuthority,
        *,
        event_id: str,
        sink: str,
        payload: dict[str, object],
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            connection.execute(
                """
                INSERT INTO session_projector_sinks (
                    session_id, event_id, sink, payload
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, event_id, sink) DO NOTHING
                """,
                (
                    authority.session_id,
                    event_id,
                    sink,
                    json.dumps(payload, sort_keys=True),
                ),
            )

    async def list_projector_sinks(
        self,
        session_id: str,
        event_id: str,
    ) -> frozenset[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sink FROM session_projector_sinks
                WHERE session_id = ? AND event_id = ?
                """,
                (session_id, event_id),
            ).fetchall()
        return frozenset(str(row["sink"]) for row in rows)

    async def list_wire_outbox_event_ids(self, session_id: str) -> tuple[str, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id FROM session_projector_sinks
                WHERE session_id = ? AND sink = 'wire_outbox'
                ORDER BY event_id
                """,
                (session_id,),
            ).fetchall()
        return tuple(str(row["event_id"]) for row in rows)

    async def advance_projector_cursor(
        self,
        authority: OwnerAuthority,
        session_seq: int,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            connection.execute(
                """
                INSERT INTO session_projector_cursors (session_id, last_session_seq)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_session_seq = MAX(
                        session_projector_cursors.last_session_seq,
                        excluded.last_session_seq
                    )
                """,
                (authority.session_id, session_seq),
            )
