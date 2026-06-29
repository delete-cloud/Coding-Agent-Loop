"""PostgreSQL durable Topic records and persistence."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from agentkit.storage.pg import AsyncPGPool, PGPool

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

TopicStatus = str

_TOPIC_STATUSES: Final[frozenset[str]] = frozenset({"open", "finalized", "aborted"})
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_FORBIDDEN_METADATA_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "command_output",
        "content",
        "env",
        "message",
        "prompt",
        "result",
        "secret",
        "stderr",
        "stdout",
        "text",
    }
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "password=",
    "secret=",
    "sk-",
    "token=",
)


@dataclass(frozen=True)
class TopicRecord:
    topic_id: str
    tape_id: str
    session_id: str
    kind: str
    status: TopicStatus
    title: str | None
    summary: str | None
    owner: str | None
    topic_initial_seq: int
    topic_finalized_seq: int | None
    created_at: datetime
    finalized_at: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("topic_id", self.topic_id)
        _require_non_empty("tape_id", self.tape_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("kind", self.kind)
        _require_topic_status(self.status)
        _require_non_negative_int("topic_initial_seq", self.topic_initial_seq)
        if self.topic_finalized_seq is not None:
            _require_non_negative_int("topic_finalized_seq", self.topic_finalized_seq)
            if self.topic_finalized_seq < self.topic_initial_seq:
                raise ValueError(
                    "topic_finalized_seq must be greater than or equal to "
                    "topic_initial_seq"
                )
        _require_datetime("created_at", self.created_at)
        if self.finalized_at is not None:
            _require_datetime("finalized_at", self.finalized_at)
        _require_optional_display_text("title", self.title)
        _require_optional_display_text("summary", self.summary)
        _require_optional_display_text("owner", self.owner)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class TopicAnchorRecord:
    topic_id: str
    tape_id: str
    seq: int
    anchor_type: str
    entry_id: str | None
    metadata: JSONObject = field(default_factory=dict)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("topic_id", self.topic_id)
        _require_non_empty("tape_id", self.tape_id)
        _require_non_negative_int("seq", self.seq)
        _require_non_empty("anchor_type", self.anchor_type)
        if self.entry_id is not None:
            _require_non_empty("entry_id", self.entry_id)
        _require_json_object("metadata", self.metadata)
        if self.created_at is not None:
            _require_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class TopicRecallLinkRecord:
    source_topic_id: str
    recalled_topic_id: str
    relation: str
    anchor_seq: int | None = None
    source_entry_start_seq: int | None = None
    source_entry_end_seq: int | None = None
    metadata: JSONObject = field(default_factory=dict)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("source_topic_id", self.source_topic_id)
        _require_non_empty("recalled_topic_id", self.recalled_topic_id)
        _require_non_empty("relation", self.relation)
        for key, value in (
            ("anchor_seq", self.anchor_seq),
            ("source_entry_start_seq", self.source_entry_start_seq),
            ("source_entry_end_seq", self.source_entry_end_seq),
        ):
            if value is not None:
                _require_non_negative_int(key, value)
        if (
            self.source_entry_start_seq is not None
            and self.source_entry_end_seq is not None
            and self.source_entry_end_seq < self.source_entry_start_seq
        ):
            raise ValueError(
                "source_entry_end_seq must be greater than or equal to "
                "source_entry_start_seq"
            )
        _require_json_object("metadata", self.metadata)
        if self.created_at is not None:
            _require_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class TopicCostRecord:
    topic_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    run_count: int = 0
    action_count: int = 0
    validation_count: int = 0
    tool_call_count: int = 0
    metadata: JSONObject = field(default_factory=dict)
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("topic_id", self.topic_id)
        for key, value in (
            ("prompt_tokens", self.prompt_tokens),
            ("completion_tokens", self.completion_tokens),
            ("total_tokens", self.total_tokens),
            ("run_count", self.run_count),
            ("action_count", self.action_count),
            ("validation_count", self.validation_count),
            ("tool_call_count", self.tool_call_count),
        ):
            _require_non_negative_int(key, value)
        _require_json_object("metadata", self.metadata)
        if self.updated_at is not None:
            _require_datetime("updated_at", self.updated_at)


class PGTopicStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS topics (
        topic_id TEXT PRIMARY KEY,
        tape_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT,
        summary TEXT,
        owner TEXT,
        topic_initial_seq INTEGER NOT NULL,
        topic_finalized_seq INTEGER,
        created_at TIMESTAMPTZ NOT NULL,
        finalized_at TIMESTAMPTZ,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS topics_session_status_created_idx
        ON topics (session_id, status, created_at, topic_id);

    CREATE INDEX IF NOT EXISTS topics_tape_status_initial_idx
        ON topics (tape_id, status, topic_initial_seq, topic_id);

    CREATE INDEX IF NOT EXISTS topics_status_created_idx
        ON topics (status, created_at, topic_id);

    CREATE UNIQUE INDEX IF NOT EXISTS topics_one_open_per_session_tape_idx
        ON topics (session_id, tape_id)
        WHERE status = 'open';

    CREATE TABLE IF NOT EXISTS topic_anchors (
        topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        tape_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        anchor_type TEXT NOT NULL,
        entry_id TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (topic_id, seq, anchor_type)
    );

    CREATE INDEX IF NOT EXISTS topic_anchors_tape_seq_idx
        ON topic_anchors (tape_id, seq);

    CREATE TABLE IF NOT EXISTS topic_recall_links (
        source_topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        recalled_topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        relation TEXT NOT NULL,
        anchor_seq INTEGER,
        source_entry_start_seq INTEGER,
        source_entry_end_seq INTEGER,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (source_topic_id, recalled_topic_id, relation)
    );

    CREATE INDEX IF NOT EXISTS topic_recall_links_recalled_idx
        ON topic_recall_links (recalled_topic_id, source_topic_id);

    CREATE TABLE IF NOT EXISTS topic_costs (
        topic_id TEXT PRIMARY KEY REFERENCES topics(topic_id) ON DELETE CASCADE,
        prompt_tokens BIGINT NOT NULL DEFAULT 0,
        completion_tokens BIGINT NOT NULL DEFAULT 0,
        total_tokens BIGINT NOT NULL DEFAULT 0,
        run_count BIGINT NOT NULL DEFAULT 0,
        action_count BIGINT NOT NULL DEFAULT 0,
        validation_count BIGINT NOT NULL DEFAULT 0,
        tool_call_count BIGINT NOT NULL DEFAULT 0,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    _INSERT_TOPIC_SQL: Final[str] = """
    WITH inserted AS (
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
            metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
        ON CONFLICT (topic_id) DO NOTHING
        RETURNING *
    )
    SELECT * FROM inserted
    UNION ALL
    SELECT * FROM topics
    WHERE topic_id = $1 AND NOT EXISTS (SELECT 1 FROM inserted)
    """
    _FINALIZE_TOPIC_SQL: Final[str] = """
    UPDATE topics
    SET status = 'finalized',
        summary = $2,
        topic_finalized_seq = $3,
        finalized_at = $4,
        metadata = $5::jsonb,
        updated_at = NOW()
    WHERE topic_id = $1 AND status = 'open'
      AND $3 >= topic_initial_seq
    RETURNING *
    """
    _ABORT_TOPIC_SQL: Final[str] = """
    UPDATE topics
    SET status = 'aborted',
        summary = $2,
        topic_finalized_seq = $3,
        finalized_at = $4,
        metadata = $5::jsonb,
        updated_at = NOW()
    WHERE topic_id = $1 AND status = 'open'
      AND ($3 IS NULL OR $3 >= topic_initial_seq)
    RETURNING *
    """
    _SELECT_TOPIC_SQL: Final[str] = "SELECT * FROM topics WHERE topic_id = $1"
    _SELECT_TOPIC_TAPE_SQL: Final[str] = (
        "SELECT tape_id FROM topics WHERE topic_id = $1"
    )
    _DELETE_TOPIC_RECALL_LINKS_SQL: Final[str] = """
    DELETE FROM topic_recall_links
    WHERE source_topic_id = $1 OR recalled_topic_id = $1
    """
    _DELETE_TOPIC_COST_SQL: Final[str] = """
    DELETE FROM topic_costs
    WHERE topic_id = $1
    """
    _DELETE_TOPIC_ANCHORS_SQL: Final[str] = """
    DELETE FROM topic_anchors
    WHERE topic_id = $1
    """
    _DELETE_TOPIC_SQL: Final[str] = """
    DELETE FROM topics
    WHERE topic_id = $1
    """
    _LIST_TOPICS_SQL: Final[str] = """
    SELECT * FROM topics
    WHERE ($1::text IS NULL OR session_id = $1)
      AND ($2::text IS NULL OR tape_id = $2)
      AND ($3::text IS NULL OR status = $3)
      AND (
        $4::timestamptz IS NULL
        OR created_at > $4
        OR (created_at = $4 AND topic_id > $5)
      )
    ORDER BY created_at, topic_id
    LIMIT $6
    OFFSET $7
    """
    _FIND_OPEN_TOPIC_SQL: Final[str] = """
    SELECT * FROM topics
    WHERE session_id = $1 AND tape_id = $2 AND status = 'open'
    ORDER BY created_at DESC, topic_id DESC
    LIMIT 1
    """
    _INSERT_ANCHOR_SQL: Final[str] = """
    INSERT INTO topic_anchors (
        topic_id,
        tape_id,
        seq,
        anchor_type,
        entry_id,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
    ON CONFLICT (topic_id, seq, anchor_type)
    DO UPDATE SET
        tape_id = EXCLUDED.tape_id,
        entry_id = EXCLUDED.entry_id,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _LIST_ANCHORS_SQL: Final[str] = """
    SELECT * FROM topic_anchors
    WHERE topic_id = $1
    ORDER BY seq, anchor_type
    """
    _INSERT_RECALL_LINK_SQL: Final[str] = """
    INSERT INTO topic_recall_links (
        source_topic_id,
        recalled_topic_id,
        relation,
        anchor_seq,
        source_entry_start_seq,
        source_entry_end_seq,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
    ON CONFLICT (source_topic_id, recalled_topic_id, relation)
    DO UPDATE SET
        anchor_seq = EXCLUDED.anchor_seq,
        source_entry_start_seq = EXCLUDED.source_entry_start_seq,
        source_entry_end_seq = EXCLUDED.source_entry_end_seq,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _LIST_RECALL_LINKS_SQL: Final[str] = """
    SELECT * FROM topic_recall_links
    WHERE source_topic_id = $1
    ORDER BY created_at, recalled_topic_id, relation
    """
    _UPSERT_COST_SQL: Final[str] = """
    INSERT INTO topic_costs (
        topic_id,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        run_count,
        action_count,
        validation_count,
        tool_call_count,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
    ON CONFLICT (topic_id)
    DO UPDATE SET
        prompt_tokens = topic_costs.prompt_tokens + EXCLUDED.prompt_tokens,
        completion_tokens = topic_costs.completion_tokens
            + EXCLUDED.completion_tokens,
        total_tokens = topic_costs.total_tokens + EXCLUDED.total_tokens,
        run_count = topic_costs.run_count + EXCLUDED.run_count,
        action_count = topic_costs.action_count + EXCLUDED.action_count,
        validation_count = topic_costs.validation_count
            + EXCLUDED.validation_count,
        tool_call_count = topic_costs.tool_call_count
            + EXCLUDED.tool_call_count,
        metadata = EXCLUDED.metadata,
        updated_at = NOW()
    RETURNING *
    """
    _SELECT_COST_SQL: Final[str] = "SELECT * FROM topic_costs WHERE topic_id = $1"

    def __init__(self, *, pool: PGPool) -> None:
        self._pool: PGPool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_SCHEMA_SQL)
            self._schema_ready = True
        return pool

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_TOPIC_SQL,
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
            record.created_at,
            record.finalized_at,
            record.metadata,
        )
        return _topic_from_row(_required_row(row, "topic insert"))

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            self._FINALIZE_TOPIC_SQL,
            "finalize",
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
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            self._ABORT_TOPIC_SQL,
            "abort",
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def _close_topic(
        self,
        query: str,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        _require_non_empty("topic_id", topic_id)
        _require_optional_display_text("summary", summary)
        if topic_finalized_seq is not None:
            _require_non_negative_int("topic_finalized_seq", topic_finalized_seq)
        _require_datetime("finalized_at", finalized_at)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            query,
            topic_id,
            summary,
            topic_finalized_seq,
            finalized_at,
            metadata,
        )
        if row is None:
            raise KeyError(f"open topic not found for {operation}: {topic_id}")
        return _topic_from_row(row)

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        _require_non_empty("topic_id", topic_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_TOPIC_SQL, topic_id)
        if row is None:
            return None
        return _topic_from_row(row)

    async def delete_topic(self, topic_id: str) -> None:
        _require_non_empty("topic_id", topic_id)
        pool = await self._ensure_schema()
        async with pool.acquire() as connection:
            try:
                _ = await connection.execute("BEGIN")
                await connection.execute(self._DELETE_TOPIC_RECALL_LINKS_SQL, topic_id)
                await connection.execute(self._DELETE_TOPIC_COST_SQL, topic_id)
                await connection.execute(self._DELETE_TOPIC_ANCHORS_SQL, topic_id)
                await connection.execute(self._DELETE_TOPIC_SQL, topic_id)
                _ = await connection.execute("COMMIT")
            except BaseException:
                try:
                    _ = await connection.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: TopicStatus | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TopicRecord]:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if tape_id is not None:
            _require_non_empty("tape_id", tape_id)
        if status is not None:
            _require_topic_status(status)
        if (after_created_at is None) != (after_topic_id is None):
            raise ValueError(
                "after_created_at and after_topic_id must be provided together"
            )
        if after_created_at is not None:
            _require_datetime("after_created_at", after_created_at)
        if after_topic_id is not None:
            _require_non_empty("after_topic_id", after_topic_id)
        _require_positive_int("limit", limit)
        _require_non_negative_int("offset", offset)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_TOPICS_SQL,
            session_id,
            tape_id,
            status,
            after_created_at,
            after_topic_id,
            limit,
            offset,
        )
        return [_topic_from_row(row) for row in rows]

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("tape_id", tape_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._FIND_OPEN_TOPIC_SQL, session_id, tape_id)
        if row is None:
            return None
        return _topic_from_row(row)

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        pool = await self._ensure_schema()
        topic_row = await pool.fetchrow(self._SELECT_TOPIC_TAPE_SQL, record.topic_id)
        if topic_row is None:
            raise KeyError(f"topic not found for anchor: {record.topic_id}")
        if topic_row["tape_id"] != record.tape_id:
            raise ValueError("topic anchor tape_id must match parent topic")
        row = await pool.fetchrow(
            self._INSERT_ANCHOR_SQL,
            record.topic_id,
            record.tape_id,
            record.seq,
            record.anchor_type,
            record.entry_id,
            record.metadata,
        )
        return _topic_anchor_from_row(_required_row(row, "topic anchor upsert"))

    async def list_topic_anchors(self, topic_id: str) -> list[TopicAnchorRecord]:
        _require_non_empty("topic_id", topic_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_ANCHORS_SQL, topic_id)
        return [_topic_anchor_from_row(row) for row in rows]

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_RECALL_LINK_SQL,
            record.source_topic_id,
            record.recalled_topic_id,
            record.relation,
            record.anchor_seq,
            record.source_entry_start_seq,
            record.source_entry_end_seq,
            record.metadata,
        )
        return _topic_recall_link_from_row(
            _required_row(row, "topic recall link upsert")
        )

    async def list_recall_links(
        self,
        source_topic_id: str,
    ) -> list[TopicRecallLinkRecord]:
        _require_non_empty("source_topic_id", source_topic_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_RECALL_LINKS_SQL, source_topic_id)
        return [_topic_recall_link_from_row(row) for row in rows]

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_COST_SQL,
            delta.topic_id,
            delta.prompt_tokens,
            delta.completion_tokens,
            delta.total_tokens,
            delta.run_count,
            delta.action_count,
            delta.validation_count,
            delta.tool_call_count,
            delta.metadata,
        )
        return _topic_cost_from_row(_required_row(row, "topic cost upsert"))

    async def load_topic_cost(self, topic_id: str) -> TopicCostRecord | None:
        _require_non_empty("topic_id", topic_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_COST_SQL, topic_id)
        if row is None:
            return None
        return _topic_cost_from_row(row)


class SQLiteTopicStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS topics (
        topic_id TEXT PRIMARY KEY,
        tape_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT,
        summary TEXT,
        owner TEXT,
        topic_initial_seq INTEGER NOT NULL,
        topic_finalized_seq INTEGER,
        created_at TEXT NOT NULL,
        finalized_at TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS topics_session_status_created_idx
        ON topics (session_id, status, created_at, topic_id);

    CREATE INDEX IF NOT EXISTS topics_tape_status_initial_idx
        ON topics (tape_id, status, topic_initial_seq, topic_id);

    CREATE INDEX IF NOT EXISTS topics_status_created_idx
        ON topics (status, created_at, topic_id);

    CREATE UNIQUE INDEX IF NOT EXISTS topics_one_open_per_session_tape_idx
        ON topics (session_id, tape_id)
        WHERE status = 'open';

    CREATE TABLE IF NOT EXISTS topic_anchors (
        topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        tape_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        anchor_type TEXT NOT NULL,
        entry_id TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        PRIMARY KEY (topic_id, seq, anchor_type)
    );

    CREATE INDEX IF NOT EXISTS topic_anchors_tape_seq_idx
        ON topic_anchors (tape_id, seq);

    CREATE TABLE IF NOT EXISTS topic_recall_links (
        source_topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        recalled_topic_id TEXT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
        relation TEXT NOT NULL,
        anchor_seq INTEGER,
        source_entry_start_seq INTEGER,
        source_entry_end_seq INTEGER,
        metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_topic_id, recalled_topic_id, relation)
    );

    CREATE INDEX IF NOT EXISTS topic_recall_links_recalled_idx
        ON topic_recall_links (recalled_topic_id, source_topic_id);

    CREATE TABLE IF NOT EXISTS topic_costs (
        topic_id TEXT PRIMARY KEY REFERENCES topics(topic_id) ON DELETE CASCADE,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        run_count INTEGER NOT NULL DEFAULT 0,
        action_count INTEGER NOT NULL DEFAULT 0,
        validation_count INTEGER NOT NULL DEFAULT 0,
        tool_call_count INTEGER NOT NULL DEFAULT 0,
        metadata TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(self._CREATE_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        now = _datetime_to_sqlite_text(datetime.now(UTC))
        with self._lock, self._connect() as connection:
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
                    _datetime_to_sqlite_text(record.created_at),
                    _optional_datetime_to_sqlite_text(record.finalized_at),
                    _json_to_sqlite_text(record.metadata),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM topics WHERE topic_id = ?",
                (record.topic_id,),
            ).fetchone()
        return _topic_from_sqlite_row(_required_sqlite_row(row, "topic insert"))

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
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
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
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
        status: TopicStatus,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        _require_non_empty("topic_id", topic_id)
        _require_optional_display_text("summary", summary)
        if topic_finalized_seq is not None:
            _require_non_negative_int("topic_finalized_seq", topic_finalized_seq)
        _require_datetime("finalized_at", finalized_at)
        _require_json_object("metadata", metadata)
        if status == "finalized" and topic_finalized_seq is None:
            raise ValueError("topic_finalized_seq must be provided for finalize")
        with self._lock, self._connect() as connection:
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
                        _datetime_to_sqlite_text(finalized_at),
                        _json_to_sqlite_text(metadata),
                        _datetime_to_sqlite_text(datetime.now(UTC)),
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
                        _datetime_to_sqlite_text(finalized_at),
                        _json_to_sqlite_text(metadata),
                        _datetime_to_sqlite_text(datetime.now(UTC)),
                        topic_id,
                        topic_finalized_seq,
                        topic_finalized_seq,
                    ),
                ).fetchone()
        if row is None:
            raise KeyError(f"open topic not found for {operation}: {topic_id}")
        return _topic_from_sqlite_row(row)

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        _require_non_empty("topic_id", topic_id)

        def read() -> TopicRecord | None:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM topics WHERE topic_id = ?",
                    (topic_id,),
                ).fetchone()
            if row is None:
                return None
            return _topic_from_sqlite_row(row)

        return await asyncio.to_thread(read)

    async def delete_topic(self, topic_id: str) -> None:
        _require_non_empty("topic_id", topic_id)

        def delete() -> None:
            with self._lock, self._connect() as connection:
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

        await asyncio.to_thread(delete)

    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: TopicStatus | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TopicRecord]:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if tape_id is not None:
            _require_non_empty("tape_id", tape_id)
        if status is not None:
            _require_topic_status(status)
        if (after_created_at is None) != (after_topic_id is None):
            raise ValueError(
                "after_created_at and after_topic_id must be provided together"
            )
        after_created_at_text: str | None = None
        if after_created_at is not None:
            _require_datetime("after_created_at", after_created_at)
            after_created_at_text = _datetime_to_sqlite_text(after_created_at)
        if after_topic_id is not None:
            _require_non_empty("after_topic_id", after_topic_id)
        _require_positive_int("limit", limit)
        _require_non_negative_int("offset", offset)

        def read() -> list[TopicRecord]:
            with self._lock, self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM topics
                    WHERE (? IS NULL OR session_id = ?)
                      AND (? IS NULL OR tape_id = ?)
                      AND (? IS NULL OR status = ?)
                      AND (
                        ? IS NULL
                        OR created_at > ?
                        OR (created_at = ? AND topic_id > ?)
                      )
                    ORDER BY created_at, topic_id
                    LIMIT ?
                    OFFSET ?
                    """,
                    (
                        session_id,
                        session_id,
                        tape_id,
                        tape_id,
                        status,
                        status,
                        after_created_at_text,
                        after_created_at_text,
                        after_created_at_text,
                        after_topic_id,
                        limit,
                        offset,
                    ),
                ).fetchall()
            return [_topic_from_sqlite_row(row) for row in rows]

        return await asyncio.to_thread(read)

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("tape_id", tape_id)

        def read() -> TopicRecord | None:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM topics
                    WHERE session_id = ? AND tape_id = ? AND status = 'open'
                    ORDER BY created_at DESC, topic_id DESC
                    LIMIT 1
                    """,
                    (session_id, tape_id),
                ).fetchone()
            if row is None:
                return None
            return _topic_from_sqlite_row(row)

        return await asyncio.to_thread(read)

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        with self._lock, self._connect() as connection:
            topic_row = connection.execute(
                "SELECT tape_id FROM topics WHERE topic_id = ?",
                (record.topic_id,),
            ).fetchone()
            if topic_row is None:
                raise KeyError(f"topic not found for anchor: {record.topic_id}")
            if topic_row["tape_id"] != record.tape_id:
                raise ValueError("topic anchor tape_id must match parent topic")
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
                    _json_to_sqlite_text(record.metadata),
                    _datetime_to_sqlite_text(record.created_at or datetime.now(UTC)),
                ),
            ).fetchone()
        return _topic_anchor_from_sqlite_row(
            _required_sqlite_row(row, "topic anchor upsert")
        )

    async def list_topic_anchors(self, topic_id: str) -> list[TopicAnchorRecord]:
        _require_non_empty("topic_id", topic_id)

        def read() -> list[TopicAnchorRecord]:
            with self._lock, self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM topic_anchors
                    WHERE topic_id = ?
                    ORDER BY seq, anchor_type
                    """,
                    (topic_id,),
                ).fetchall()
            return [_topic_anchor_from_sqlite_row(row) for row in rows]

        return await asyncio.to_thread(read)

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        with self._lock, self._connect() as connection:
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
                    _json_to_sqlite_text(record.metadata),
                    _datetime_to_sqlite_text(record.created_at or datetime.now(UTC)),
                ),
            ).fetchone()
        return _topic_recall_link_from_sqlite_row(
            _required_sqlite_row(row, "topic recall link upsert")
        )

    async def list_recall_links(
        self,
        source_topic_id: str,
    ) -> list[TopicRecallLinkRecord]:
        _require_non_empty("source_topic_id", source_topic_id)

        def read() -> list[TopicRecallLinkRecord]:
            with self._lock, self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM topic_recall_links
                    WHERE source_topic_id = ?
                    ORDER BY created_at, recalled_topic_id, relation
                    """,
                    (source_topic_id,),
                ).fetchall()
            return [_topic_recall_link_from_sqlite_row(row) for row in rows]

        return await asyncio.to_thread(read)

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        with self._lock, self._connect() as connection:
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
                    _json_to_sqlite_text(delta.metadata),
                    _datetime_to_sqlite_text(delta.updated_at or datetime.now(UTC)),
                ),
            ).fetchone()
        return _topic_cost_from_sqlite_row(
            _required_sqlite_row(row, "topic cost upsert")
        )

    async def load_topic_cost(self, topic_id: str) -> TopicCostRecord | None:
        _require_non_empty("topic_id", topic_id)

        def read() -> TopicCostRecord | None:
            with self._lock, self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM topic_costs WHERE topic_id = ?",
                    (topic_id,),
                ).fetchone()
            if row is None:
                return None
            return _topic_cost_from_sqlite_row(row)

        return await asyncio.to_thread(read)


def _required_row(
    row: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _required_sqlite_row(
    row: sqlite3.Row | None,
    context: str,
) -> sqlite3.Row:
    if row is None:
        raise RuntimeError(f"sqlite {context} returned no row")
    return row


def _topic_from_row(row: dict[str, object]) -> TopicRecord:
    return TopicRecord(
        topic_id=_required_str(row, "topic_id", context="topic row"),
        tape_id=_required_str(row, "tape_id", context="topic row"),
        session_id=_required_str(row, "session_id", context="topic row"),
        kind=_required_str(row, "kind", context="topic row"),
        status=_required_str(row, "status", context="topic row"),
        title=_optional_str(row, "title", context="topic row"),
        summary=_optional_str(row, "summary", context="topic row"),
        owner=_optional_str(row, "owner", context="topic row"),
        topic_initial_seq=_required_int(row, "topic_initial_seq", context="topic row"),
        topic_finalized_seq=_optional_int(
            row, "topic_finalized_seq", context="topic row"
        ),
        created_at=_required_datetime(row, "created_at", context="topic row"),
        finalized_at=_optional_datetime(row, "finalized_at", context="topic row"),
        metadata=_required_json_object(row, "metadata", context="topic row"),
    )


def _topic_anchor_from_row(row: dict[str, object]) -> TopicAnchorRecord:
    return TopicAnchorRecord(
        topic_id=_required_str(row, "topic_id", context="topic anchor row"),
        tape_id=_required_str(row, "tape_id", context="topic anchor row"),
        seq=_required_int(row, "seq", context="topic anchor row"),
        anchor_type=_required_str(row, "anchor_type", context="topic anchor row"),
        entry_id=_optional_str(row, "entry_id", context="topic anchor row"),
        metadata=_required_json_object(
            row,
            "metadata",
            context="topic anchor row",
        ),
        created_at=_optional_datetime(
            row,
            "created_at",
            context="topic anchor row",
        ),
    )


def _topic_recall_link_from_row(row: dict[str, object]) -> TopicRecallLinkRecord:
    return TopicRecallLinkRecord(
        source_topic_id=_required_str(
            row,
            "source_topic_id",
            context="topic recall link row",
        ),
        recalled_topic_id=_required_str(
            row,
            "recalled_topic_id",
            context="topic recall link row",
        ),
        relation=_required_str(row, "relation", context="topic recall link row"),
        anchor_seq=_optional_int(row, "anchor_seq", context="topic recall link row"),
        source_entry_start_seq=_optional_int(
            row,
            "source_entry_start_seq",
            context="topic recall link row",
        ),
        source_entry_end_seq=_optional_int(
            row,
            "source_entry_end_seq",
            context="topic recall link row",
        ),
        metadata=_required_json_object(
            row,
            "metadata",
            context="topic recall link row",
        ),
        created_at=_optional_datetime(
            row,
            "created_at",
            context="topic recall link row",
        ),
    )


def _topic_cost_from_row(row: dict[str, object]) -> TopicCostRecord:
    return TopicCostRecord(
        topic_id=_required_str(row, "topic_id", context="topic cost row"),
        prompt_tokens=_required_int(row, "prompt_tokens", context="topic cost row"),
        completion_tokens=_required_int(
            row,
            "completion_tokens",
            context="topic cost row",
        ),
        total_tokens=_required_int(row, "total_tokens", context="topic cost row"),
        run_count=_required_int(row, "run_count", context="topic cost row"),
        action_count=_required_int(row, "action_count", context="topic cost row"),
        validation_count=_required_int(
            row,
            "validation_count",
            context="topic cost row",
        ),
        tool_call_count=_required_int(
            row,
            "tool_call_count",
            context="topic cost row",
        ),
        metadata=_required_json_object(row, "metadata", context="topic cost row"),
        updated_at=_optional_datetime(row, "updated_at", context="topic cost row"),
    )


def _topic_from_sqlite_row(row: sqlite3.Row) -> TopicRecord:
    return TopicRecord(
        topic_id=_sqlite_required_str(row, "topic_id", context="topic row"),
        tape_id=_sqlite_required_str(row, "tape_id", context="topic row"),
        session_id=_sqlite_required_str(row, "session_id", context="topic row"),
        kind=_sqlite_required_str(row, "kind", context="topic row"),
        status=_sqlite_required_str(row, "status", context="topic row"),
        title=_sqlite_optional_str(row, "title", context="topic row"),
        summary=_sqlite_optional_str(row, "summary", context="topic row"),
        owner=_sqlite_optional_str(row, "owner", context="topic row"),
        topic_initial_seq=_sqlite_required_int(
            row,
            "topic_initial_seq",
            context="topic row",
        ),
        topic_finalized_seq=_sqlite_optional_int(
            row,
            "topic_finalized_seq",
            context="topic row",
        ),
        created_at=_sqlite_required_datetime(row, "created_at", context="topic row"),
        finalized_at=_sqlite_optional_datetime(
            row,
            "finalized_at",
            context="topic row",
        ),
        metadata=_sqlite_required_json_object(row, "metadata", context="topic row"),
    )


def _topic_anchor_from_sqlite_row(row: sqlite3.Row) -> TopicAnchorRecord:
    return TopicAnchorRecord(
        topic_id=_sqlite_required_str(row, "topic_id", context="topic anchor row"),
        tape_id=_sqlite_required_str(row, "tape_id", context="topic anchor row"),
        seq=_sqlite_required_int(row, "seq", context="topic anchor row"),
        anchor_type=_sqlite_required_str(
            row,
            "anchor_type",
            context="topic anchor row",
        ),
        entry_id=_sqlite_optional_str(row, "entry_id", context="topic anchor row"),
        metadata=_sqlite_required_json_object(
            row,
            "metadata",
            context="topic anchor row",
        ),
        created_at=_sqlite_optional_datetime(
            row,
            "created_at",
            context="topic anchor row",
        ),
    )


def _topic_recall_link_from_sqlite_row(row: sqlite3.Row) -> TopicRecallLinkRecord:
    return TopicRecallLinkRecord(
        source_topic_id=_sqlite_required_str(
            row,
            "source_topic_id",
            context="topic recall link row",
        ),
        recalled_topic_id=_sqlite_required_str(
            row,
            "recalled_topic_id",
            context="topic recall link row",
        ),
        relation=_sqlite_required_str(
            row,
            "relation",
            context="topic recall link row",
        ),
        anchor_seq=_sqlite_optional_int(
            row,
            "anchor_seq",
            context="topic recall link row",
        ),
        source_entry_start_seq=_sqlite_optional_int(
            row,
            "source_entry_start_seq",
            context="topic recall link row",
        ),
        source_entry_end_seq=_sqlite_optional_int(
            row,
            "source_entry_end_seq",
            context="topic recall link row",
        ),
        metadata=_sqlite_required_json_object(
            row,
            "metadata",
            context="topic recall link row",
        ),
        created_at=_sqlite_optional_datetime(
            row,
            "created_at",
            context="topic recall link row",
        ),
    )


def _topic_cost_from_sqlite_row(row: sqlite3.Row) -> TopicCostRecord:
    return TopicCostRecord(
        topic_id=_sqlite_required_str(row, "topic_id", context="topic cost row"),
        prompt_tokens=_sqlite_required_int(
            row,
            "prompt_tokens",
            context="topic cost row",
        ),
        completion_tokens=_sqlite_required_int(
            row,
            "completion_tokens",
            context="topic cost row",
        ),
        total_tokens=_sqlite_required_int(
            row,
            "total_tokens",
            context="topic cost row",
        ),
        run_count=_sqlite_required_int(row, "run_count", context="topic cost row"),
        action_count=_sqlite_required_int(
            row,
            "action_count",
            context="topic cost row",
        ),
        validation_count=_sqlite_required_int(
            row,
            "validation_count",
            context="topic cost row",
        ),
        tool_call_count=_sqlite_required_int(
            row,
            "tool_call_count",
            context="topic cost row",
        ),
        metadata=_sqlite_required_json_object(
            row,
            "metadata",
            context="topic cost row",
        ),
        updated_at=_sqlite_optional_datetime(
            row,
            "updated_at",
            context="topic cost row",
        ),
    )


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_topic_status(status: str) -> None:
    if status not in _TOPIC_STATUSES:
        raise ValueError(f"topic status must be one of {sorted(_TOPIC_STATUSES)}")


def _require_optional_display_text(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_DISPLAY_TEXT_CHARS} characters"
        )
    _reject_secret_shaped_value(field_name, value)


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int")


def _require_json_object(field_name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        key_folded = key.casefold()
        if any(part in key_folded for part in _FORBIDDEN_METADATA_KEY_PARTS):
            raise ValueError(f"{field_name} contains forbidden metadata key: {key}")
        _require_json_value(f"{field_name}.{key}", item)


def _require_json_value(field_name: str, value: JSONValue) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            if len(value) > _MAX_METADATA_STRING_CHARS:
                raise ValueError(
                    f"{field_name} must be at most "
                    f"{_MAX_METADATA_STRING_CHARS} characters"
                )
            _reject_secret_shaped_value(field_name, value)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_value(f"{field_name}[{index}]", item)
        return
    if isinstance(value, dict):
        _require_json_object(field_name, value)
        return
    raise TypeError(f"{field_name} must be JSON-safe")


def _reject_secret_shaped_value(field_name: str, value: str) -> None:
    folded = value.casefold()
    if any(marker in folded for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(f"{field_name} must not contain secret-shaped values")


def _required_str(row: dict[str, object], key: str, *, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string {key}")
    return value


def _optional_str(row: dict[str, object], key: str, *, context: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string or None {key}")
    return value


def _required_int(row: dict[str, object], key: str, *, context: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres {context} must include int {key}")
    return value


def _optional_int(row: dict[str, object], key: str, *, context: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres {context} must include int or None {key}")
    return value


def _required_datetime(row: dict[str, object], key: str, *, context: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime {key}")
    return value


def _optional_datetime(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include dict {key}")
    _require_json_object(key, value)
    return value


def _sqlite_required_str(row: sqlite3.Row, key: str, *, context: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include string {key}")
    return value


def _sqlite_optional_str(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include string or None {key}")
    return value


def _sqlite_required_int(row: sqlite3.Row, key: str, *, context: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sqlite {context} must include int {key}")
    return value


def _sqlite_optional_int(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> int | None:
    value = row[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sqlite {context} must include int or None {key}")
    return value


def _sqlite_required_datetime(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> datetime:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include datetime text {key}")
    return _datetime_from_sqlite_text(value)


def _sqlite_optional_datetime(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include datetime text or None {key}")
    return _datetime_from_sqlite_text(value)


def _sqlite_required_json_object(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include JSON text {key}")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError(f"sqlite {context} must include JSON object {key}")
    _require_json_object(key, parsed)
    return parsed


def _json_to_sqlite_text(value: JSONObject) -> str:
    _require_json_object("metadata", value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _datetime_to_sqlite_text(value: datetime) -> str:
    _require_datetime("datetime", value)
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    normalized = value.astimezone(UTC)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _optional_datetime_to_sqlite_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _datetime_to_sqlite_text(value)


def _datetime_from_sqlite_text(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"invalid sqlite datetime text: {value}") from exc
