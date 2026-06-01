"""Durable runtime store records and persistence."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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


class JSONLRuntimeStore:
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        await self._append_jsonl("runs.jsonl", _agent_run_to_payload(record))
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
        if error is not None:
            _require_non_empty("error", error)
        existing = await self.load_agent_run(run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {run_id}")
        updated = AgentRunRecord(
            run_id=existing.run_id,
            session_id=existing.session_id,
            tape_id=existing.tape_id,
            parent_run_id=existing.parent_run_id,
            agent_id=existing.agent_id,
            status=status,
            started_at=existing.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        await self._append_jsonl("runs.jsonl", _agent_run_to_payload(updated))
        return updated

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        _require_non_empty("run_id", run_id)
        runs = await self._latest_runs()
        return runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        _require_non_empty("session_id", session_id)
        runs = [
            run
            for run in (await self._latest_runs()).values()
            if run.session_id == session_id
        ]
        return sorted(runs, key=lambda run: (run.started_at, run.run_id))

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
        with self._lock:
            runs = self._latest_runs_sync()
            candidates = [
                run
                for run in runs.values()
                if run.status in {"requested", "expired"}
                and run.metadata.get("execution_binding_kind")
                in {"external_worker", "local_attached"}
                and run.metadata.get("executor_kind") == executor_kind
                and (session_id is None or run.session_id == session_id)
            ]
            if not candidates:
                return None
            selected = min(candidates, key=lambda run: (run.started_at, run.run_id))
            metadata = {**selected.metadata, **claim_metadata}
            claimed = AgentRunRecord(
                run_id=selected.run_id,
                session_id=selected.session_id,
                tape_id=selected.tape_id,
                parent_run_id=selected.parent_run_id,
                agent_id=selected.agent_id,
                status="claimed",
                started_at=selected.started_at,
                ended_at=selected.ended_at,
                metadata=cast(JSONObject, metadata),
                result=selected.result,
                error=selected.error,
            )
            self._append_jsonl_sync("runs.jsonl", _agent_run_to_payload(claimed))
            return claimed

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
        with self._lock:
            events = self._runtime_events_sync()
            for event in events:
                if event.event_id == record.event_id:
                    return event
            sequence = 1 + max(
                (event.sequence or 0 for event in events),
                default=0,
            )
            persisted = RuntimeEventRecord(
                event_id=record.event_id,
                run_id=record.run_id,
                event_kind=record.event_kind,
                payload=record.payload,
                created_at=record.created_at,
                sequence=sequence,
            )
            self._append_jsonl_sync(
                "events.jsonl", _runtime_event_to_payload(persisted)
            )
            return persisted

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
        events = [
            event
            for event in await self._runtime_events()
            if event.run_id == run_id and (event.sequence or 0) > after_sequence
        ]
        return sorted(events, key=lambda event: event.sequence or 0)[:limit]

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        _require_non_empty("event_id", event_id)
        for event in await self._runtime_events():
            if event.event_id == event_id:
                return event
        return None

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        await self._append_jsonl(
            "snapshots.jsonl", _message_snapshot_to_payload(record)
        )
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        _require_non_empty("snapshot_id", snapshot_id)
        snapshots = await self._latest_message_snapshots()
        return snapshots.get(snapshot_id)

    async def list_message_snapshots(
        self,
        run_id: str,
    ) -> list[RunMessageSnapshotRecord]:
        _require_non_empty("run_id", run_id)
        snapshots = [
            snapshot
            for snapshot in (await self._latest_message_snapshots()).values()
            if snapshot.run_id == run_id
        ]
        return sorted(snapshots, key=lambda item: (item.created_at, item.snapshot_id))

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        existing = (await self._latest_interactions()).get(record.interaction_id)
        if existing is not None:
            return existing
        await self._append_jsonl("interactions.jsonl", _interaction_to_payload(record))
        return record

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
        existing = (await self._latest_interactions()).get(interaction_id)
        if existing is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        if existing.resolved_at is not None:
            return existing
        updated = AgentInteractionRecord(
            interaction_id=existing.interaction_id,
            run_id=existing.run_id,
            interaction_kind=existing.interaction_kind,
            status=status,
            request_payload=existing.request_payload,
            response_payload=response_payload,
            metadata=existing.metadata,
            created_at=existing.created_at,
            resolved_at=resolved_at,
        )
        await self._append_jsonl("interactions.jsonl", _interaction_to_payload(updated))
        return updated

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        _require_non_empty("interaction_id", interaction_id)
        return (await self._latest_interactions()).get(interaction_id)

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        _require_non_empty("run_id", run_id)
        interactions = [
            interaction
            for interaction in (await self._latest_interactions()).values()
            if interaction.run_id == run_id
        ]
        return sorted(
            interactions,
            key=lambda item: (item.created_at, item.interaction_id),
        )

    async def _latest_runs(self) -> dict[str, AgentRunRecord]:
        with self._lock:
            return self._latest_runs_sync()

    def _latest_runs_sync(self) -> dict[str, AgentRunRecord]:
        records: dict[str, AgentRunRecord] = {}
        for payload in self._read_jsonl_sync("runs.jsonl"):
            record = _agent_run_from_payload(payload)
            records[record.run_id] = record
        return records

    async def _runtime_events(self) -> list[RuntimeEventRecord]:
        with self._lock:
            return self._runtime_events_sync()

    def _runtime_events_sync(self) -> list[RuntimeEventRecord]:
        return [
            _runtime_event_from_payload(payload)
            for payload in self._read_jsonl_sync("events.jsonl")
        ]

    async def _latest_message_snapshots(
        self,
    ) -> dict[str, RunMessageSnapshotRecord]:
        snapshots: dict[str, RunMessageSnapshotRecord] = {}
        with self._lock:
            for payload in self._read_jsonl_sync("snapshots.jsonl"):
                snapshot = _message_snapshot_from_payload(payload)
                snapshots[snapshot.snapshot_id] = snapshot
        return snapshots

    async def _latest_interactions(self) -> dict[str, AgentInteractionRecord]:
        interactions: dict[str, AgentInteractionRecord] = {}
        with self._lock:
            for payload in self._read_jsonl_sync("interactions.jsonl"):
                interaction = _interaction_from_payload(payload)
                interactions[interaction.interaction_id] = interaction
        return interactions

    async def _append_jsonl(self, filename: str, payload: JSONObject) -> None:
        with self._lock:
            self._append_jsonl_sync(filename, payload)

    def _append_jsonl_sync(self, filename: str, payload: JSONObject) -> None:
        path = self._root / filename
        self._root.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")

    def _read_jsonl_sync(self, filename: str) -> list[JSONObject]:
        path = self._root / filename
        if not path.exists():
            return []
        rows: list[JSONObject] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise TypeError(f"{filename}:{line_number} must be a JSON object")
                rows.append(cast(JSONObject, payload))
        return rows


class SQLiteRuntimeStore:
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS agent_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        tape_id TEXT,
        parent_run_id TEXT,
        agent_id TEXT,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        metadata TEXT NOT NULL,
        result TEXT NOT NULL,
        error TEXT
    );

    CREATE INDEX IF NOT EXISTS agent_runs_session_id_idx
        ON agent_runs (session_id, started_at, run_id);

    CREATE TABLE IF NOT EXISTS runtime_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE NOT NULL,
        run_id TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS runtime_events_run_id_sequence_idx
        ON runtime_events (run_id, sequence);

    CREATE TABLE IF NOT EXISTS run_message_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        messages TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS run_message_snapshots_run_id_created_idx
        ON run_message_snapshots (run_id, created_at, snapshot_id);

    CREATE TABLE IF NOT EXISTS agent_interactions (
        interaction_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        interaction_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        request_payload TEXT NOT NULL,
        response_payload TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );

    CREATE INDEX IF NOT EXISTS agent_interactions_run_id_created_idx
        ON agent_interactions (run_id, created_at, interaction_id);
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(self._CREATE_SCHEMA_SQL)

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
                    started_at, ended_at, metadata, result, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        if error is not None:
            _require_non_empty("error", error)
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
                if run.metadata.get("execution_binding_kind")
                in {"external_worker", "local_attached"}
                and run.metadata.get("executor_kind") == executor_kind
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

    CREATE INDEX IF NOT EXISTS agent_runs_external_worker_claim_idx
        ON agent_runs (
            (metadata->>'executor_kind'),
            session_id,
            started_at,
            run_id
        )
        WHERE status IN ('requested', 'expired')
          AND metadata->>'execution_binding_kind' IN ('external_worker', 'local_attached')
          AND metadata->>'executor_kind' IS NOT NULL;

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
    _CLAIM_ATTACHED_EXECUTOR_RUN_SQL: Final[str] = """
    WITH candidate AS (
        SELECT run_id
        FROM agent_runs
        WHERE status IN ('requested', 'expired')
          AND metadata->>'execution_binding_kind' IN ('external_worker', 'local_attached')
          AND metadata->>'executor_kind' = $2
          AND ($1::text IS NULL OR session_id = $1)
        ORDER BY started_at, run_id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE agent_runs
    SET status = 'claimed',
        metadata = metadata || $3::jsonb,
        updated_at = NOW()
    WHERE run_id = (SELECT run_id FROM candidate)
    RETURNING *
    """
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
    _SELECT_EVENT_SQL: Final[str] = "SELECT * FROM runtime_events WHERE event_id = $1"
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
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._CLAIM_ATTACHED_EXECUTOR_RUN_SQL,
            session_id,
            executor_kind,
            claim_metadata,
        )
        if row is None:
            return None
        return _agent_run_from_row(row)

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        """Compatibility wrapper for the legacy external-worker store API."""
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

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

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        _require_non_empty("event_id", event_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_EVENT_SQL, event_id)
        if row is None:
            return None
        return _runtime_event_from_row(row)

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


def _json_to_sql(value: JSONValue) -> str:
    return json.dumps(value, sort_keys=True)


def _json_from_sql(value: object, *, context: str) -> JSONValue:
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include JSON text")
    loaded = json.loads(value)
    return cast(JSONValue, loaded)


def _json_object_from_sql(value: object, *, context: str) -> JSONObject:
    loaded = _json_from_sql(value, context=context)
    if not isinstance(loaded, dict):
        raise TypeError(f"sqlite {context} must decode to a JSON object")
    return cast(JSONObject, loaded)


def _message_list_from_sql(value: object, *, context: str) -> list[JSONObject]:
    loaded = _json_from_sql(value, context=context)
    if not isinstance(loaded, list):
        raise TypeError(f"sqlite {context} must decode to a list")
    messages: list[JSONObject] = []
    for item in loaded:
        if not isinstance(item, dict):
            raise TypeError(f"sqlite {context} messages must contain objects")
        messages.append(cast(JSONObject, item))
    return messages


def _sqlite_value(row: sqlite3.Row, key: str, *, context: str) -> object:
    try:
        return row[key]
    except (IndexError, KeyError) as exc:
        raise TypeError(f"sqlite {context} row must include {key}") from exc


def _sqlite_required_str(row: sqlite3.Row, key: str, *, context: str) -> str:
    value = _sqlite_value(row, key, context=context)
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} row must include string {key}")
    return value


def _sqlite_optional_str(row: sqlite3.Row, key: str, *, context: str) -> str | None:
    value = _sqlite_value(row, key, context=context)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} row must include string or NULL {key}")
    return value


def _sqlite_required_int(row: sqlite3.Row, key: str, *, context: str) -> int:
    value = _sqlite_value(row, key, context=context)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sqlite {context} row must include int {key}")
    return value


def _sqlite_required_datetime(row: sqlite3.Row, key: str, *, context: str) -> datetime:
    return datetime.fromisoformat(_sqlite_required_str(row, key, context=context))


def _sqlite_optional_datetime(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = _sqlite_optional_str(row, key, context=context)
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _agent_run_sqlite_values(record: AgentRunRecord) -> tuple[object, ...]:
    return (
        record.run_id,
        record.session_id,
        record.tape_id,
        record.parent_run_id,
        record.agent_id,
        record.status,
        _datetime_to_json(record.started_at),
        None if record.ended_at is None else _datetime_to_json(record.ended_at),
        _json_to_sql(record.metadata),
        _json_to_sql(record.result),
        record.error,
    )


def _agent_run_from_sqlite_row(row: sqlite3.Row) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_sqlite_required_str(row, "run_id", context="agent run"),
        session_id=_sqlite_required_str(row, "session_id", context="agent run"),
        tape_id=_sqlite_optional_str(row, "tape_id", context="agent run"),
        parent_run_id=_sqlite_optional_str(row, "parent_run_id", context="agent run"),
        agent_id=_sqlite_optional_str(row, "agent_id", context="agent run"),
        status=_sqlite_required_str(row, "status", context="agent run"),
        started_at=_sqlite_required_datetime(row, "started_at", context="agent run"),
        ended_at=_sqlite_optional_datetime(row, "ended_at", context="agent run"),
        metadata=_json_object_from_sql(row["metadata"], context="agent run metadata"),
        result=_json_object_from_sql(row["result"], context="agent run result"),
        error=_sqlite_optional_str(row, "error", context="agent run"),
    )


def _runtime_event_from_sqlite_row(row: sqlite3.Row) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_sqlite_required_int(row, "sequence", context="runtime event"),
        event_id=_sqlite_required_str(row, "event_id", context="runtime event"),
        run_id=_sqlite_required_str(row, "run_id", context="runtime event"),
        event_kind=_sqlite_required_str(row, "event_kind", context="runtime event"),
        payload=_json_object_from_sql(
            row["payload"],
            context="runtime event payload",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="runtime event",
        ),
    )


def _message_snapshot_from_sqlite_row(
    row: sqlite3.Row,
) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_sqlite_required_str(
            row,
            "snapshot_id",
            context="message snapshot",
        ),
        run_id=_sqlite_required_str(row, "run_id", context="message snapshot"),
        messages=_message_list_from_sql(
            row["messages"],
            context="message snapshot messages",
        ),
        metadata=_json_object_from_sql(
            row["metadata"],
            context="message snapshot metadata",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="message snapshot",
        ),
    )


def _interaction_sqlite_values(record: AgentInteractionRecord) -> tuple[object, ...]:
    return (
        record.interaction_id,
        record.run_id,
        record.interaction_kind,
        record.status,
        _json_to_sql(record.request_payload),
        _json_to_sql(record.response_payload),
        _json_to_sql(record.metadata),
        _datetime_to_json(record.created_at),
        None if record.resolved_at is None else _datetime_to_json(record.resolved_at),
    )


def _interaction_from_sqlite_row(row: sqlite3.Row) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_sqlite_required_str(
            row,
            "interaction_id",
            context="agent interaction",
        ),
        run_id=_sqlite_required_str(row, "run_id", context="agent interaction"),
        interaction_kind=_sqlite_required_str(
            row,
            "interaction_kind",
            context="agent interaction",
        ),
        status=_sqlite_required_str(row, "status", context="agent interaction"),
        request_payload=_json_object_from_sql(
            row["request_payload"],
            context="agent interaction request payload",
        ),
        response_payload=_json_object_from_sql(
            row["response_payload"],
            context="agent interaction response payload",
        ),
        metadata=_json_object_from_sql(
            row["metadata"],
            context="agent interaction metadata",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="agent interaction",
        ),
        resolved_at=_sqlite_optional_datetime(
            row,
            "resolved_at",
            context="agent interaction",
        ),
    )


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


def _agent_run_to_payload(record: AgentRunRecord) -> JSONObject:
    return {
        "run_id": record.run_id,
        "session_id": record.session_id,
        "tape_id": record.tape_id,
        "parent_run_id": record.parent_run_id,
        "agent_id": record.agent_id,
        "status": record.status,
        "started_at": _datetime_to_json(record.started_at),
        "ended_at": (
            None if record.ended_at is None else _datetime_to_json(record.ended_at)
        ),
        "metadata": record.metadata,
        "result": record.result,
        "error": record.error,
    }


def _agent_run_from_payload(payload: JSONObject) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_required_payload_str(payload, "run_id", context="agent run payload"),
        session_id=_required_payload_str(
            payload,
            "session_id",
            context="agent run payload",
        ),
        tape_id=_optional_payload_str(payload, "tape_id", context="agent run payload"),
        parent_run_id=_optional_payload_str(
            payload,
            "parent_run_id",
            context="agent run payload",
        ),
        agent_id=_optional_payload_str(
            payload, "agent_id", context="agent run payload"
        ),
        status=_required_payload_str(payload, "status", context="agent run payload"),
        started_at=_required_payload_datetime(
            payload,
            "started_at",
            context="agent run payload",
        ),
        ended_at=_optional_payload_datetime(
            payload,
            "ended_at",
            context="agent run payload",
        ),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="agent run payload",
        ),
        result=_required_payload_json_object(
            payload,
            "result",
            context="agent run payload",
        ),
        error=_optional_payload_str(payload, "error", context="agent run payload"),
    )


def _runtime_event_to_payload(record: RuntimeEventRecord) -> JSONObject:
    return {
        "sequence": record.sequence,
        "event_id": record.event_id,
        "run_id": record.run_id,
        "event_kind": record.event_kind,
        "payload": record.payload,
        "created_at": _datetime_to_json(record.created_at),
    }


def _runtime_event_from_payload(payload: JSONObject) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_required_payload_int(
            payload,
            "sequence",
            context="runtime event payload",
        ),
        event_id=_required_payload_str(
            payload,
            "event_id",
            context="runtime event payload",
        ),
        run_id=_required_payload_str(
            payload, "run_id", context="runtime event payload"
        ),
        event_kind=_required_payload_str(
            payload,
            "event_kind",
            context="runtime event payload",
        ),
        payload=_required_payload_json_object(
            payload,
            "payload",
            context="runtime event payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="runtime event payload",
        ),
    )


def _message_snapshot_to_payload(record: RunMessageSnapshotRecord) -> JSONObject:
    return {
        "snapshot_id": record.snapshot_id,
        "run_id": record.run_id,
        "messages": record.messages,
        "metadata": record.metadata,
        "created_at": _datetime_to_json(record.created_at),
    }


def _message_snapshot_from_payload(payload: JSONObject) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_required_payload_str(
            payload,
            "snapshot_id",
            context="message snapshot payload",
        ),
        run_id=_required_payload_str(
            payload,
            "run_id",
            context="message snapshot payload",
        ),
        messages=_required_payload_message_list(payload, "messages"),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="message snapshot payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="message snapshot payload",
        ),
    )


def _interaction_to_payload(record: AgentInteractionRecord) -> JSONObject:
    return {
        "interaction_id": record.interaction_id,
        "run_id": record.run_id,
        "interaction_kind": record.interaction_kind,
        "status": record.status,
        "request_payload": record.request_payload,
        "response_payload": record.response_payload,
        "metadata": record.metadata,
        "created_at": _datetime_to_json(record.created_at),
        "resolved_at": (
            None
            if record.resolved_at is None
            else _datetime_to_json(record.resolved_at)
        ),
    }


def _interaction_from_payload(payload: JSONObject) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_required_payload_str(
            payload,
            "interaction_id",
            context="agent interaction payload",
        ),
        run_id=_required_payload_str(
            payload,
            "run_id",
            context="agent interaction payload",
        ),
        interaction_kind=_required_payload_str(
            payload,
            "interaction_kind",
            context="agent interaction payload",
        ),
        status=_required_payload_str(
            payload,
            "status",
            context="agent interaction payload",
        ),
        request_payload=_required_payload_json_object(
            payload,
            "request_payload",
            context="agent interaction payload",
        ),
        response_payload=_required_payload_json_object(
            payload,
            "response_payload",
            context="agent interaction payload",
        ),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="agent interaction payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="agent interaction payload",
        ),
        resolved_at=_optional_payload_datetime(
            payload,
            "resolved_at",
            context="agent interaction payload",
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


def _datetime_to_json(value: datetime) -> str:
    _require_datetime("datetime", value)
    return value.isoformat()


def _required_payload_str(payload: JSONObject, key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string {key}")
    return value


def _optional_payload_str(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string or None {key}")
    return value


def _required_payload_int(payload: JSONObject, key: str, *, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must include int {key}")
    return value


def _required_payload_datetime(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> datetime:
    value = _required_payload_str(payload, key, context=context)
    return datetime.fromisoformat(value)


def _optional_payload_datetime(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} must include datetime string or None {key}")
    return datetime.fromisoformat(value)


def _required_payload_json_object(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{context} must include JSON object {key}")
    return cast(JSONObject, value)


def _required_payload_message_list(payload: JSONObject, key: str) -> list[JSONObject]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TypeError("message snapshot payload must include list messages")
    messages: list[JSONObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("message snapshot payload messages must contain objects")
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
    "JSONLRuntimeStore",
    "PGRuntimeStore",
    "RunMessageSnapshotRecord",
    "RuntimeEventRecord",
    "SQLiteRuntimeStore",
]
