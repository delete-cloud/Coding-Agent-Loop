from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import json
from typing import Any, cast

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.pg import (
    PGCheckpointStore,
    PGPool,
    PGSessionOwnerStore,
    PGTapeStore,
)
from agentkit.storage.protocols import TapeInfo, TapeSearchResult

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    PGRuntimeStore,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
    _agent_run_from_row,
    _interaction_from_row,
    _message_snapshot_from_row,
    _runtime_event_from_row,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
)
from coding_agent.server.stores.session_store import PGSessionMetadataStore


class PGDurableStore:
    """PostgreSQL protected write facade for durable owner fencing."""

    _UPSERT_SESSION_SQL = """
    INSERT INTO agent_http_sessions (session_id, payload)
    VALUES ($1, $2::jsonb)
    ON CONFLICT (session_id)
    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
    """
    _DELETE_SESSION_SQL = "DELETE FROM agent_http_sessions WHERE session_id = $1"
    _SELECT_SESSION_FOR_UPDATE_SQL = """
    SELECT payload
    FROM agent_http_sessions
    WHERE session_id = $1
    FOR UPDATE
    """
    _SELECT_SESSION_BY_TAPE_SQL = """
    SELECT session_id
    FROM session_tapes
    WHERE tape_id = $1
    LIMIT 1
    """
    _CREATE_SESSION_TAPES_SQL = """
    CREATE TABLE IF NOT EXISTS session_tapes (
        session_id TEXT PRIMARY KEY,
        tape_id TEXT UNIQUE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS session_tapes_tape_id_idx
        ON session_tapes (tape_id);
    """
    _SELECT_SESSION_TAPE_FOR_UPDATE_SQL = """
    SELECT tape_id
    FROM session_tapes
    WHERE session_id = $1
    FOR UPDATE
    """
    _SELECT_TAPE_SESSION_FOR_UPDATE_SQL = """
    SELECT session_id
    FROM session_tapes
    WHERE tape_id = $1
    FOR UPDATE
    """
    _UPSERT_SESSION_TAPE_SQL = """
    INSERT INTO session_tapes (session_id, tape_id)
    VALUES ($1, $2)
    ON CONFLICT DO NOTHING
    """
    _SELECT_OWNER_FOR_UPDATE_SQL = """
    SELECT owner_id, lease_expires_at, fencing_token
    FROM session_owners
    WHERE session_id = $1
    FOR UPDATE
    """
    _SELECT_RUN_SESSION_FOR_UPDATE_SQL = """
    SELECT session_id
    FROM agent_runs
    WHERE run_id = $1
    FOR UPDATE
    """
    _SELECT_INTERACTION_RUN_FOR_UPDATE_SQL = """
    SELECT run_id
    FROM agent_interactions
    WHERE interaction_id = $1
    FOR UPDATE
    """
    _SELECT_CHECKPOINT_META_FOR_UPDATE_SQL = """
    SELECT meta
    FROM agent_checkpoints
    WHERE checkpoint_id = $1
    FOR UPDATE
    """
    _UPSERT_OWNED_RUN_SQL = """
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
    WHERE agent_runs.session_id = $2
    RETURNING *
    """
    _INSERT_OWNED_EVENT_SQL = """
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
    SELECT runtime_events.*
    FROM runtime_events
    WHERE runtime_events.event_id = $1
      AND runtime_events.run_id = $2
      AND NOT EXISTS (SELECT 1 FROM inserted)
    """
    _UPSERT_OWNED_MESSAGE_SNAPSHOT_SQL = """
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
        messages = EXCLUDED.messages,
        metadata = EXCLUDED.metadata,
        created_at = EXCLUDED.created_at,
        updated_at = NOW()
    WHERE run_message_snapshots.run_id = $2
    RETURNING *
    """
    _INSERT_OWNED_INTERACTION_SQL = """
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
    SELECT agent_interactions.*
    FROM agent_interactions
    WHERE agent_interactions.interaction_id = $1
      AND agent_interactions.run_id = $2
      AND NOT EXISTS (SELECT 1 FROM inserted)
    """
    _UPSERT_OWNED_CHECKPOINT_SQL = """
    INSERT INTO agent_checkpoints (
        checkpoint_id,
        tape_id,
        meta,
        entries,
        plugin_states,
        extra
    )
    VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb)
    ON CONFLICT (checkpoint_id) DO UPDATE SET
        tape_id = EXCLUDED.tape_id,
        meta = EXCLUDED.meta,
        entries = EXCLUDED.entries,
        plugin_states = EXCLUDED.plugin_states,
        extra = EXCLUDED.extra
    WHERE agent_checkpoints.meta->>'session_id' = $7
    RETURNING checkpoint_id
    """
    _INSERT_TAPE_SQL = PGTapeStore._INSERT_SQL
    _TRUNCATE_TAPE_SQL = PGTapeStore._TRUNCATE_SQL
    _DELETE_CHECKPOINT_SQL = PGCheckpointStore._DELETE_SQL
    _DELETE_NEWER_CHECKPOINTS_SQL = """
    DELETE FROM agent_checkpoints
    WHERE tape_id = $1
      AND meta->>'session_id' = $2
      AND (meta->>'entry_count')::int > $3
    """
    _DELETE_SESSION_TAPE_SQL = "DELETE FROM session_tapes WHERE session_id = $1"

    def __init__(self, *, pool: PGPool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        pool = await self._pool.get_pool()
        if self._schema_ready:
            return
        _ = await pool.execute(PGSessionOwnerStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGSessionMetadataStore._CREATE_TABLE_SQL)
        _ = await pool.execute(self._CREATE_SESSION_TAPES_SQL)
        _ = await pool.execute(PGTapeStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGCheckpointStore._CREATE_TABLE_SQL)
        _ = await pool.execute(PGRuntimeStore._CREATE_SCHEMA_SQL)
        self._schema_ready = True

    async def _with_transaction(self, body: Callable[[Any], Any]) -> Any:
        await self._ensure_schema()
        connection = await self._pool.acquire()
        try:
            _ = await connection.execute("BEGIN")
            result = await body(connection)
            _ = await connection.execute("COMMIT")
            return result
        except BaseException:
            try:
                _ = await connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            await self._pool.release(connection)

    async def session_id_for_tape(self, tape_id: str) -> str | None:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_SESSION_BY_TAPE_SQL, tape_id)
        if row is None:
            return None
        return _required_str(dict(row), "session_id")

    async def save_session(
        self,
        authority: OwnerAuthority,
        payload: dict[str, Any],
    ) -> None:
        _require_payload_session(authority, payload)
        tape_id = payload.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            if tape_id:
                await self._bind_tape(connection, authority.session_id, tape_id)
            _ = await connection.fetchrow(
                self._SELECT_SESSION_FOR_UPDATE_SQL,
                authority.session_id,
            )
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                payload,
            )

        await self._with_transaction(body)

    async def delete_session(self, authority: OwnerAuthority) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await connection.fetchrow(
                self._SELECT_SESSION_FOR_UPDATE_SQL,
                authority.session_id,
            )
            await connection.execute(self._DELETE_SESSION_SQL, authority.session_id)
            await connection.execute(
                self._DELETE_SESSION_TAPE_SQL, authority.session_id
            )

        await self._with_transaction(body)

    async def append_tape_entries(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        if not entries:
            return

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, tape_id)
            payload_values = [json.dumps(entry) for entry in entries]
            await connection.execute(self._INSERT_TAPE_SQL, tape_id, payload_values)

        await self._with_transaction(body)

    async def truncate_tape(
        self,
        authority: OwnerAuthority,
        tape_id: str,
        keep: int,
    ) -> None:
        if keep < 0:
            raise ValueError("keep must be >= 0")

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, tape_id)
            await connection.execute(self._TRUNCATE_TAPE_SQL, tape_id, keep)

        await self._with_transaction(body)

    async def create_agent_run(
        self,
        authority: OwnerAuthority,
        record: AgentRunRecord,
    ) -> AgentRunRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("run target belongs to another owner")
        if record.tape_id is None:
            raise SessionOwnershipConflictError("run target is not bound to a tape")

        async def body(connection: Any) -> AgentRunRecord:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, record.tape_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_RUN_SQL,
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
            return _agent_run_from_row(
                _required_owned_row(row, "run target belongs to another owner")
            )

        return cast(AgentRunRecord, await self._with_transaction(body))

    async def update_agent_run(
        self,
        authority: OwnerAuthority,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        async def body(connection: Any) -> AgentRunRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, run_id)
            row = await connection.fetchrow(
                PGRuntimeStore._UPDATE_RUN_SQL,
                run_id,
                status,
                ended_at,
                metadata,
                result,
                error,
            )
            return _agent_run_from_row(_required_row(row, "agent run update"))

        return cast(AgentRunRecord, await self._with_transaction(body))

    async def append_runtime_event(
        self,
        authority: OwnerAuthority,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        async def body(connection: Any) -> RuntimeEventRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._INSERT_OWNED_EVENT_SQL,
                record.event_id,
                record.run_id,
                record.event_kind,
                record.payload,
                record.created_at,
            )
            return _runtime_event_from_row(
                _required_owned_row(
                    row,
                    "runtime event target belongs to another owner",
                )
            )

        return cast(RuntimeEventRecord, await self._with_transaction(body))

    async def save_message_snapshot(
        self,
        authority: OwnerAuthority,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        async def body(connection: Any) -> RunMessageSnapshotRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_MESSAGE_SNAPSHOT_SQL,
                record.snapshot_id,
                record.run_id,
                record.messages,
                record.metadata,
                record.created_at,
            )
            return _message_snapshot_from_row(
                _required_owned_row(
                    row,
                    "message snapshot target belongs to another owner",
                )
            )

        return cast(RunMessageSnapshotRecord, await self._with_transaction(body))

    async def create_agent_interaction(
        self,
        authority: OwnerAuthority,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        async def body(connection: Any) -> AgentInteractionRecord:
            await self._require_owner(connection, authority)
            await self._require_run_owner(connection, authority, record.run_id)
            row = await connection.fetchrow(
                self._INSERT_OWNED_INTERACTION_SQL,
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
            return _interaction_from_row(
                _required_owned_row(row, "interaction target belongs to another owner")
            )

        return cast(AgentInteractionRecord, await self._with_transaction(body))

    async def resolve_agent_interaction(
        self,
        authority: OwnerAuthority,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        async def body(connection: Any) -> AgentInteractionRecord:
            await self._require_owner(connection, authority)
            existing = await connection.fetchrow(
                self._SELECT_INTERACTION_RUN_FOR_UPDATE_SQL,
                interaction_id,
            )
            if existing is None:
                raise KeyError(f"agent interaction not found: {interaction_id}")
            await self._require_run_owner(
                connection,
                authority,
                _required_str(dict(existing), "run_id"),
            )
            row = await connection.fetchrow(
                PGRuntimeStore._RESOLVE_INTERACTION_SQL,
                interaction_id,
                status,
                response_payload,
                resolved_at,
            )
            return _interaction_from_row(
                _required_row(row, "agent interaction resolve")
            )

        return cast(AgentInteractionRecord, await self._with_transaction(body))

    async def claim_attached_executor_run(
        self,
        authorities: Mapping[str, OwnerAuthority],
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        if session_id is not None:
            authority = authorities.get(session_id)
            if authority is None:
                return None
            return await self._claim_attached_executor_run_for_authority(
                authority,
                executor_kind=executor_kind,
                claim_metadata=claim_metadata,
            )
        for authority in sorted(authorities.values(), key=lambda item: item.session_id):
            claimed = await self._claim_attached_executor_run_for_authority(
                authority,
                executor_kind=executor_kind,
                claim_metadata=claim_metadata,
            )
            if claimed is not None:
                return claimed
        return None

    async def _claim_attached_executor_run_for_authority(
        self,
        authority: OwnerAuthority,
        *,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None:
        async def body(connection: Any) -> AgentRunRecord | None:
            await self._require_owner(connection, authority)
            row = await connection.fetchrow(
                PGRuntimeStore._CLAIM_ATTACHED_EXECUTOR_RUN_SQL,
                authority.session_id,
                executor_kind,
                claim_metadata,
            )
            if row is None:
                return None
            return _agent_run_from_row(row)

        return cast(AgentRunRecord | None, await self._with_transaction(body))

    async def save_checkpoint(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
    ) -> None:
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, meta.tape_id)
            row = await connection.fetchrow(
                self._UPSERT_OWNED_CHECKPOINT_SQL,
                meta.checkpoint_id,
                meta.tape_id,
                _checkpoint_meta_payload(meta),
                list(snapshot.tape_entries),
                snapshot.plugin_states,
                snapshot.extra,
                authority.session_id,
            )
            _required_owned_row(row, "checkpoint target belongs to another owner")

        await self._with_transaction(body)

    async def delete_checkpoint(
        self,
        authority: OwnerAuthority,
        checkpoint_id: str,
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_checkpoint_owner(connection, authority, checkpoint_id)
            await connection.execute(self._DELETE_CHECKPOINT_SQL, checkpoint_id)

        await self._with_transaction(body)

    async def restore_checkpoint_state(
        self,
        authority: OwnerAuthority,
        snapshot: CheckpointSnapshot,
        session_payload: dict[str, Any],
    ) -> None:
        _require_payload_session(authority, session_payload)
        meta = snapshot.meta
        if meta.session_id != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )
        if session_payload.get("tape_id") != meta.tape_id:
            raise SessionOwnershipConflictError(
                "checkpoint restore session payload has mismatched tape id"
            )

        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, meta.tape_id)
            await self._require_checkpoint_owner(
                connection,
                authority,
                meta.checkpoint_id,
            )
            await connection.execute(self._TRUNCATE_TAPE_SQL, meta.tape_id, 0)
            if snapshot.tape_entries:
                payload_values = [json.dumps(entry) for entry in snapshot.tape_entries]
                await connection.execute(
                    self._INSERT_TAPE_SQL, meta.tape_id, payload_values
                )
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                session_payload,
            )
            await connection.execute(
                self._DELETE_NEWER_CHECKPOINTS_SQL,
                meta.tape_id,
                authority.session_id,
                meta.entry_count,
            )

        await self._with_transaction(body)

    async def _require_owner(self, connection: Any, authority: OwnerAuthority) -> None:
        row = await connection.fetchrow(
            self._SELECT_OWNER_FOR_UPDATE_SQL,
            authority.session_id,
        )
        if row is None:
            raise SessionOwnershipConflictError(
                "session owner lease is missing",
                reason=SessionOwnershipConflictReason.MISSING_OWNER,
            )
        row_dict = dict(row)
        lease_expires_at = row_dict.get("lease_expires_at")
        if not isinstance(lease_expires_at, datetime):
            raise TypeError("postgres owner row missing datetime lease_expires_at")
        now = datetime.now(lease_expires_at.tzinfo)
        if lease_expires_at <= now:
            raise SessionOwnershipConflictError(
                "session owner lease has expired",
                reason=SessionOwnershipConflictReason.EXPIRED_LEASE,
            )
        if (
            row_dict.get("owner_id") != authority.owner_id
            or row_dict.get("fencing_token") != authority.epoch
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    async def _require_stable_tape(
        self,
        connection: Any,
        authority: OwnerAuthority,
        tape_id: str,
    ) -> None:
        row = await connection.fetchrow(
            self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
            tape_id,
        )
        if row is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        if dict(row).get("session_id") != authority.session_id:
            raise SessionOwnershipConflictError("tape target belongs to another owner")

    async def _bind_tape(
        self,
        connection: Any,
        session_id: str,
        tape_id: str,
    ) -> None:
        await connection.execute(self._UPSERT_SESSION_TAPE_SQL, session_id, tape_id)
        session_row = await connection.fetchrow(
            self._SELECT_SESSION_TAPE_FOR_UPDATE_SQL,
            session_id,
        )
        if session_row is None:
            tape_row = await connection.fetchrow(
                self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
                tape_id,
            )
            if tape_row is not None and dict(tape_row).get("session_id") != session_id:
                raise SessionOwnershipConflictError(
                    "tape target belongs to another session"
                )
            raise SessionOwnershipConflictError("session tape target is not bound")
        if dict(session_row).get("tape_id") != tape_id:
            raise SessionOwnershipConflictError("session tape target cannot be rebound")
        tape_row = await connection.fetchrow(
            self._SELECT_TAPE_SESSION_FOR_UPDATE_SQL,
            tape_id,
        )
        if tape_row is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        if dict(tape_row).get("session_id") != session_id:
            raise SessionOwnershipConflictError(
                "tape target belongs to another session"
            )

    async def _session_id_for_run(
        self,
        connection: Any,
        run_id: str,
        *,
        for_update: bool,
    ) -> str | None:
        del for_update
        row = await connection.fetchrow(self._SELECT_RUN_SESSION_FOR_UPDATE_SQL, run_id)
        if row is None:
            return None
        return _required_str(dict(row), "session_id")

    async def _require_run_owner(
        self,
        connection: Any,
        authority: OwnerAuthority,
        run_id: str,
    ) -> None:
        session_id = await self._session_id_for_run(
            connection,
            run_id,
            for_update=True,
        )
        if session_id is None:
            raise KeyError(f"agent run not found: {run_id}")
        if session_id != authority.session_id:
            raise SessionOwnershipConflictError("run target belongs to another owner")

    async def _require_checkpoint_owner(
        self,
        connection: Any,
        authority: OwnerAuthority,
        checkpoint_id: str,
    ) -> None:
        row = await connection.fetchrow(
            self._SELECT_CHECKPOINT_META_FOR_UPDATE_SQL,
            checkpoint_id,
        )
        if row is None:
            raise KeyError(f"checkpoint not found: {checkpoint_id}")
        meta = _required_dict(dict(row), "meta")
        if meta.get("session_id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "checkpoint target belongs to another owner"
            )


class FencedPGTapeStore:
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGTapeStore(pool=pool)
        self._authority_for_session = authority_for_session

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        session_id = await self._require_session_id_for_tape_async(tape_id)
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
        session_id = await self._require_session_id_for_tape_async(tape_id)
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

    async def _require_session_id_for_tape_async(self, tape_id: str) -> str:
        session_id = await self._durable_store.session_id_for_tape(tape_id)
        if session_id is None:
            raise SessionOwnershipConflictError("tape target is not bound to a session")
        return session_id


class FencedPGCheckpointStore:
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGCheckpointStore(pool=pool)
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
        snapshot = await self._delegate.load(checkpoint_id)
        if snapshot is None:
            return
        session_id = snapshot.meta.session_id
        if session_id is None:
            raise SessionOwnershipConflictError(
                "checkpoint target is not bound to a session"
            )
        await self._durable_store.delete_checkpoint(
            self._authority_for_session(session_id),
            checkpoint_id,
        )


class FencedPGRuntimeStore:
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
        authorities: Callable[[], Mapping[str, OwnerAuthority]],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGRuntimeStore(pool=pool)
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
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        existing = await self._delegate.load_agent_run(run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {run_id}")
        return await self._durable_store.update_agent_run(
            self._authority_for_session(existing.session_id),
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
        claim_metadata: JSONObject,
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
        existing = await self._delegate.load_agent_run(record.run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {record.run_id}")
        return await self._durable_store.append_runtime_event(
            self._authority_for_session(existing.session_id),
            record,
        )

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

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        return await self._delegate.load_runtime_event(event_id)

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        existing = await self._delegate.load_agent_run(record.run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {record.run_id}")
        return await self._durable_store.save_message_snapshot(
            self._authority_for_session(existing.session_id),
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
        existing = await self._delegate.load_agent_run(record.run_id)
        if existing is None:
            raise KeyError(f"agent run not found: {record.run_id}")
        return await self._durable_store.create_agent_interaction(
            self._authority_for_session(existing.session_id),
            record,
        )

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        existing = await self._delegate.load_agent_interaction(interaction_id)
        if existing is None:
            raise KeyError(f"agent interaction not found: {interaction_id}")
        run = await self._delegate.load_agent_run(existing.run_id)
        if run is None:
            raise KeyError(f"agent run not found: {existing.run_id}")
        return await self._durable_store.resolve_agent_interaction(
            self._authority_for_session(run.session_id),
            interaction_id,
            status=status,
            response_payload=response_payload,
            resolved_at=resolved_at,
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


def _require_payload_session(
    authority: OwnerAuthority,
    payload: dict[str, Any],
) -> None:
    payload_id = payload.get("id")
    if payload_id != authority.session_id:
        raise SessionOwnershipConflictError("session payload belongs to another owner")
    payload_session_id = payload.get("session_id")
    if payload_session_id is not None and payload_session_id != authority.session_id:
        raise SessionOwnershipConflictError("session payload belongs to another owner")


def _checkpoint_meta_payload(meta: CheckpointMeta) -> dict[str, Any]:
    return {
        "checkpoint_id": meta.checkpoint_id,
        "tape_id": meta.tape_id,
        "session_id": meta.session_id,
        "entry_count": meta.entry_count,
        "window_start": meta.window_start,
        "created_at": meta.created_at.isoformat(),
        "label": meta.label,
    }


def _required_row(row: dict[str, object] | None, context: str) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _required_owned_row(
    row: dict[str, object] | None,
    conflict_message: str,
) -> dict[str, object]:
    if row is None:
        raise SessionOwnershipConflictError(conflict_message)
    return row


def _required_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres row must include string {key}")
    return value


def _required_dict(row: dict[str, object], key: str) -> dict[str, object]:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres row must include dict {key}")
    return cast(dict[str, object], value)
