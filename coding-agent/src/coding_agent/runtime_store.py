"""PostgreSQL durable runtime store records and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, cast

from agentkit.storage.pg import AsyncPGPool, PGPool

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    session_id: str
    tape_id: str | None
    parent_run_id: str | None
    agent_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)
    result: JSONObject = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("status", self.status)
        if self.tape_id is not None:
            _require_non_empty("tape_id", self.tape_id)
        if self.parent_run_id is not None:
            _require_non_empty("parent_run_id", self.parent_run_id)
        if self.agent_id is not None:
            _require_non_empty("agent_id", self.agent_id)
        _require_datetime("started_at", self.started_at)
        if self.ended_at is not None:
            _require_datetime("ended_at", self.ended_at)
        _require_json_object("metadata", self.metadata)
        _require_json_object("result", self.result)
        if self.error is not None:
            _require_non_empty("error", self.error)


@dataclass(frozen=True)
class RuntimeEventRecord:
    event_id: str
    run_id: str
    event_kind: str
    payload: JSONObject
    created_at: datetime
    sequence: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty("event_id", self.event_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("event_kind", self.event_kind)
        _require_json_object("payload", self.payload)
        _require_datetime("created_at", self.created_at)
        if self.sequence is not None:
            _require_positive_int("sequence", self.sequence)


@dataclass(frozen=True)
class RunMessageSnapshotRecord:
    snapshot_id: str
    run_id: str
    messages: list[JSONObject]
    metadata: JSONObject
    created_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("snapshot_id", self.snapshot_id)
        _require_non_empty("run_id", self.run_id)
        if not isinstance(self.messages, list):
            raise TypeError("messages must be a list")
        for message in self.messages:
            _require_json_object("message", message)
        _require_json_object("metadata", self.metadata)
        _require_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class AgentInteractionRecord:
    interaction_id: str
    run_id: str
    interaction_kind: str
    status: str
    request_payload: JSONObject
    response_payload: JSONObject
    metadata: JSONObject
    created_at: datetime
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("interaction_id", self.interaction_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("interaction_kind", self.interaction_kind)
        _require_non_empty("status", self.status)
        _require_json_object("request_payload", self.request_payload)
        _require_json_object("response_payload", self.response_payload)
        _require_json_object("metadata", self.metadata)
        _require_datetime("created_at", self.created_at)
        if self.resolved_at is not None:
            _require_datetime("resolved_at", self.resolved_at)


class PGRuntimeStore:
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS agent_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        tape_id TEXT,
        parent_run_id TEXT,
        agent_id TEXT,
        status TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS agent_runs_session_id_idx
        ON agent_runs (session_id, started_at, run_id);

    CREATE INDEX IF NOT EXISTS agent_runs_tape_id_idx
        ON agent_runs (tape_id, started_at, run_id)
        WHERE tape_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS runtime_events (
        sequence BIGSERIAL PRIMARY KEY,
        event_id TEXT UNIQUE NOT NULL,
        run_id TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    );

    CREATE INDEX IF NOT EXISTS runtime_events_run_id_sequence_idx
        ON runtime_events (run_id, sequence);

    CREATE TABLE IF NOT EXISTS run_message_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        messages JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS run_message_snapshots_run_id_created_idx
        ON run_message_snapshots (run_id, created_at, snapshot_id);

    CREATE TABLE IF NOT EXISTS agent_interactions (
        interaction_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        interaction_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        request_payload JSONB NOT NULL,
        response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL,
        resolved_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS agent_interactions_run_id_created_idx
        ON agent_interactions (run_id, created_at, interaction_id);
    """
    _INSERT_RUN_SQL: Final[str] = """
    INSERT INTO agent_runs (
        run_id,
        session_id,
        tape_id,
        parent_run_id,
        agent_id,
        status,
        started_at,
        ended_at,
        metadata,
        result,
        error
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11)
    ON CONFLICT (run_id)
    DO UPDATE SET
        session_id = EXCLUDED.session_id,
        tape_id = EXCLUDED.tape_id,
        parent_run_id = EXCLUDED.parent_run_id,
        agent_id = EXCLUDED.agent_id,
        status = EXCLUDED.status,
        started_at = EXCLUDED.started_at,
        ended_at = EXCLUDED.ended_at,
        metadata = EXCLUDED.metadata,
        result = EXCLUDED.result,
        error = EXCLUDED.error,
        updated_at = NOW()
    RETURNING *
    """
    _UPDATE_RUN_SQL: Final[str] = """
    UPDATE agent_runs
    SET status = $2,
        ended_at = $3,
        metadata = $4::jsonb,
        result = $5::jsonb,
        error = $6,
        updated_at = NOW()
    WHERE run_id = $1
    RETURNING *
    """
    _SELECT_RUN_SQL: Final[str] = "SELECT * FROM agent_runs WHERE run_id = $1"
    _LIST_RUNS_SQL: Final[str] = (
        "SELECT * FROM agent_runs WHERE session_id = $1 ORDER BY started_at, run_id"
    )
    _INSERT_EVENT_SQL: Final[str] = """
    WITH inserted AS (
        INSERT INTO runtime_events (
            event_id,
            run_id,
            event_kind,
            payload,
            created_at
        )
        VALUES ($1, $2, $3, $4::jsonb, $5)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING *
    )
    SELECT * FROM inserted
    UNION ALL
    SELECT * FROM runtime_events
    WHERE event_id = $1 AND NOT EXISTS (SELECT 1 FROM inserted)
    """
    _REPLAY_EVENTS_SQL: Final[str] = """
    SELECT * FROM runtime_events
    WHERE run_id = $1 AND sequence > $2
    ORDER BY sequence
    LIMIT $3
    """
    _UPSERT_MESSAGE_SNAPSHOT_SQL: Final[str] = """
    INSERT INTO run_message_snapshots (
        snapshot_id,
        run_id,
        messages,
        metadata,
        created_at
    )
    VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
    ON CONFLICT (snapshot_id)
    DO UPDATE SET
        run_id = EXCLUDED.run_id,
        messages = EXCLUDED.messages,
        metadata = EXCLUDED.metadata,
        created_at = EXCLUDED.created_at,
        updated_at = NOW()
    RETURNING *
    """
    _SELECT_MESSAGE_SNAPSHOT_SQL: Final[str] = (
        "SELECT * FROM run_message_snapshots WHERE snapshot_id = $1"
    )
    _LIST_MESSAGE_SNAPSHOTS_SQL: Final[str] = (
        "SELECT * FROM run_message_snapshots WHERE run_id = $1 "
        "ORDER BY created_at, snapshot_id"
    )
    _INSERT_INTERACTION_SQL: Final[str] = """
    WITH inserted AS (
        INSERT INTO agent_interactions (
            interaction_id,
            run_id,
            interaction_kind,
            status,
            request_payload,
            response_payload,
            metadata,
            created_at,
            resolved_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9)
        ON CONFLICT (interaction_id) DO NOTHING
        RETURNING *
    )
    SELECT * FROM inserted
    UNION ALL
    SELECT * FROM agent_interactions
    WHERE interaction_id = $1 AND NOT EXISTS (SELECT 1 FROM inserted)
    """
    _RESOLVE_INTERACTION_SQL: Final[str] = """
    WITH resolved AS (
        UPDATE agent_interactions
        SET status = $2,
            response_payload = $3::jsonb,
            resolved_at = $4,
            updated_at = NOW()
        WHERE interaction_id = $1 AND resolved_at IS NULL
        RETURNING *
    )
    SELECT * FROM resolved
    UNION ALL
    SELECT * FROM agent_interactions
    WHERE interaction_id = $1 AND NOT EXISTS (SELECT 1 FROM resolved)
    """
    _SELECT_INTERACTION_SQL: Final[str] = (
        "SELECT * FROM agent_interactions WHERE interaction_id = $1"
    )
    _LIST_INTERACTIONS_SQL: Final[str] = (
        "SELECT * FROM agent_interactions WHERE run_id = $1 "
        "ORDER BY created_at, interaction_id"
    )

    def __init__(self, *, pool: PGPool) -> None:
        self._pool: PGPool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_SCHEMA_SQL)
            self._schema_ready = True
        return pool

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_RUN_SQL,
            record.run_id,
            record.session_id,
            record.tape_id,
            record.parent_run_id,
            record.agent_id,
            record.status,
            record.started_at,
            record.ended_at,
            record.metadata,
            record.result,
            record.error,
        )
        return _agent_run_from_row(_required_row(row, "agent run insert"))

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
        if error is not None:
            _require_non_empty("error", error)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_RUN_SQL,
            run_id,
            status,
            ended_at,
            metadata,
            result,
            error,
        )
        if row is None:
            raise KeyError(f"agent run not found: {run_id}")
        return _agent_run_from_row(row)

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        _require_non_empty("run_id", run_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_RUN_SQL, run_id)
        if row is None:
            return None
        return _agent_run_from_row(row)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        _require_non_empty("session_id", session_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_RUNS_SQL, session_id)
        return [_agent_run_from_row(row) for row in rows]

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_EVENT_SQL,
            record.event_id,
            record.run_id,
            record.event_kind,
            record.payload,
            record.created_at,
        )
        return _runtime_event_from_row(_required_row(row, "runtime event insert"))

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
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._REPLAY_EVENTS_SQL, run_id, after_sequence, limit)
        return [_runtime_event_from_row(row) for row in rows]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_MESSAGE_SNAPSHOT_SQL,
            record.snapshot_id,
            record.run_id,
            record.messages,
            record.metadata,
            record.created_at,
        )
        return _message_snapshot_from_row(_required_row(row, "message snapshot upsert"))

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        _require_non_empty("snapshot_id", snapshot_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_MESSAGE_SNAPSHOT_SQL, snapshot_id)
        if row is None:
            return None
        return _message_snapshot_from_row(row)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        _require_non_empty("run_id", run_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_MESSAGE_SNAPSHOTS_SQL, run_id)
        return [_message_snapshot_from_row(row) for row in rows]

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._INSERT_INTERACTION_SQL,
            record.interaction_id,
            record.run_id,
            record.interaction_kind,
            record.status,
            record.request_payload,
            record.response_payload,
            record.metadata,
            record.created_at,
            record.resolved_at,
        )
        return _interaction_from_row(_required_row(row, "agent interaction insert"))

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
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._RESOLVE_INTERACTION_SQL,
            interaction_id,
            status,
            response_payload,
            resolved_at,
        )
        if row is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        return _interaction_from_row(row)

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        _require_non_empty("interaction_id", interaction_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_INTERACTION_SQL, interaction_id)
        if row is None:
            return None
        return _interaction_from_row(row)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        _require_non_empty("run_id", run_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_INTERACTIONS_SQL, run_id)
        return [_interaction_from_row(row) for row in rows]


