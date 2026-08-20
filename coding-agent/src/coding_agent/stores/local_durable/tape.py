"""Fenced tape append/load/truncate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from agentkit.storage.sqlite import (
    _entry_anchor_type,
    _entry_nested_str,
    _json_object_from_text,
    _optional_entry_str,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
)
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
    _row_required_int,
)


class LocalTapeMixin:
    async def append_tape_entries(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        _require_non_empty("tape_id", tape_id)
        if not entries:
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=tape_id,
                session_id=authority.session_id,
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), -1) AS max_seq FROM tape_entries WHERE tape_id = ?",
                (tape_id,),
            ).fetchone()
            max_seq = _row_required_int(row, "max_seq", context="tape max seq")
            values: list[tuple[object, ...]] = []
            now = datetime.now(UTC).isoformat()
            for offset, entry in enumerate(entries, start=1):
                _require_json_object("tape entry", entry)
                values.append(
                    (
                        tape_id,
                        max_seq + offset,
                        json.dumps(entry, sort_keys=True),
                        _optional_entry_str(entry, "kind"),
                        _entry_nested_str(entry, "run_id"),
                        _entry_nested_str(entry, "tool_call_id"),
                        _entry_anchor_type(entry),
                        now,
                    )
                )
            connection.executemany(
                """
                INSERT INTO tape_entries (
                    tape_id, seq, entry_json, kind, run_id, tool_call_id,
                    anchor_type, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    async def load_tape(self, tape_id: str) -> list[dict[str, Any]]:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT entry_json
                FROM tape_entries
                WHERE tape_id = ?
                ORDER BY seq
                """,
                (tape_id,),
            ).fetchall()
        return [
            _json_object_from_text(row["entry_json"], context="tape entry")
            for row in rows
        ]

    async def truncate_tape(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        keep: int,
    ) -> None:
        _require_non_empty("tape_id", tape_id)
        if keep < 0:
            raise ValueError("keep must be >= 0")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=tape_id,
                session_id=authority.session_id,
            )
            connection.execute(
                "DELETE FROM tape_entries WHERE tape_id = ? AND seq >= ?",
                (tape_id, keep),
            )

    def session_id_for_tape(self, tape_id: str) -> str | None:
        _require_non_empty("tape_id", tape_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM session_tapes WHERE tape_id = ?",
                (tape_id,),
            ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        if not isinstance(session_id, str):
            raise TypeError("session_tapes session_id must be text")
        return session_id
