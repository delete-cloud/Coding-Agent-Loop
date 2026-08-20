"""Fenced checkpoint save/restore and topic reconcile."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from agentkit.checkpoint.models import CheckpointSnapshot
from agentkit.storage.sqlite import (
    _checkpoint_snapshot_from_payload,
    _checkpoint_snapshot_to_payload,
    _entry_anchor_type,
    _entry_nested_str,
    _json_object_from_text,
    _optional_entry_str,
)
from coding_agent.stores.runtime_store import (
    _datetime_to_json,
)
from coding_agent.topics.store import (
    _datetime_to_sqlite_text as _topic_datetime_to_sqlite_text,
    _require_datetime as _topic_require_datetime,
    _require_non_empty as _topic_require_non_empty,
    _require_non_negative_int as _topic_require_non_negative_int,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.server.stores.session_store import SessionPayload
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
)


class LocalCheckpointMixin:
    async def save_checkpoint(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
    ) -> None:
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError("checkpoint belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=meta.tape_id,
                session_id=authority.session_id,
            )
            existing = connection.execute(
                """
                SELECT session_id, tape_id
                FROM checkpoints
                WHERE checkpoint_id = ?
                """,
                (meta.checkpoint_id,),
            ).fetchone()
            if existing is not None and (
                existing["session_id"] != authority.session_id
                or existing["tape_id"] != meta.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "checkpoint target belongs to another session"
                )
            payload = _checkpoint_snapshot_to_payload(snapshot)
            connection.execute(
                """
                INSERT INTO checkpoints (
                    checkpoint_id, tape_id, session_id, entry_count, window_start,
                    label, snapshot_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id)
                DO UPDATE SET
                    entry_count = excluded.entry_count,
                    window_start = excluded.window_start,
                    label = excluded.label,
                    snapshot_json = excluded.snapshot_json,
                    created_at = excluded.created_at
                """,
                (
                    meta.checkpoint_id,
                    meta.tape_id,
                    meta.session_id,
                    meta.entry_count,
                    meta.window_start,
                    meta.label,
                    json.dumps(payload, sort_keys=True),
                    meta.created_at.isoformat(),
                ),
            )

    async def load_checkpoint(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        _require_non_empty("checkpoint_id", checkpoint_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return _checkpoint_snapshot_from_payload(
            _json_object_from_text(row["snapshot_json"], context="checkpoint snapshot")
        )

    async def delete_checkpoint(
        self,
        authority: OwnerAuthority,
        checkpoint_id: str,
    ) -> None:
        _require_non_empty("checkpoint_id", checkpoint_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            row = connection.execute(
                "SELECT session_id FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if row is None:
                return
            if row["session_id"] != authority.session_id:
                raise SessionOwnershipConflictError(
                    "checkpoint target belongs to another session"
                )
            connection.execute(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )

    async def restore_checkpoint_state(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
        session_payload: SessionPayload,
    ) -> None:
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError("checkpoint belongs to another session")
        if session_payload.get("id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        if session_payload.get("tape_id") != meta.tape_id:
            raise SessionOwnershipConflictError(
                "checkpoint restore session payload has mismatched tape id"
            )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=meta.tape_id,
                session_id=authority.session_id,
            )
            checkpoint_row = connection.execute(
                """
                SELECT session_id, tape_id
                FROM checkpoints
                WHERE checkpoint_id = ?
                """,
                (meta.checkpoint_id,),
            ).fetchone()
            if checkpoint_row is None:
                raise KeyError(f"checkpoint not found: {meta.checkpoint_id}")
            if (
                checkpoint_row["session_id"] != authority.session_id
                or checkpoint_row["tape_id"] != meta.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "checkpoint target belongs to another session"
                )
            connection.execute(
                "DELETE FROM tape_entries WHERE tape_id = ?",
                (meta.tape_id,),
            )
            now = datetime.now(UTC).isoformat()
            values: list[tuple[object, ...]] = []
            for seq, entry in enumerate(snapshot.tape_entries):
                _require_json_object("tape entry", entry)
                values.append(
                    (
                        meta.tape_id,
                        seq,
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
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(session_payload, sort_keys=True)),
            )
            connection.execute(
                """
                UPDATE agent_runs
                SET superseded_by_checkpoint_id = ?,
                    superseded_at = ?
                WHERE session_id = ?
                  AND julianday(started_at) > julianday(?)
                  AND superseded_at IS NULL
                """,
                (
                    meta.checkpoint_id,
                    now,
                    authority.session_id,
                    _datetime_to_json(meta.created_at),
                ),
            )
            self._reconcile_topics_after_checkpoint_restore(
                connection,
                tape_id=meta.tape_id,
                entry_count=meta.entry_count,
                checkpoint_created_at=meta.created_at,
            )
            connection.execute(
                """
                DELETE FROM checkpoints
                WHERE tape_id = ?
                  AND session_id = ?
                  AND entry_count > ?
                """,
                (meta.tape_id, authority.session_id, meta.entry_count),
            )
            connection.execute(
                """
                DELETE FROM session_mailbox_slots
                WHERE session_id = ?
                  AND slot_id LIKE 'turn:%'
                """,
                (authority.session_id,),
            )
            self._open_projection_epoch(connection, authority.session_id)

    def _reconcile_topics_after_checkpoint_restore(
        self,
        connection: sqlite3.Connection,
        *,
        tape_id: str,
        entry_count: int,
        checkpoint_created_at: datetime,
    ) -> None:
        _topic_require_non_empty("tape_id", tape_id)
        _topic_require_non_negative_int("entry_count", entry_count)
        _topic_require_datetime("checkpoint_created_at", checkpoint_created_at)
        checkpoint_created_at_text = _topic_datetime_to_sqlite_text(
            checkpoint_created_at
        )
        connection.execute(
            """
            DELETE FROM topic_recall_links
            WHERE source_topic_id IN (
                SELECT topic_id FROM topics WHERE tape_id = ?
            )
               OR recalled_topic_id IN (
                SELECT topic_id FROM topics WHERE tape_id = ?
            )
            """,
            (tape_id, tape_id),
        )
        connection.execute(
            """
            DELETE FROM topic_costs
            WHERE topic_id IN (
                SELECT topic_id FROM topics WHERE tape_id = ?
            )
            """,
            (tape_id,),
        )
        connection.execute(
            """
            DELETE FROM topic_anchors
            WHERE tape_id = ?
              AND seq >= ?
            """,
            (tape_id, entry_count),
        )
        connection.execute(
            """
            DELETE FROM topics
            WHERE tape_id = ?
              AND (
                topic_initial_seq >= ?
                OR created_at > ?
              )
            """,
            (tape_id, entry_count, checkpoint_created_at_text),
        )
        connection.execute(
            """
            UPDATE topics
            SET status = 'open',
                summary = NULL,
                topic_finalized_seq = NULL,
                finalized_at = NULL,
                metadata = '{}',
                updated_at = ?
            WHERE tape_id = ?
              AND status IN ('finalized', 'aborted')
              AND (
                topic_finalized_seq >= ?
                OR finalized_at > ?
              )
            """,
            (
                _topic_datetime_to_sqlite_text(datetime.now(UTC)),
                tape_id,
                entry_count,
                checkpoint_created_at_text,
            ),
        )

    def session_id_for_checkpoint(self, checkpoint_id: str) -> str | None:
        _require_non_empty("checkpoint_id", checkpoint_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        if not isinstance(session_id, str):
            raise TypeError("checkpoint session_id must be text")
        return session_id
