"""Owner/session writes and fencing assertions."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast
from agentkit.storage.sqlite import (
    SQLiteCheckpointStore,
    SQLiteTapeStore,
)
from coding_agent.stores.runtime_store import (
    SQLiteRuntimeStore,
)
from coding_agent.topics.store import (
    SQLiteTopicStore,
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
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
)


class LocalCoreMixin:
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
        dispatch_generation INTEGER NOT NULL DEFAULT 0,
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
        admitted_session_seq INTEGER,
        admitted_dispatch_generation INTEGER,
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
