"""SQLite runtime store backend."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Final, cast
from coding_agent.stores.rtstore.sqlite_schema import CREATE_SCHEMA_SQL
from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.sqlite_codec import (
    _agent_run_from_sqlite_row,
    _agent_run_sqlite_values,
    _interaction_from_sqlite_row,
    _interaction_sqlite_values,
    _json_to_sql,
    _message_snapshot_from_sqlite_row,
    _runtime_event_from_sqlite_row,
)
from coding_agent.stores.rtstore.payload import _datetime_to_json
from coding_agent.stores.rtstore.validate import (
    _normalize_optional_error,
    _require_datetime,
    _require_json_object,
    _require_non_empty,
    _require_non_negative_int,
    _require_positive_int,
)


class SQLiteRuntimeStore:
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000
    _CREATE_SCHEMA_SQL: Final[str] = CREATE_SCHEMA_SQL

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)

    @classmethod
    def _ensure_schema(cls, connection: sqlite3.Connection) -> None:
        connection.executescript(cls._CREATE_SCHEMA_SQL)
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        if "superseded_by_checkpoint_id" not in columns:
            connection.execute(
                "ALTER TABLE agent_runs ADD COLUMN superseded_by_checkpoint_id TEXT"
            )
        if "superseded_at" not in columns:
            connection.execute("ALTER TABLE agent_runs ADD COLUMN superseded_at TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    run_id, session_id, tape_id, parent_run_id, agent_id, status,
                    started_at, ended_at, metadata, result, error,
                    superseded_by_checkpoint_id, superseded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id)
                DO UPDATE SET
                    session_id = excluded.session_id,
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
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        _require_non_empty("run_id", run_id)
        _require_non_empty("status", status)
        if ended_at is not None:
            _require_datetime("ended_at", ended_at)
        _require_json_object("metadata", metadata)
        _require_json_object("result", result)
        error = _normalize_optional_error(error)
        with self._lock, self._connect() as connection:
            existing_row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing_row is None:
                raise KeyError(f"agent run not found: {run_id}")
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
                    _json_to_sql(metadata),
                    _json_to_sql(result),
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

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _agent_run_from_sqlite_row(row)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        _require_non_empty("session_id", session_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE session_id = ?
                ORDER BY started_at, run_id
                """,
                (session_id,),
            ).fetchall()
        return [_agent_run_from_sqlite_row(row) for row in rows]

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        _require_non_empty("executor_kind", executor_kind)
        _require_json_object("claim_metadata", claim_metadata)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE status IN ('requested', 'expired')
                ORDER BY started_at, run_id
                """
            ).fetchall()
            candidates = [
                _agent_run_from_sqlite_row(row)
                for row in rows
                if row["session_id"] == session_id or session_id is None
            ]
            candidates = [
                run
                for run in candidates
                if run.metadata.get("executor_ref_kind")
                in {"external_worker", "local_attached"}
                and run.metadata.get("executor_kind") == executor_kind
                and run.superseded_at is None
            ]
            if not candidates:
                return None
            selected = candidates[0]
            metadata = cast(JSONObject, {**selected.metadata, **claim_metadata})
            connection.execute(
                """
                UPDATE agent_runs
                SET status = 'claimed',
                    metadata = ?
                WHERE run_id = ?
                """,
                (_json_to_sql(metadata), selected.run_id),
            )
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (selected.run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"agent run not found after claim: {selected.run_id}")
        return _agent_run_from_sqlite_row(row)

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO runtime_events (
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
        limit: int = _DEFAULT_REPLAY_LIMIT,
    ) -> list[RuntimeEventRecord]:
        _require_non_empty("run_id", run_id)
        _require_non_negative_int("after_sequence", after_sequence)
        _require_positive_int("limit", limit)
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

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        _require_non_empty("event_id", event_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return _runtime_event_from_sqlite_row(row)

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO run_message_snapshots (
                    snapshot_id, run_id, messages, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id)
                DO UPDATE SET
                    run_id = excluded.run_id,
                    messages = excluded.messages,
                    metadata = excluded.metadata,
                    created_at = excluded.created_at
                """,
                (
                    record.snapshot_id,
                    record.run_id,
                    _json_to_sql(record.messages),
                    _json_to_sql(record.metadata),
                    _datetime_to_json(record.created_at),
                ),
            )
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        _require_non_empty("snapshot_id", snapshot_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM run_message_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return _message_snapshot_from_sqlite_row(row)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_message_snapshots
                WHERE run_id = ?
                ORDER BY created_at, snapshot_id
                """,
                (run_id,),
            ).fetchall()
        return [_message_snapshot_from_sqlite_row(row) for row in rows]

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_interactions (
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
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        _require_non_empty("interaction_id", interaction_id)
        _require_non_empty("status", status)
        _require_json_object("response_payload", response_payload)
        _require_datetime("resolved_at", resolved_at)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM agent_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"agent interaction not found: {interaction_id}")
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
            raise KeyError(
                f"agent interaction not found after resolve: {interaction_id}"
            )
        return _interaction_from_sqlite_row(row)

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        _require_non_empty("interaction_id", interaction_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_interactions WHERE interaction_id = ?",
                (interaction_id,),
            ).fetchone()
        if row is None:
            return None
        return _interaction_from_sqlite_row(row)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        _require_non_empty("run_id", run_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_interactions
                WHERE run_id = ?
                ORDER BY created_at, interaction_id
                """,
                (run_id,),
            ).fetchall()
        return [_interaction_from_sqlite_row(row) for row in rows]
