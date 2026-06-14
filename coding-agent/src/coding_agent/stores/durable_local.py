from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.protocols import TapeInfo, TapeSearchResult
from agentkit.storage.sqlite import (
    SQLiteCheckpointStore,
    SQLiteTapeStore,
    _checkpoint_snapshot_from_payload,
    _checkpoint_snapshot_to_payload,
    _entry_anchor_type,
    _entry_nested_str,
    _json_object_from_text,
    _optional_entry_str,
)

from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AgentInteractionRecord,
    RuntimeEventRecord,
    RunMessageSnapshotRecord,
    SQLiteRuntimeStore,
    _agent_run_from_sqlite_row,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _interaction_from_sqlite_row,
    _interaction_sqlite_values,
    _json_to_sql,
    _runtime_event_from_sqlite_row,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SQLiteSessionOwnerStore,
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
    _datetime_from_sqlite_text,
    _datetime_to_sqlite_text,
)
from coding_agent.server.stores.session_store import SQLiteSessionStore, SessionPayload


class SQLiteLocalDurableStore:
    """SQLite-local protected mutation facade.

    This facade is intentionally local to the SQLite bundle path. It does not replace
    the generic store protocols; it provides the transaction shape required by local
    durable fencing: owner epoch check, target ownership check, and mutation in the
    same SQLite transaction.
    """

    _SESSION_TAPES_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS session_tapes (
        session_id TEXT PRIMARY KEY,
        tape_id TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS session_tapes_tape_id_idx
        ON session_tapes (tape_id);
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_store = SQLiteSessionOwnerStore(path)
        with self._connect() as connection:
            connection.executescript(SQLiteSessionStore._CREATE_TABLE_SQL)
            connection.executescript(SQLiteTapeStore._CREATE_SCHEMA_SQL)
            connection.executescript(SQLiteCheckpointStore._CREATE_SCHEMA_SQL)
            connection.executescript(SQLiteRuntimeStore._CREATE_SCHEMA_SQL)
            connection.executescript(self._SESSION_TAPES_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def acquire_owner(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        return await self._owner_store.acquire_authority(
            session_id,
            owner_id,
            lease_seconds=lease_seconds,
        )

    async def renew_owner(
        self,
        authority: OwnerAuthority,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        return await self._owner_store.renew_authority(
            authority,
            lease_seconds=lease_seconds,
        )

    async def save_session(
        self,
        authority: OwnerAuthority,
        payload: SessionPayload,
    ) -> None:
        _require_json_object("session payload", payload)
        payload_id = payload.get("id")
        if payload_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        payload_session_id = payload.get("session_id")
        if (
            payload_session_id is not None
            and payload_session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        tape_id = payload.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(payload, sort_keys=True)),
            )

    async def delete_session(self, authority: OwnerAuthority) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            connection.execute(
                "DELETE FROM agent_http_sessions WHERE session_id = ?",
                (authority.session_id,),
            )
            connection.execute(
                "DELETE FROM session_tapes WHERE session_id = ?",
                (authority.session_id,),
            )

    def load_session(self, session_id: str) -> SessionPayload | None:
        _require_non_empty("session_id", session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_http_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict):
            raise TypeError("sqlite session payload must be a JSON object")
        return cast(SessionPayload, payload)

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

    async def create_agent_run(
        self,
        authority: OwnerAuthority,
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("agent run belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            existing = connection.execute(
                "SELECT session_id FROM agent_runs WHERE run_id = ?",
                (record.run_id,),
            ).fetchone()
            if existing is not None and existing["session_id"] != authority.session_id:
                raise SessionOwnershipConflictError(
                    "agent run target belongs to another session"
                )
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, session_id, tape_id, parent_run_id, agent_id, status,
                    started_at, ended_at, metadata, result, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _agent_run_sqlite_values(record),
            )
        return record

    async def update_agent_run(
        self,
        authority: OwnerAuthority,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, Any],
        result: dict[str, Any],
        error: str | None,
    ) -> AgentRunRecord:
        _require_non_empty("run_id", run_id)
        _require_non_empty("status", status)
        _require_json_object("metadata", metadata)
        _require_json_object("result", result)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_run_belongs_to_session(
                connection,
                run_id=run_id,
                session_id=authority.session_id,
            )
            connection.execute(
                """
                UPDATE agent_runs
                SET status = ?,
                    ended_at = ?,
                    metadata = ?,
                    result = ?,
                    error = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    None if ended_at is None else _datetime_to_json(ended_at),
                    _json_to_sql(cast(dict[str, Any], metadata)),
                    _json_to_sql(cast(dict[str, Any], result)),
                    error,
                    run_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"agent run not found after update: {run_id}")
        return _agent_run_from_sqlite_row(row)

    async def append_runtime_event(
        self,
        authority: OwnerAuthority,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_run_belongs_to_session(
                connection,
                run_id=record.run_id,
                session_id=authority.session_id,
            )
            existing = connection.execute(
                """
                SELECT runtime_events.*
                FROM runtime_events
                JOIN agent_runs ON agent_runs.run_id = runtime_events.run_id
                WHERE runtime_events.event_id = ?
                """,
                (record.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["run_id"] != record.run_id:
                    raise SessionOwnershipConflictError(
                        "runtime event target belongs to another run"
                    )
                return _runtime_event_from_sqlite_row(existing)
            connection.execute(
                """
                INSERT INTO runtime_events (
                    event_id, run_id, event_kind, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.run_id,
                    record.event_kind,
                    _json_to_sql(record.payload),
                    _datetime_to_json(record.created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM runtime_events WHERE event_id = ?",
                (record.event_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("sqlite runtime event insert returned no row")
        return _runtime_event_from_sqlite_row(row)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        _require_non_empty("run_id", run_id)
        if after_sequence < 0:
            raise ValueError("after_sequence must be >= 0")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (run_id, after_sequence, limit),
            ).fetchall()
        return [_runtime_event_from_sqlite_row(row) for row in rows]

    async def save_message_snapshot(
        self,
        authority: OwnerAuthority,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_run_belongs_to_session(
                connection,
                run_id=record.run_id,
                session_id=authority.session_id,
            )
            existing = connection.execute(
                """
                SELECT run_message_snapshots.run_id, agent_runs.session_id
                FROM run_message_snapshots
                JOIN agent_runs ON agent_runs.run_id = run_message_snapshots.run_id
                WHERE run_message_snapshots.snapshot_id = ?
                """,
                (record.snapshot_id,),
            ).fetchone()
            if existing is not None and (
                existing["run_id"] != record.run_id
                or existing["session_id"] != authority.session_id
            ):
                raise SessionOwnershipConflictError(
                    "message snapshot target belongs to another session"
                )
            connection.execute(
                """
                INSERT INTO run_message_snapshots (
                    snapshot_id, run_id, messages, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id)
                DO UPDATE SET
                    messages = excluded.messages,
                    metadata = excluded.metadata,
                    created_at = excluded.created_at
                """,
                (
                    record.snapshot_id,
                    record.run_id,
                    _json_to_sql(cast(list[Any], record.messages)),
                    _json_to_sql(record.metadata),
                    _datetime_to_json(record.created_at),
                ),
            )
        return record

    async def create_agent_interaction(
        self,
        authority: OwnerAuthority,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_run_belongs_to_session(
                connection,
                run_id=record.run_id,
                session_id=authority.session_id,
            )
            existing = connection.execute(
                """
                SELECT agent_interactions.run_id, agent_runs.session_id
                FROM agent_interactions
                JOIN agent_runs ON agent_runs.run_id = agent_interactions.run_id
                WHERE agent_interactions.interaction_id = ?
                """,
                (record.interaction_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["run_id"] != record.run_id
                    or existing["session_id"] != authority.session_id
                ):
                    raise SessionOwnershipConflictError(
                        "interaction target belongs to another session"
                    )
                row = connection.execute(
                    "SELECT * FROM agent_interactions WHERE interaction_id = ?",
                    (record.interaction_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        "sqlite agent interaction lookup returned no row"
                    )
                return _interaction_from_sqlite_row(row)
            connection.execute(
                """
                INSERT INTO agent_interactions (
                    interaction_id, run_id, interaction_kind, status,
                    request_payload, response_payload, metadata,
                    created_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _interaction_sqlite_values(record),
            )
            row = connection.execute(
                "SELECT * FROM agent_interactions WHERE interaction_id = ?",
                (record.interaction_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("sqlite agent interaction insert returned no row")
        return _interaction_from_sqlite_row(row)

    async def resolve_agent_interaction(
        self,
        authority: OwnerAuthority,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, Any],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            existing = connection.execute(
                """
                SELECT agent_interactions.*
                FROM agent_interactions
                JOIN agent_runs ON agent_runs.run_id = agent_interactions.run_id
                WHERE agent_interactions.interaction_id = ?
                  AND agent_runs.session_id = ?
                """,
                (interaction_id, authority.session_id),
            ).fetchone()
            if existing is None:
                raise SessionOwnershipConflictError(
                    "interaction target belongs to another session"
                )
            if existing["resolved_at"] is None:
                connection.execute(
                    """
                    UPDATE agent_interactions
                    SET status = ?,
                        response_payload = ?,
                        resolved_at = ?
                    WHERE interaction_id = ?
                    """,
                    (
                        status,
                        _json_to_sql(response_payload),
                        _datetime_to_json(resolved_at),
                        interaction_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM agent_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("sqlite agent interaction resolve returned no row")
        return _interaction_from_sqlite_row(row)

    async def claim_attached_executor_run(
        self,
        authorities: Mapping[str, OwnerAuthority],
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, Any],
    ) -> AgentRunRecord | None:
        _require_non_empty("executor_kind", executor_kind)
        _require_json_object("claim_metadata", claim_metadata)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            filter_sql = ""
            values: list[object] = []
            if session_id is not None:
                filter_sql = "AND session_id = ?"
                values.append(session_id)
            rows = connection.execute(
                f"""
                SELECT * FROM agent_runs
                WHERE status IN ('requested', 'expired')
                  {filter_sql}
                ORDER BY started_at, run_id
                """,
                values,
            ).fetchall()
            candidates = [_agent_run_from_sqlite_row(row) for row in rows]
            candidates = [
                run
                for run in candidates
                if run.metadata.get("executor_ref_kind")
                in {"external_worker", "local_attached"}
                and run.metadata.get("executor_kind") == executor_kind
            ]
            selected: AgentRunRecord | None = None
            authority: OwnerAuthority | None = None
            for candidate in candidates:
                candidate_authority = authorities.get(candidate.session_id)
                if candidate_authority is None:
                    continue
                try:
                    self._assert_authority(connection, candidate_authority)
                except SessionOwnershipConflictError:
                    continue
                selected = candidate
                authority = candidate_authority
                break
            if selected is None or authority is None:
                return None
            metadata = cast(dict[str, Any], {**selected.metadata, **claim_metadata})
            connection.execute(
                """
                UPDATE agent_runs
                SET status = 'claimed',
                    metadata = ?
                WHERE run_id = ?
                  AND session_id = ?
                """,
                (
                    _json_to_sql(metadata),
                    selected.run_id,
                    authority.session_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (selected.run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"agent run not found after claim: {selected.run_id}")
        return _agent_run_from_sqlite_row(row)

    def session_id_for_run(self, run_id: str) -> str | None:
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        if not isinstance(session_id, str):
            raise TypeError("agent run session_id must be text")
        return session_id

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
                DELETE FROM checkpoints
                WHERE tape_id = ?
                  AND session_id = ?
                  AND entry_count > ?
                """,
                (meta.tape_id, authority.session_id, meta.entry_count),
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

    def _assert_authority(
        self,
        connection: sqlite3.Connection,
        authority: OwnerAuthority,
    ) -> None:
        row = connection.execute(
            """
            SELECT owner_id, lease_expires_at, fencing_token
            FROM session_owners
            WHERE session_id = ?
            """,
            (authority.session_id,),
        ).fetchone()
        if row is None:
            raise SessionOwnershipConflictError(
                "session owner lease is missing",
                reason=SessionOwnershipConflictReason.MISSING_OWNER,
            )
        lease_expires_at = _datetime_from_sqlite_text(row["lease_expires_at"])
        if lease_expires_at <= datetime.now(UTC):
            raise SessionOwnershipConflictError(
                "session owner lease has expired",
                reason=SessionOwnershipConflictReason.EXPIRED_LEASE,
            )
        if (
            row["owner_id"] != authority.owner_id
            or row["fencing_token"] != authority.epoch
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    def _bind_tape(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        tape_id: str,
    ) -> None:
        now = _datetime_to_sqlite_text(datetime.now(UTC))
        session_row = connection.execute(
            "SELECT tape_id FROM session_tapes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session_row is not None and session_row["tape_id"] != tape_id:
            raise SessionOwnershipConflictError("session tape target cannot be rebound")
        tape_row = connection.execute(
            "SELECT session_id FROM session_tapes WHERE tape_id = ?",
            (tape_id,),
        ).fetchone()
        if tape_row is not None and tape_row["session_id"] != session_id:
            raise SessionOwnershipConflictError(
                "tape target belongs to another session"
            )
        connection.execute(
            """
            INSERT INTO session_tapes (session_id, tape_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET
                tape_id = excluded.tape_id,
                updated_at = excluded.updated_at
            """,
            (session_id, tape_id, now, now),
        )

    def _assert_tape_belongs_to_session(
        self,
        connection: sqlite3.Connection,
        *,
        tape_id: str,
        session_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT session_id
            FROM session_tapes
            WHERE tape_id = ?
            """,
            (tape_id,),
        ).fetchone()
        if row is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        if row["session_id"] != session_id:
            raise SessionOwnershipConflictError(
                "tape target belongs to another session"
            )

    def _assert_run_belongs_to_session(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        session_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT session_id FROM agent_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"agent run not found: {run_id}")
        if row["session_id"] != session_id:
            raise SessionOwnershipConflictError("agent run belongs to another session")


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_json_object(field_name: str, value: object) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")


def _row_required_int(row: sqlite3.Row | None, key: str, *, context: str) -> int:
    if row is None:
        raise TypeError(f"{context} row is missing")
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"{context} {key} must be an int")
    return value


class FencedSQLiteTapeStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteTapeStore(path)
        self._authority_for_session = authority_for_session

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        session_id = self._require_session_id_for_tape(tape_id)
        await self._durable_store.append_tape_entries(
            self._authority_for_session(session_id),
            tape_id,
            entries,
        )

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        return await self._delegate.load(tape_id)

    async def list_ids(self) -> list[str]:
        return await self._delegate.list_ids()

    async def truncate(self, tape_id: str, keep: int) -> None:
        session_id = self._require_session_id_for_tape(tape_id)
        await self._durable_store.truncate_tape(
            self._authority_for_session(session_id),
            tape_id,
            keep,
        )

    async def info(self, tape_id: str) -> TapeInfo | None:
        return await self._delegate.info(tape_id)

    async def search(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        return await self._delegate.search(
            tape_id=tape_id,
            kind=kind,
            run_id=run_id,
            tool_call_id=tool_call_id,
            anchor_type=anchor_type,
            limit=limit,
        )

    def _require_session_id_for_tape(self, tape_id: str) -> str:
        session_id = self._durable_store.session_id_for_tape(tape_id)
        if session_id is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        return session_id


class FencedSQLiteCheckpointStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteCheckpointStore(path)
        self._authority_for_session = authority_for_session

    async def save(self, snapshot: CheckpointSnapshot) -> None:
        session_id = snapshot.meta.session_id
        if session_id is None:
            raise SessionOwnershipConflictError(
                "checkpoint target is not bound to a session"
            )
        await self._durable_store.save_checkpoint(
            self._authority_for_session(session_id),
            snapshot,
        )

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        return await self._delegate.load(checkpoint_id)

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        return await self._delegate.list_by_tape(tape_id)

    async def delete(self, checkpoint_id: str) -> None:
        session_id = self._durable_store.session_id_for_checkpoint(checkpoint_id)
        if session_id is None:
            return
        await self._durable_store.delete_checkpoint(
            self._authority_for_session(session_id),
            checkpoint_id,
        )


class FencedSQLiteRuntimeStore:
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
        authorities: Callable[[], Mapping[str, OwnerAuthority]],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = SQLiteRuntimeStore(path)
        self._authority_for_session = authority_for_session
        self._authorities = authorities

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        return await self._durable_store.create_agent_run(
            self._authority_for_session(record.session_id),
            record,
        )

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, Any],
        result: dict[str, Any],
        error: str | None,
    ) -> AgentRunRecord:
        session_id = self._require_session_id_for_run(run_id)
        return await self._durable_store.update_agent_run(
            self._authority_for_session(session_id),
            run_id,
            status=status,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return await self._delegate.load_agent_run(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._delegate.list_agent_runs(session_id)

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, Any],
    ) -> AgentRunRecord | None:
        return await self._durable_store.claim_attached_executor_run(
            self._authorities(),
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, Any],
    ) -> AgentRunRecord | None:
        return await self._durable_store.claim_attached_executor_run(
            self._authorities(),
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.append_runtime_event(
            self._authority_for_session(session_id),
            record,
        )

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        return await self._delegate.load_runtime_event(event_id)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await self._delegate.replay_runtime_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.save_message_snapshot(
            self._authority_for_session(session_id),
            record,
        )

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return await self._delegate.load_message_snapshot(snapshot_id)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        return await self._delegate.list_message_snapshots(run_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        session_id = self._require_session_id_for_run(record.run_id)
        return await self._durable_store.create_agent_interaction(
            self._authority_for_session(session_id),
            record,
        )

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        return await self._delegate.load_agent_interaction(interaction_id)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._delegate.list_agent_interactions(run_id)

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, Any],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        interaction = await self._delegate.load_agent_interaction(interaction_id)
        if interaction is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        session_id = self._require_session_id_for_run(interaction.run_id)
        return await self._durable_store.resolve_agent_interaction(
            self._authority_for_session(session_id),
            interaction_id,
            status=status,
            response_payload=response_payload,
            resolved_at=resolved_at,
        )

    def _require_session_id_for_run(self, run_id: str) -> str:
        session_id = self._durable_store.session_id_for_run(run_id)
        if session_id is None:
            raise KeyError(f"agent run not found: {run_id}")
        return session_id
