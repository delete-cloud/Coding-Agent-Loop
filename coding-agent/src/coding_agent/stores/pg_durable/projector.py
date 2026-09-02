"""Owner-fenced projector cursor and sink receipts for PostgreSQL."""

from __future__ import annotations

from typing import Any

from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.pg_durable.fact_source_rows import _event_record_from_pg_row
from coding_agent.stores.runtime_store import EventRecord


class PgProjectorMixin:
    async def load_projector_cursor(self, session_id: str) -> int:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(
            """
            SELECT last_session_seq FROM session_projector_cursors
            WHERE session_id = $1
            """,
            session_id,
        )
        if row is None:
            return 0
        return int(row["last_session_seq"])

    async def list_session_events_after(
        self,
        session_id: str,
        after_seq: int,
    ) -> tuple[EventRecord, ...]:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        rows = await pool.fetch(
            """
            SELECT * FROM session_event_records
            WHERE session_id = $1 AND session_seq > $2
            ORDER BY session_seq
            """,
            session_id,
            after_seq,
        )
        return tuple(_event_record_from_pg_row(dict(row)) for row in rows)

    async def upsert_projector_sink(
        self,
        authority: OwnerAuthority,
        *,
        event_id: str,
        sink: str,
        payload: dict[str, object],
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await connection.execute(
                """
                INSERT INTO session_projector_sinks (
                    session_id, event_id, sink, payload
                )
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (session_id, event_id, sink) DO NOTHING
                """,
                authority.session_id,
                event_id,
                sink,
                payload,
            )

        await self._with_transaction(body)

    async def list_projector_sinks(
        self,
        session_id: str,
        event_id: str,
    ) -> frozenset[str]:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        rows = await pool.fetch(
            """
            SELECT sink FROM session_projector_sinks
            WHERE session_id = $1 AND event_id = $2
            """,
            session_id,
            event_id,
        )
        return frozenset(str(row["sink"]) for row in rows)

    async def list_wire_outbox_event_ids(self, session_id: str) -> tuple[str, ...]:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        rows = await pool.fetch(
            """
            SELECT event_id FROM session_projector_sinks
            WHERE session_id = $1 AND sink = 'wire_outbox'
            ORDER BY event_id
            """,
            session_id,
        )
        return tuple(str(row["event_id"]) for row in rows)

    async def advance_projector_cursor(
        self,
        authority: OwnerAuthority,
        session_seq: int,
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await connection.execute(
                """
                INSERT INTO session_projector_cursors (session_id, last_session_seq)
                VALUES ($1, $2)
                ON CONFLICT (session_id) DO UPDATE SET
                    last_session_seq = GREATEST(
                        session_projector_cursors.last_session_seq,
                        EXCLUDED.last_session_seq
                    )
                """,
                authority.session_id,
                session_seq,
            )

        await self._with_transaction(body)
