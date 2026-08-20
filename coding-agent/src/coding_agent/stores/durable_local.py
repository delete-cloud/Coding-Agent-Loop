from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    CursorEpochMismatchError,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EventRecord,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    ProjectionCursor,
    RawCursor,
    RetentionFloorReplay,
    RuntimeEventRecord,
    RunMessageSnapshotRecord,
    SQLiteRuntimeStore,
    SessionFactSourceState,
    TrustedHandoff,
    _agent_run_from_sqlite_row,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _interaction_from_sqlite_row,
    _interaction_sqlite_values,
    _json_object_from_sql,
    _json_to_sql,
    _runtime_event_from_sqlite_row,
    _sqlite_optional_int,
    _sqlite_optional_str,
    _sqlite_required_datetime,
    _sqlite_required_int,
    _require_positive_int,
    _sqlite_required_str,
    assert_projection_binding,
    assert_raw_cursor_not_expired,
    assert_trusted_handoff,
    effect_status_may_replace,
    format_u64,
    parse_u64,
    receipt_generation_may_replace,
    stored_trusted_handoff,
)
from coding_agent.topics.store import (
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
    TopicStatus,
    _datetime_to_sqlite_text as _topic_datetime_to_sqlite_text,
    _json_to_sqlite_text as _topic_json_to_sqlite_text,
    _optional_datetime_to_sqlite_text as _topic_optional_datetime_to_sqlite_text,
    _require_datetime as _topic_require_datetime,
    _require_json_object as _topic_require_json_object,
    _require_non_empty as _topic_require_non_empty,
    _require_non_negative_int as _topic_require_non_negative_int,
    _require_optional_display_text as _topic_require_optional_display_text,
    _required_sqlite_row,
    _topic_anchor_from_sqlite_row,
    _topic_cost_from_sqlite_row,
    _topic_from_sqlite_row,
    _topic_recall_link_from_sqlite_row,
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
    _HARNESS_FACT_SOURCE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS session_fact_source (
        session_id TEXT PRIMARY KEY,
        session_seq INTEGER NOT NULL,
        retention_floor INTEGER NOT NULL,
        projection TEXT NOT NULL,
        projection_epoch INTEGER NOT NULL,
        trusted_handoff_seq INTEGER,
        trusted_handoff_epoch INTEGER,
        trusted_handoff_projection TEXT,
        trusted_handoff_payload TEXT,
        trusted_handoff_accepted_at TEXT
    );
    CREATE TABLE IF NOT EXISTS session_event_records (
        session_id TEXT NOT NULL,
        session_seq INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        event_kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        projection_epoch INTEGER NOT NULL,
        PRIMARY KEY (session_id, session_seq)
    );
    CREATE TABLE IF NOT EXISTS session_mailbox_slots (
        session_id TEXT NOT NULL,
        slot_id TEXT NOT NULL,
        lane TEXT NOT NULL,
        disposition TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (session_id, slot_id)
    );
    CREATE TABLE IF NOT EXISTS session_effect_slots (
        session_id TEXT NOT NULL,
        effect_id TEXT NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (session_id, effect_id)
    );
    CREATE TABLE IF NOT EXISTS session_receipt_slots (
        session_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        generation TEXT NOT NULL,
        payload TEXT NOT NULL,
        compensation_effect_id TEXT,
        PRIMARY KEY (session_id, receipt_id)
    );
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
            SQLiteRuntimeStore._ensure_schema(connection)
            connection.executescript(SQLiteTopicStore._CREATE_SCHEMA_SQL)
            connection.executescript(self._SESSION_TAPES_SCHEMA_SQL)
            connection.executescript(self._HARNESS_FACT_SOURCE_SCHEMA_SQL)
            self._ensure_harness_fact_source_columns(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
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

    def session_id_for_topic(self, topic_id: str) -> str | None:
        _topic_require_non_empty("topic_id", topic_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM topics WHERE topic_id = ?",
                (topic_id,),
            ).fetchone()
        if row is None:
            return None
        session_id = row["session_id"]
        if not isinstance(session_id, str):
            raise TypeError("topic session_id must be text")
        return session_id

    async def create_topic(
        self,
        authority: OwnerAuthority,
        record: TopicRecord,
    ) -> TopicRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("topic target belongs to another owner")
        now = _topic_datetime_to_sqlite_text(datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_tape_belongs_to_session(
                connection,
                tape_id=record.tape_id,
                session_id=authority.session_id,
            )
            connection.execute(
                """
                INSERT INTO topics (
                    topic_id,
                    tape_id,
                    session_id,
                    kind,
                    status,
                    title,
                    summary,
                    owner,
                    topic_initial_seq,
                    topic_finalized_seq,
                    created_at,
                    finalized_at,
                    metadata,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO NOTHING
                """,
                (
                    record.topic_id,
                    record.tape_id,
                    record.session_id,
                    record.kind,
                    record.status,
                    record.title,
                    record.summary,
                    record.owner,
                    record.topic_initial_seq,
                    record.topic_finalized_seq,
                    _topic_datetime_to_sqlite_text(record.created_at),
                    _topic_optional_datetime_to_sqlite_text(record.finalized_at),
                    _topic_json_to_sqlite_text(record.metadata),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM topics WHERE topic_id = ?",
                (record.topic_id,),
            ).fetchone()
            topic = _topic_from_sqlite_row(_required_sqlite_row(row, "topic insert"))
            if (
                topic.session_id != authority.session_id
                or topic.tape_id != record.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            return topic

    async def finalize_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            "finalized",
            "finalize",
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def abort_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            "aborted",
            "abort",
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def _close_topic(
        self,
        authority: OwnerAuthority,
        status: TopicStatus,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        _topic_require_non_empty("topic_id", topic_id)
        _topic_require_optional_display_text("summary", summary)
        if topic_finalized_seq is not None:
            _topic_require_non_negative_int("topic_finalized_seq", topic_finalized_seq)
        _topic_require_datetime("finalized_at", finalized_at)
        _topic_require_json_object("metadata", metadata)
        if status == "finalized" and topic_finalized_seq is None:
            raise ValueError("topic_finalized_seq must be provided for finalize")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(connection, authority, topic_id)
            if status == "finalized":
                row = connection.execute(
                    """
                    UPDATE topics
                    SET status = ?,
                        summary = ?,
                        topic_finalized_seq = ?,
                        finalized_at = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE topic_id = ? AND status = 'open'
                      AND ? >= topic_initial_seq
                    RETURNING *
                    """,
                    (
                        status,
                        summary,
                        topic_finalized_seq,
                        _topic_datetime_to_sqlite_text(finalized_at),
                        _topic_json_to_sqlite_text(metadata),
                        _topic_datetime_to_sqlite_text(datetime.now(UTC)),
                        topic_id,
                        topic_finalized_seq,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE topics
                    SET status = ?,
                        summary = ?,
                        topic_finalized_seq = ?,
                        finalized_at = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE topic_id = ? AND status = 'open'
                      AND (? IS NULL OR ? >= topic_initial_seq)
                    RETURNING *
                    """,
                    (
                        status,
                        summary,
                        topic_finalized_seq,
                        _topic_datetime_to_sqlite_text(finalized_at),
                        _topic_json_to_sqlite_text(metadata),
                        _topic_datetime_to_sqlite_text(datetime.now(UTC)),
                        topic_id,
                        topic_finalized_seq,
                        topic_finalized_seq,
                    ),
                ).fetchone()
        if row is None:
            raise KeyError(f"open topic not found for {operation}: {topic_id}")
        return _topic_from_sqlite_row(row)

    async def delete_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
    ) -> None:
        _topic_require_non_empty("topic_id", topic_id)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(connection, authority, topic_id)
            connection.execute(
                """
                DELETE FROM topic_recall_links
                WHERE source_topic_id = ? OR recalled_topic_id = ?
                """,
                (topic_id, topic_id),
            )
            connection.execute(
                "DELETE FROM topic_costs WHERE topic_id = ?",
                (topic_id,),
            )
            connection.execute(
                "DELETE FROM topic_anchors WHERE topic_id = ?",
                (topic_id,),
            )
            connection.execute(
                "DELETE FROM topics WHERE topic_id = ?",
                (topic_id,),
            )

    async def record_topic_anchor(
        self,
        authority: OwnerAuthority,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            topic_tape_id = self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.topic_id,
            )
            if record.tape_id != topic_tape_id:
                raise SessionOwnershipConflictError(
                    "topic anchor target belongs to another tape"
                )
            row = connection.execute(
                """
                INSERT INTO topic_anchors (
                    topic_id,
                    tape_id,
                    seq,
                    anchor_type,
                    entry_id,
                    metadata,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id, seq, anchor_type)
                DO UPDATE SET
                    tape_id = excluded.tape_id,
                    entry_id = excluded.entry_id,
                    metadata = excluded.metadata
                RETURNING *
                """,
                (
                    record.topic_id,
                    record.tape_id,
                    record.seq,
                    record.anchor_type,
                    record.entry_id,
                    _topic_json_to_sqlite_text(record.metadata),
                    _topic_datetime_to_sqlite_text(
                        record.created_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_anchor_from_sqlite_row(
            _required_sqlite_row(row, "topic anchor upsert")
        )

    async def record_recall_link(
        self,
        authority: OwnerAuthority,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.source_topic_id,
            )
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                record.recalled_topic_id,
            )
            row = connection.execute(
                """
                INSERT INTO topic_recall_links (
                    source_topic_id,
                    recalled_topic_id,
                    relation,
                    anchor_seq,
                    source_entry_start_seq,
                    source_entry_end_seq,
                    metadata,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_topic_id, recalled_topic_id, relation)
                DO UPDATE SET
                    anchor_seq = excluded.anchor_seq,
                    source_entry_start_seq = excluded.source_entry_start_seq,
                    source_entry_end_seq = excluded.source_entry_end_seq,
                    metadata = excluded.metadata
                RETURNING *
                """,
                (
                    record.source_topic_id,
                    record.recalled_topic_id,
                    record.relation,
                    record.anchor_seq,
                    record.source_entry_start_seq,
                    record.source_entry_end_seq,
                    _topic_json_to_sqlite_text(record.metadata),
                    _topic_datetime_to_sqlite_text(
                        record.created_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_recall_link_from_sqlite_row(
            _required_sqlite_row(row, "topic recall link upsert")
        )

    async def update_topic_cost(
        self,
        authority: OwnerAuthority,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            self._assert_topic_belongs_to_authority(
                connection,
                authority,
                delta.topic_id,
            )
            row = connection.execute(
                """
                INSERT INTO topic_costs (
                    topic_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    run_count,
                    action_count,
                    validation_count,
                    tool_call_count,
                    metadata,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id)
                DO UPDATE SET
                    prompt_tokens = topic_costs.prompt_tokens
                        + excluded.prompt_tokens,
                    completion_tokens = topic_costs.completion_tokens
                        + excluded.completion_tokens,
                    total_tokens = topic_costs.total_tokens
                        + excluded.total_tokens,
                    run_count = topic_costs.run_count + excluded.run_count,
                    action_count = topic_costs.action_count + excluded.action_count,
                    validation_count = topic_costs.validation_count
                        + excluded.validation_count,
                    tool_call_count = topic_costs.tool_call_count
                        + excluded.tool_call_count,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                (
                    delta.topic_id,
                    delta.prompt_tokens,
                    delta.completion_tokens,
                    delta.total_tokens,
                    delta.run_count,
                    delta.action_count,
                    delta.validation_count,
                    delta.tool_call_count,
                    _topic_json_to_sqlite_text(delta.metadata),
                    _topic_datetime_to_sqlite_text(
                        delta.updated_at or datetime.now(UTC)
                    ),
                ),
            ).fetchone()
        return _topic_cost_from_sqlite_row(
            _required_sqlite_row(row, "topic cost upsert")
        )

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
                    started_at, ended_at, metadata, result, error,
                    superseded_by_checkpoint_id, superseded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                and run.superseded_at is None
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

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        _require_json_object("session payload", unit.session_state)
        if unit.session_state.get("id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        payload_session_id = unit.session_state.get("session_id")
        if (
            payload_session_id is not None
            and payload_session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("agent run belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            if unit.run_state is not None:
                if unit.run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                self._assert_tape_belongs_to_session(
                    connection,
                    tape_id=unit.run_state.tape_id,
                    session_id=authority.session_id,
                )
            fact = self._ensure_fact_source(connection, authority.session_id)
            existing_row = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE event_id = ?
                """,
                (unit.event.event_id,),
            ).fetchone()
            idempotent = False
            if existing_row is not None:
                existing_event = _event_record_from_sqlite_row(existing_row)
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
                    connection.execute(
                        """
                        UPDATE session_event_records
                        SET projection_epoch = ?
                        WHERE event_id = ?
                        """,
                        (fact.projection_epoch_int, unit.event.event_id),
                    )
                    promoted_row = connection.execute(
                        """
                        SELECT * FROM session_event_records
                        WHERE event_id = ?
                        """,
                        (unit.event.event_id,),
                    ).fetchone()
                    if promoted_row is None:
                        raise RuntimeError("failed to promote event projection_epoch")
                    event = _event_record_from_sqlite_row(promoted_row)
                else:
                    event = existing_event
                idempotent = True
            else:
                next_seq = fact.session_seq_int + 1
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (next_seq, authority.session_id),
                )
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        next_seq,
                        unit.event.event_id,
                        unit.event.event_kind,
                        _json_to_sql(unit.event.payload),
                        _datetime_to_json(unit.event.created_at),
                        fact.projection_epoch_int,
                    ),
                )
                event = EventRecord(
                    event_id=unit.event.event_id,
                    session_id=authority.session_id,
                    event_kind=unit.event.event_kind,
                    payload=unit.event.payload,
                    created_at=unit.event.created_at,
                    session_seq=format_u64(next_seq),
                    projection_epoch=format_u64(fact.projection_epoch_int),
                )
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(unit.session_state, sort_keys=True)),
            )
            if unit.run_state is not None:
                existing = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE run_id = ?",
                    (unit.run_state.run_id,),
                ).fetchone()
                if (
                    existing is not None
                    and existing["session_id"] != authority.session_id
                ):
                    raise SessionOwnershipConflictError(
                        "agent run target belongs to another session"
                    )
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
                    _agent_run_sqlite_values(unit.run_state),
                )
            if unit.mailbox is not None:
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        unit.mailbox.slot_id,
                        unit.mailbox.lane,
                        unit.mailbox.disposition,
                        _json_to_sql(unit.mailbox.payload),
                    ),
                )
            if unit.effect is not None:
                existing_effect = connection.execute(
                    """
                    SELECT status FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, unit.effect.effect_id),
                ).fetchone()
                if existing_effect is None or effect_status_may_replace(
                    current=existing_effect["status"],
                    incoming=unit.effect.status,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_effect_slots (
                            session_id, effect_id, status, payload
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id, effect_id)
                        DO UPDATE SET
                            status = excluded.status,
                            payload = excluded.payload
                        """,
                        (
                            authority.session_id,
                            unit.effect.effect_id,
                            unit.effect.status,
                            _json_to_sql(unit.effect.payload),
                        ),
                    )
            if unit.receipt is not None:
                existing_receipt = connection.execute(
                    """
                    SELECT generation FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt is None or receipt_generation_may_replace(
                    current=existing_receipt["generation"],
                    incoming=unit.receipt.generation,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_receipt_slots (
                            session_id, receipt_id, generation, payload,
                            compensation_effect_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, receipt_id)
                        DO UPDATE SET
                            generation = excluded.generation,
                            payload = excluded.payload,
                            compensation_effect_id = excluded.compensation_effect_id
                        """,
                        (
                            authority.session_id,
                            unit.receipt.receipt_id,
                            unit.receipt.generation,
                            _json_to_sql(unit.receipt.payload),
                            unit.receipt.compensation_effect_id,
                        ),
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

    @classmethod
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

    def _assert_topic_belongs_to_authority(
        self,
        connection: sqlite3.Connection,
        authority: OwnerAuthority,
        topic_id: str,
    ) -> str:
        row = connection.execute(
            """
            SELECT session_id, tape_id
            FROM topics
            WHERE topic_id = ?
            """,
            (topic_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"topic not found: {topic_id}")
        session_id = row["session_id"]
        if session_id != authority.session_id:
            raise SessionOwnershipConflictError("topic target belongs to another owner")
        tape_id = row["tape_id"]
        if not isinstance(tape_id, str):
            raise TypeError("topic tape_id must be text")
        self._assert_tape_belongs_to_session(
            connection,
            tape_id=tape_id,
            session_id=authority.session_id,
        )
        return tape_id

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


@dataclass(frozen=True)
class _SqliteFactSource:
    state: SessionFactSourceState
    session_seq_int: int
    retention_floor_int: int
    projection_epoch_int: int
    projection: str


def _fact_source_from_sqlite_row(row: sqlite3.Row) -> _SqliteFactSource:
    session_seq = _sqlite_required_int(row, "session_seq", context="fact source")
    retention_floor = _sqlite_required_int(
        row, "retention_floor", context="fact source"
    )
    projection_epoch = _sqlite_required_int(
        row, "projection_epoch", context="fact source"
    )
    projection = _sqlite_required_str(row, "projection", context="fact source")
    session_id = _sqlite_required_str(row, "session_id", context="fact source")
    raw_handoff_payload = row["trusted_handoff_payload"]
    handoff_payload = (
        None
        if raw_handoff_payload is None
        else _json_object_from_sql(raw_handoff_payload, context="trusted handoff")
    )
    return _SqliteFactSource(
        state=SessionFactSourceState(
            session_id=session_id,
            session_seq=format_u64(session_seq),
            retention_floor=format_u64(retention_floor),
            projection=projection,
            projection_epoch=format_u64(projection_epoch),
            trusted_handoff=stored_trusted_handoff(
                session_id=session_id,
                session_seq=_sqlite_optional_int(
                    row, "trusted_handoff_seq", context="fact source"
                ),
                epoch=_sqlite_optional_int(
                    row, "trusted_handoff_epoch", context="fact source"
                ),
                projection=_sqlite_optional_str(
                    row, "trusted_handoff_projection", context="fact source"
                ),
                payload=handoff_payload,
            ),
        ),
        session_seq_int=session_seq,
        retention_floor_int=retention_floor,
        projection_epoch_int=projection_epoch,
        projection=projection,
    )


def _event_record_from_sqlite_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=_sqlite_required_str(row, "event_id", context="session event"),
        session_id=_sqlite_required_str(row, "session_id", context="session event"),
        event_kind=_sqlite_required_str(row, "event_kind", context="session event"),
        payload=_json_object_from_sql(row["payload"], context="session event"),
        created_at=_sqlite_required_datetime(
            row, "created_at", context="session event"
        ),
        session_seq=format_u64(
            _sqlite_required_int(row, "session_seq", context="session event")
        ),
        projection_epoch=format_u64(
            _sqlite_required_int(row, "projection_epoch", context="session event")
        ),
    )


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


class FencedSQLiteTopicStore(SQLiteTopicStore):
    def __init__(
        self,
        *,
        durable_store: SQLiteLocalDurableStore,
        path: Path,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        super().__init__(path)
        self._durable_store = durable_store
        self._authority_for_session = authority_for_session

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        return await self._durable_store.create_topic(
            self._authority_for_session(record.session_id),
            record,
        )

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        session_id = self._require_session_id_for_topic(topic_id)
        return await self._durable_store.finalize_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: dict[str, Any],
    ) -> TopicRecord:
        session_id = self._require_session_id_for_topic(topic_id)
        return await self._durable_store.abort_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def delete_topic(self, topic_id: str) -> None:
        session_id = self._require_session_id_for_topic(topic_id)
        await self._durable_store.delete_topic(
            self._authority_for_session(session_id),
            topic_id,
        )

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        session_id = self._require_session_id_for_topic(record.topic_id)
        return await self._durable_store.record_topic_anchor(
            self._authority_for_session(session_id),
            record,
        )

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        session_id = self._require_session_id_for_topic(record.source_topic_id)
        return await self._durable_store.record_recall_link(
            self._authority_for_session(session_id),
            record,
        )

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        session_id = self._require_session_id_for_topic(delta.topic_id)
        return await self._durable_store.update_topic_cost(
            self._authority_for_session(session_id),
            delta,
        )

    def _require_session_id_for_topic(self, topic_id: str) -> str:
        session_id = self._durable_store.session_id_for_topic(topic_id)
        if session_id is None:
            raise KeyError(f"topic not found: {topic_id}")
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
