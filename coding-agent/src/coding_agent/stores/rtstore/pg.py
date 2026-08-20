"""PostgreSQL runtime store backend."""

from __future__ import annotations

from datetime import datetime
from typing import Final
from coding_agent.stores.rtstore.pg_sql import PGRuntimeSqlMixin
from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.pg_codec import (
    _agent_run_from_row,
    _interaction_from_row,
    _message_snapshot_from_row,
    _required_row,
    _runtime_event_from_row,
)
from coding_agent.stores.rtstore.validate import (
    _normalize_optional_error,
    _require_datetime,
    _require_json_object,
    _require_non_empty,
    _require_non_negative_int,
    _require_positive_int,
)


class PGRuntimeStore(PGRuntimeSqlMixin):
    _DEFAULT_REPLAY_LIMIT: Final[int] = 1000

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
            record.superseded_by_checkpoint_id,
            record.superseded_at,
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
        error = _normalize_optional_error(error)
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
