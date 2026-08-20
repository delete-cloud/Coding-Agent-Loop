"""Fenced runtime run/event/interaction writes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    AgentInteractionRecord,
    RuntimeEventRecord,
    RunMessageSnapshotRecord,
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
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.helpers import (
    _require_json_object,
    _require_non_empty,
)


class LocalRuntimeMixin:
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