def _required_row(
    row: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _agent_run_from_row(row: dict[str, object]) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_required_str(row, "run_id", context="agent run row"),
        session_id=_required_str(row, "session_id", context="agent run row"),
        tape_id=_optional_str(row, "tape_id", context="agent run row"),
        parent_run_id=_optional_str(row, "parent_run_id", context="agent run row"),
        agent_id=_optional_str(row, "agent_id", context="agent run row"),
        status=_required_str(row, "status", context="agent run row"),
        started_at=_required_datetime(row, "started_at", context="agent run row"),
        ended_at=_optional_datetime(row, "ended_at", context="agent run row"),
        metadata=_required_json_object(row, "metadata", context="agent run row"),
        result=_required_json_object(row, "result", context="agent run row"),
        error=_optional_str(row, "error", context="agent run row"),
    )


def _runtime_event_from_row(row: dict[str, object]) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_required_int(row, "sequence", context="runtime event row"),
        event_id=_required_str(row, "event_id", context="runtime event row"),
        run_id=_required_str(row, "run_id", context="runtime event row"),
        event_kind=_required_str(row, "event_kind", context="runtime event row"),
        payload=_required_json_object(row, "payload", context="runtime event row"),
        created_at=_required_datetime(row, "created_at", context="runtime event row"),
    )


def _message_snapshot_from_row(
    row: dict[str, object],
) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_required_str(
            row,
            "snapshot_id",
            context="message snapshot row",
        ),
        run_id=_required_str(row, "run_id", context="message snapshot row"),
        messages=_required_message_list(row, "messages"),
        metadata=_required_json_object(
            row,
            "metadata",
            context="message snapshot row",
        ),
        created_at=_required_datetime(
            row,
            "created_at",
            context="message snapshot row",
        ),
    )


def _interaction_from_row(row: dict[str, object]) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_required_str(
            row,
            "interaction_id",
            context="agent interaction row",
        ),
        run_id=_required_str(row, "run_id", context="agent interaction row"),
        interaction_kind=_required_str(
            row,
            "interaction_kind",
            context="agent interaction row",
        ),
        status=_required_str(row, "status", context="agent interaction row"),
        request_payload=_required_json_object(
            row,
            "request_payload",
            context="agent interaction row",
        ),
        response_payload=_required_json_object(
            row,
            "response_payload",
            context="agent interaction row",
        ),
        metadata=_required_json_object(
            row,
            "metadata",
            context="agent interaction row",
        ),
        created_at=_required_datetime(
            row,
            "created_at",
            context="agent interaction row",
        ),
        resolved_at=_optional_datetime(
            row,
            "resolved_at",
            context="agent interaction row",
        ),
    )


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
        raise TypeError(f"postgres {context} must include JSON object {key}")
    return cast(JSONObject, value)


def _required_message_list(row: dict[str, object], key: str) -> list[JSONObject]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError("postgres message snapshot row must include list messages")
    messages: list[JSONObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("postgres message snapshot messages must contain objects")
        messages.append(cast(JSONObject, item))
    return messages


def _require_non_empty(field_name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_json_object(field_name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive_int(field_name: str, value: int) -> None:
    _require_non_negative_int(field_name, value)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")


__all__ = [
    "AgentInteractionRecord",
    "AgentRunRecord",
    "PGRuntimeStore",
    "RunMessageSnapshotRecord",
    "RuntimeEventRecord",
]
