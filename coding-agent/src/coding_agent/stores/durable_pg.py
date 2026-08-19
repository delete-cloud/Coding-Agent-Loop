from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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

from coding_agent.topics.store import (
    PGTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
    TopicStatus,
    _topic_anchor_from_row,
    _topic_cost_from_row,
    _topic_from_row,
    _topic_recall_link_from_row,
)
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    CursorEpochMismatchError,
    DEFAULT_HARNESS_PROJECTION,
    EffectLedgerSlot,
    EventRecord,
    JSONObject,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    PGRuntimeStore,
    ProjectionCursor,
    RawCursor,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
    SessionFactSourceState,
    TrustedHandoff,
    _agent_run_from_row,
    _interaction_from_row,
    _message_snapshot_from_row,
    _require_non_empty,
    _require_positive_int,
    _runtime_event_from_row,
    assert_projection_binding,
    assert_raw_cursor_not_expired,
    assert_trusted_handoff,
    effect_status_may_replace,
    format_u64,
    parse_u64,
    receipt_generation_may_replace,
    stored_trusted_handoff,
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
    _SUPERSEDE_RUNS_AFTER_CHECKPOINT_SQL = """
    UPDATE agent_runs
    SET superseded_by_checkpoint_id = $3,
        superseded_at = NOW(),
        updated_at = NOW()
    WHERE session_id = $1
      AND started_at > $2
      AND superseded_at IS NULL
    """
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
        error,
        superseded_by_checkpoint_id,
        superseded_at
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12, $13
    )
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
    _DELETE_TOPIC_RECALL_LINKS_FOR_TAPE_SQL = """
    DELETE FROM topic_recall_links
    WHERE source_topic_id IN (
        SELECT topic_id FROM topics WHERE tape_id = $1
    )
       OR recalled_topic_id IN (
        SELECT topic_id FROM topics WHERE tape_id = $1
    )
    """
    _DELETE_TOPIC_COSTS_FOR_TAPE_SQL = """
    DELETE FROM topic_costs
    WHERE topic_id IN (
        SELECT topic_id FROM topics WHERE tape_id = $1
    )
    """
    _DELETE_TOPIC_ANCHORS_AFTER_CHECKPOINT_SQL = """
    DELETE FROM topic_anchors
    WHERE tape_id = $1
      AND seq >= $2
    """
    _DELETE_TOPICS_AFTER_CHECKPOINT_SQL = """
    DELETE FROM topics
    WHERE tape_id = $1
      AND (
        topic_initial_seq >= $2
        OR created_at > $3
      )
    """
    _REOPEN_TOPICS_CLOSED_AFTER_CHECKPOINT_SQL = """
    UPDATE topics
    SET status = 'open',
        summary = NULL,
        topic_finalized_seq = NULL,
        finalized_at = NULL,
        metadata = '{}'::jsonb,
        updated_at = NOW()
    WHERE tape_id = $1
      AND status IN ('finalized', 'aborted')
      AND (
        topic_finalized_seq >= $2
        OR finalized_at > $3
      )
    """
    _SELECT_TOPIC_SESSION_TAPE_SQL = """
    SELECT session_id, tape_id
    FROM topics
    WHERE topic_id = $1
    """
    _SELECT_TOPIC_SESSION_TAPE_FOR_UPDATE_SQL = """
    SELECT session_id, tape_id
    FROM topics
    WHERE topic_id = $1
    FOR UPDATE
    """
    _DELETE_SESSION_TAPE_SQL = "DELETE FROM session_tapes WHERE session_id = $1"
    _CREATE_HARNESS_FACT_SOURCE_SQL = """
    CREATE TABLE IF NOT EXISTS session_fact_source (
        session_id TEXT PRIMARY KEY,
        session_seq BIGINT NOT NULL,
        retention_floor BIGINT NOT NULL,
        projection TEXT NOT NULL,
        projection_epoch BIGINT NOT NULL,
        trusted_handoff_seq BIGINT,
        trusted_handoff_epoch BIGINT,
        trusted_handoff_projection TEXT,
        trusted_handoff_payload JSONB,
        trusted_handoff_accepted_at TIMESTAMPTZ
    );
    CREATE TABLE IF NOT EXISTS session_event_records (
        session_id TEXT NOT NULL,
        session_seq BIGINT NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        event_kind TEXT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        projection_epoch BIGINT NOT NULL,
        PRIMARY KEY (session_id, session_seq)
    );
    CREATE TABLE IF NOT EXISTS session_mailbox_slots (
        session_id TEXT NOT NULL,
        slot_id TEXT NOT NULL,
        lane TEXT NOT NULL,
        disposition TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, slot_id)
    );
    CREATE TABLE IF NOT EXISTS session_effect_slots (
        session_id TEXT NOT NULL,
        effect_id TEXT NOT NULL,
        status TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, effect_id)
    );
    CREATE TABLE IF NOT EXISTS session_receipt_slots (
        session_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        generation TEXT NOT NULL,
        payload JSONB NOT NULL,
        compensation_effect_id TEXT,
        PRIMARY KEY (session_id, receipt_id)
    );
    """
    _MIGRATE_HARNESS_FACT_SOURCE_SQL = """
    ALTER TABLE session_fact_source
        ADD COLUMN IF NOT EXISTS trusted_handoff_seq BIGINT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_epoch BIGINT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_projection TEXT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_payload JSONB,
        ADD COLUMN IF NOT EXISTS trusted_handoff_accepted_at TIMESTAMPTZ
    """
    _SELECT_FACT_SOURCE_FOR_UPDATE_SQL = """
    SELECT *
    FROM session_fact_source
    WHERE session_id = $1
    FOR UPDATE
    """
    _SELECT_FACT_SOURCE_SQL = """
    SELECT *
    FROM session_fact_source
    WHERE session_id = $1
    """
    _INSERT_FACT_SOURCE_SQL = """
    INSERT INTO session_fact_source (
        session_id, session_seq, retention_floor, projection, projection_epoch
    )
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (session_id) DO NOTHING
    RETURNING *
    """
    _UPDATE_FACT_SOURCE_SEQ_SQL = """
    UPDATE session_fact_source
    SET session_seq = $2
    WHERE session_id = $1
    RETURNING *
    """
    _BUMP_PROJECTION_EPOCH_SQL = """
    UPDATE session_fact_source
    SET projection_epoch = projection_epoch + 1
    WHERE session_id = $1
    RETURNING *
    """
    _UPDATE_RETENTION_FLOOR_SQL = """
    UPDATE session_fact_source
    SET retention_floor = $2
    WHERE session_id = $1
    RETURNING *
    """
    _UPDATE_TRUSTED_HANDOFF_SQL = """
    UPDATE session_fact_source
    SET trusted_handoff_seq = $2,
        trusted_handoff_epoch = $3,
        trusted_handoff_projection = $4,
        trusted_handoff_payload = $5::jsonb,
        trusted_handoff_accepted_at = $6
    WHERE session_id = $1
    RETURNING *
    """
    _INSERT_SESSION_EVENT_SQL = """
    INSERT INTO session_event_records (
        session_id, session_seq, event_id, event_kind, payload, created_at,
        projection_epoch
    )
    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
    RETURNING *
    """
    _SELECT_SESSION_EVENT_SQL = """
    SELECT * FROM session_event_records
    WHERE session_id = $1 AND session_seq = $2
    """
    _REPLAY_SESSION_EVENTS_AFTER_SQL = """
    SELECT * FROM session_event_records
    WHERE session_id = $1 AND session_seq > $2
    ORDER BY session_seq
    LIMIT $3
    """
    _REPLAY_PROJECTION_EVENTS_AFTER_SQL = """
    SELECT * FROM session_event_records
    WHERE session_id = $1 AND session_seq > $2 AND projection_epoch = $3
    ORDER BY session_seq
    LIMIT $4
    """
    _REPLAY_SESSION_EVENTS_FROM_SQL = """
    SELECT * FROM session_event_records
    WHERE session_id = $1 AND session_seq >= $2
    ORDER BY session_seq
    LIMIT $3
    """
    _UPSERT_MAILBOX_SLOT_SQL = """
    INSERT INTO session_mailbox_slots (
        session_id, slot_id, lane, disposition, payload
    )
    VALUES ($1, $2, $3, $4, $5::jsonb)
    ON CONFLICT (session_id, slot_id)
    DO UPDATE SET
        lane = EXCLUDED.lane,
        disposition = EXCLUDED.disposition,
        payload = EXCLUDED.payload
    RETURNING *
    """
    _SELECT_MAILBOX_SLOT_SQL = """
    SELECT * FROM session_mailbox_slots
    WHERE session_id = $1 AND slot_id = $2
    """
    _UPSERT_EFFECT_SLOT_SQL = """
    INSERT INTO session_effect_slots (
        session_id, effect_id, status, payload
    )
    VALUES ($1, $2, $3, $4::jsonb)
    ON CONFLICT (session_id, effect_id)
    DO UPDATE SET
        status = EXCLUDED.status,
        payload = EXCLUDED.payload
    RETURNING *
    """
    _SELECT_EFFECT_SLOT_SQL = """
    SELECT * FROM session_effect_slots
    WHERE session_id = $1 AND effect_id = $2
    """
    _UPSERT_RECEIPT_SLOT_SQL = """
    INSERT INTO session_receipt_slots (
        session_id, receipt_id, generation, payload, compensation_effect_id
    )
    VALUES ($1, $2, $3, $4::jsonb, $5)
    ON CONFLICT (session_id, receipt_id)
    DO UPDATE SET
        generation = EXCLUDED.generation,
        payload = EXCLUDED.payload,
        compensation_effect_id = EXCLUDED.compensation_effect_id
    RETURNING *
    """
    _SELECT_RECEIPT_SLOT_SQL = """
    SELECT * FROM session_receipt_slots
    WHERE session_id = $1 AND receipt_id = $2
    """

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
        _ = await pool.execute(PGTopicStore._CREATE_SCHEMA_SQL)
        _ = await pool.execute(self._CREATE_HARNESS_FACT_SOURCE_SQL)
        _ = await pool.execute(self._MIGRATE_HARNESS_FACT_SOURCE_SQL)
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

    async def session_id_for_topic(self, topic_id: str) -> str | None:
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_TOPIC_SESSION_TAPE_SQL, topic_id)
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
                record.superseded_by_checkpoint_id,
                record.superseded_at,
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
                self._SUPERSEDE_RUNS_AFTER_CHECKPOINT_SQL,
                authority.session_id,
                meta.created_at,
                meta.checkpoint_id,
            )
            await self._reconcile_topics_after_checkpoint_restore(
                connection,
                tape_id=meta.tape_id,
                entry_count=meta.entry_count,
                checkpoint_created_at=meta.created_at,
            )
            await connection.execute(
                self._DELETE_NEWER_CHECKPOINTS_SQL,
                meta.tape_id,
                authority.session_id,
                meta.entry_count,
            )
            await self._open_projection_epoch(connection, authority.session_id)

        await self._with_transaction(body)

    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        _require_payload_session(authority, unit.session_state)
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("run target belongs to another owner")

        async def body(connection: Any) -> AuthoritativeCommit:
            await self._require_owner(connection, authority)
            if tape_id:
                await self._bind_tape(connection, authority.session_id, tape_id)
            if unit.run_state is not None:
                if unit.run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                await self._require_stable_tape(
                    connection, authority, unit.run_state.tape_id
                )
            fact = await self._ensure_fact_source(connection, authority.session_id)
            next_seq = fact.session_seq_int + 1
            _ = await connection.fetchrow(
                self._UPDATE_FACT_SOURCE_SEQ_SQL,
                authority.session_id,
                next_seq,
            )
            event_row = await connection.fetchrow(
                self._INSERT_SESSION_EVENT_SQL,
                authority.session_id,
                next_seq,
                unit.event.event_id,
                unit.event.event_kind,
                unit.event.payload,
                unit.event.created_at,
                fact.projection_epoch_int,
            )
            await connection.execute(
                self._UPSERT_SESSION_SQL,
                authority.session_id,
                unit.session_state,
            )
            if unit.run_state is not None:
                run_row = await connection.fetchrow(
                    self._UPSERT_OWNED_RUN_SQL,
                    unit.run_state.run_id,
                    unit.run_state.session_id,
                    unit.run_state.tape_id,
                    unit.run_state.parent_run_id,
                    unit.run_state.agent_id,
                    unit.run_state.status,
                    unit.run_state.started_at,
                    unit.run_state.ended_at,
                    unit.run_state.metadata,
                    unit.run_state.result,
                    unit.run_state.error,
                    unit.run_state.superseded_by_checkpoint_id,
                    unit.run_state.superseded_at,
                )
                _required_owned_row(run_row, "run target belongs to another owner")
            _ = await connection.fetchrow(
                self._UPSERT_MAILBOX_SLOT_SQL,
                authority.session_id,
                unit.mailbox.slot_id,
                unit.mailbox.lane,
                unit.mailbox.disposition,
                unit.mailbox.payload,
            )
            existing_effect = await connection.fetchrow(
                self._SELECT_EFFECT_SLOT_SQL,
                authority.session_id,
                unit.effect.effect_id,
            )
            if existing_effect is None or effect_status_may_replace(
                current=_required_str(dict(existing_effect), "status"),
                incoming=unit.effect.status,
            ):
                _ = await connection.fetchrow(
                    self._UPSERT_EFFECT_SLOT_SQL,
                    authority.session_id,
                    unit.effect.effect_id,
                    unit.effect.status,
                    unit.effect.payload,
                )
            existing_receipt = await connection.fetchrow(
                self._SELECT_RECEIPT_SLOT_SQL,
                authority.session_id,
                unit.receipt.receipt_id,
            )
            if existing_receipt is None or receipt_generation_may_replace(
                current=_required_str(dict(existing_receipt), "generation"),
                incoming=unit.receipt.generation,
            ):
                _ = await connection.fetchrow(
                    self._UPSERT_RECEIPT_SLOT_SQL,
                    authority.session_id,
                    unit.receipt.receipt_id,
                    unit.receipt.generation,
                    unit.receipt.payload,
                    unit.receipt.compensation_effect_id,
                )
            event = _event_record_from_pg_row(
                _required_row(event_row, "session event insert")
            )
            return AuthoritativeCommit(
                event=event,
                projection=fact.projection,
                projection_epoch=format_u64(fact.projection_epoch_int),
                raw_cursor=RawCursor(
                    session_id=authority.session_id,
                    session_seq=format_u64(next_seq),
                ),
            )

        return cast(AuthoritativeCommit, await self._with_transaction(body))

    async def load_session_fact_source(
        self,
        session_id: str,
    ) -> SessionFactSourceState | None:
        _require_non_empty("session_id", session_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, session_id)
        if row is None:
            return None
        return _fact_source_from_pg_row(dict(row)).state

    async def load_event_record(
        self,
        session_id: str,
        session_seq: str,
    ) -> EventRecord | None:
        _require_non_empty("session_id", session_id)
        seq = parse_u64(session_seq, field_name="session_seq")
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_SESSION_EVENT_SQL, session_id, seq)
        if row is None:
            return None
        return _event_record_from_pg_row(dict(row))

    async def load_mailbox_slot(
        self,
        session_id: str,
        slot_id: str,
    ) -> MailboxDispositionSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("slot_id", slot_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_MAILBOX_SLOT_SQL, session_id, slot_id)
        if row is None:
            return None
        return _mailbox_from_pg_row(dict(row))

    async def load_effect_slot(
        self,
        session_id: str,
        effect_id: str,
    ) -> EffectLedgerSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("effect_id", effect_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_EFFECT_SLOT_SQL, session_id, effect_id)
        if row is None:
            return None
        return _effect_from_pg_row(dict(row))

    async def load_receipt_slot(
        self,
        session_id: str,
        receipt_id: str,
    ) -> OperationReceiptSlot | None:
        _require_non_empty("session_id", session_id)
        _require_non_empty("receipt_id", receipt_id)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        row = await pool.fetchrow(self._SELECT_RECEIPT_SLOT_SQL, session_id, receipt_id)
        if row is None:
            return None
        return _receipt_from_pg_row(dict(row))

    async def replay_raw(
        self,
        cursor: RawCursor,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, cursor.session_id)
        if fact_row is None:
            return []
        fact = _fact_source_from_pg_row(dict(fact_row))
        assert_raw_cursor_not_expired(cursor, fact.state.retention_floor)
        after = parse_u64(cursor.session_seq, field_name="session_seq")
        rows = await pool.fetch(
            self._REPLAY_SESSION_EVENTS_AFTER_SQL,
            cursor.session_id,
            after,
            limit,
        )
        return [_event_record_from_pg_row(dict(row)) for row in rows]

    async def replay_from_retention_floor(
        self,
        session_id: str,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_non_empty("session_id", session_id)
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, session_id)
        if fact_row is None:
            return []
        fact = _fact_source_from_pg_row(dict(fact_row))
        rows = await pool.fetch(
            self._REPLAY_SESSION_EVENTS_FROM_SQL,
            session_id,
            fact.retention_floor_int,
            limit,
        )
        return [_event_record_from_pg_row(dict(row)) for row in rows]

    async def replay_projection(
        self,
        cursor: ProjectionCursor,
        *,
        limit: int = 1000,
    ) -> list[EventRecord]:
        _require_positive_int("limit", limit)
        await self._ensure_schema()
        pool = await self._pool.get_pool()
        fact_row = await pool.fetchrow(self._SELECT_FACT_SOURCE_SQL, cursor.session_id)
        if fact_row is None:
            raise CursorEpochMismatchError(
                f"projection cursor bound to epoch {cursor.epoch}, current is missing"
            )
        fact = _fact_source_from_pg_row(dict(fact_row))
        assert_projection_binding(cursor, fact.state)
        assert_raw_cursor_not_expired(
            RawCursor(session_id=cursor.session_id, session_seq=cursor.session_seq),
            fact.state.retention_floor,
        )
        after = parse_u64(cursor.session_seq, field_name="session_seq")
        epoch = parse_u64(cursor.epoch, field_name="epoch")
        rows = await pool.fetch(
            self._REPLAY_PROJECTION_EVENTS_AFTER_SQL,
            cursor.session_id,
            after,
            epoch,
            limit,
        )
        return [_event_record_from_pg_row(dict(row)) for row in rows]

    async def raise_retention_floor(
        self,
        authority: OwnerAuthority,
        retention_floor: str,
    ) -> SessionFactSourceState:
        floor = parse_u64(retention_floor, field_name="retention_floor")

        async def body(connection: Any) -> SessionFactSourceState:
            await self._require_owner(connection, authority)
            fact = await self._ensure_fact_source(connection, authority.session_id)
            if floor < fact.retention_floor_int:
                raise ValueError("retention_floor cannot move backwards")
            if floor > fact.session_seq_int + 1:
                raise ValueError("retention_floor cannot pass the physical log")
            row = await connection.fetchrow(
                self._UPDATE_RETENTION_FLOOR_SQL,
                authority.session_id,
                floor,
            )
            return _fact_source_from_pg_row(_required_row(row, "retention floor")).state

        return cast(SessionFactSourceState, await self._with_transaction(body))

    async def accept_trusted_handoff(
        self,
        authority: OwnerAuthority,
        handoff: TrustedHandoff,
    ) -> SessionFactSourceState:
        if handoff.session_id != authority.session_id:
            raise SessionOwnershipConflictError("handoff belongs to another session")

        async def body(connection: Any) -> SessionFactSourceState:
            await self._require_owner(connection, authority)
            row = await connection.fetchrow(
                self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
                authority.session_id,
            )
            if row is None:
                raise CursorEpochMismatchError(
                    f"trusted handoff bound to epoch {handoff.epoch}, current is missing"
                )
            fact = _fact_source_from_pg_row(dict(row))
            assert_trusted_handoff(handoff, fact.state)
            updated = await connection.fetchrow(
                self._UPDATE_TRUSTED_HANDOFF_SQL,
                authority.session_id,
                parse_u64(handoff.session_seq, field_name="session_seq"),
                parse_u64(handoff.epoch, field_name="epoch"),
                handoff.projection,
                handoff.payload,
                datetime.now(UTC),
            )
            return _fact_source_from_pg_row(
                _required_row(updated, "trusted handoff")
            ).state

        return cast(SessionFactSourceState, await self._with_transaction(body))

    async def _ensure_fact_source(
        self,
        connection: Any,
        session_id: str,
    ) -> _PgFactSource:
        existing = await connection.fetchrow(
            self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
            session_id,
        )
        if existing is not None:
            return _fact_source_from_pg_row(dict(existing))
        inserted = await connection.fetchrow(
            self._INSERT_FACT_SOURCE_SQL,
            session_id,
            0,
            0,
            DEFAULT_HARNESS_PROJECTION,
            0,
        )
        return _fact_source_from_pg_row(
            _required_row(inserted, "session fact source insert")
        )

    async def _open_projection_epoch(
        self,
        connection: Any,
        session_id: str,
    ) -> None:
        existing = await connection.fetchrow(
            self._SELECT_FACT_SOURCE_FOR_UPDATE_SQL,
            session_id,
        )
        if existing is None:
            _ = await connection.fetchrow(
                self._INSERT_FACT_SOURCE_SQL,
                session_id,
                0,
                0,
                DEFAULT_HARNESS_PROJECTION,
                1,
            )
            return
        _ = await connection.fetchrow(self._BUMP_PROJECTION_EPOCH_SQL, session_id)

    async def create_topic(
        self,
        authority: OwnerAuthority,
        record: TopicRecord,
    ) -> TopicRecord:
        if record.session_id != authority.session_id:
            raise SessionOwnershipConflictError("topic target belongs to another owner")

        async def body(connection: Any) -> TopicRecord:
            await self._require_owner(connection, authority)
            await self._require_stable_tape(connection, authority, record.tape_id)
            row = await connection.fetchrow(
                PGTopicStore._INSERT_TOPIC_SQL,
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
            topic = _topic_from_row(_required_row(row, "topic insert"))
            if (
                topic.session_id != authority.session_id
                or topic.tape_id != record.tape_id
            ):
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            return topic

        return cast(TopicRecord, await self._with_transaction(body))

    async def finalize_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            PGTopicStore._FINALIZE_TOPIC_SQL,
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
        metadata: JSONObject,
    ) -> TopicRecord:
        return await self._close_topic(
            authority,
            PGTopicStore._ABORT_TOPIC_SQL,
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
        query: str,
        operation: str,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        async def body(connection: Any) -> TopicRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [topic_id])
            row = await connection.fetchrow(
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

        return cast(TopicRecord, await self._with_transaction(body))

    async def delete_topic(
        self,
        authority: OwnerAuthority,
        topic_id: str,
    ) -> None:
        async def body(connection: Any) -> None:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [topic_id])
            await connection.execute(
                PGTopicStore._DELETE_TOPIC_RECALL_LINKS_SQL,
                topic_id,
            )
            await connection.execute(PGTopicStore._DELETE_TOPIC_COST_SQL, topic_id)
            await connection.execute(PGTopicStore._DELETE_TOPIC_ANCHORS_SQL, topic_id)
            await connection.execute(PGTopicStore._DELETE_TOPIC_SQL, topic_id)

        await self._with_transaction(body)

    async def record_topic_anchor(
        self,
        authority: OwnerAuthority,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        async def body(connection: Any) -> TopicAnchorRecord:
            await self._require_owner(connection, authority)
            topic_tapes = await self._lock_topic_targets(
                connection,
                authority,
                [record.topic_id],
            )
            if record.tape_id != topic_tapes[record.topic_id]:
                raise SessionOwnershipConflictError(
                    "topic anchor target belongs to another tape"
                )
            row = await connection.fetchrow(
                PGTopicStore._INSERT_ANCHOR_SQL,
                record.topic_id,
                record.tape_id,
                record.seq,
                record.anchor_type,
                record.entry_id,
                record.metadata,
            )
            return _topic_anchor_from_row(_required_row(row, "topic anchor upsert"))

        return cast(TopicAnchorRecord, await self._with_transaction(body))

    async def record_recall_link(
        self,
        authority: OwnerAuthority,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        async def body(connection: Any) -> TopicRecallLinkRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(
                connection,
                authority,
                [record.source_topic_id, record.recalled_topic_id],
            )
            row = await connection.fetchrow(
                PGTopicStore._INSERT_RECALL_LINK_SQL,
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

        return cast(TopicRecallLinkRecord, await self._with_transaction(body))

    async def update_topic_cost(
        self,
        authority: OwnerAuthority,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        async def body(connection: Any) -> TopicCostRecord:
            await self._require_owner(connection, authority)
            _ = await self._lock_topic_targets(connection, authority, [delta.topic_id])
            row = await connection.fetchrow(
                PGTopicStore._UPSERT_COST_SQL,
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

        return cast(TopicCostRecord, await self._with_transaction(body))

    async def _reconcile_topics_after_checkpoint_restore(
        self,
        connection: Any,
        *,
        tape_id: str,
        entry_count: int,
        checkpoint_created_at: datetime,
    ) -> None:
        if not tape_id:
            raise ValueError("tape_id must be non-empty")
        if entry_count < 0:
            raise ValueError("entry_count must be >= 0")
        await connection.execute(
            self._DELETE_TOPIC_RECALL_LINKS_FOR_TAPE_SQL,
            tape_id,
        )
        await connection.execute(
            self._DELETE_TOPIC_COSTS_FOR_TAPE_SQL,
            tape_id,
        )
        await connection.execute(
            self._DELETE_TOPIC_ANCHORS_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
        )
        await connection.execute(
            self._DELETE_TOPICS_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
            checkpoint_created_at,
        )
        await connection.execute(
            self._REOPEN_TOPICS_CLOSED_AFTER_CHECKPOINT_SQL,
            tape_id,
            entry_count,
            checkpoint_created_at,
        )

    async def _lock_topic_targets(
        self,
        connection: Any,
        authority: OwnerAuthority,
        topic_ids: list[str],
    ) -> dict[str, str]:
        targets: dict[str, str] = {}
        for topic_id in sorted(set(topic_ids)):
            row = await connection.fetchrow(
                self._SELECT_TOPIC_SESSION_TAPE_SQL, topic_id
            )
            if row is None:
                raise KeyError(f"topic not found: {topic_id}")
            row_dict = dict(row)
            session_id = _required_str(row_dict, "session_id")
            tape_id = _required_str(row_dict, "tape_id")
            if session_id != authority.session_id:
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
            targets[topic_id] = tape_id

        for tape_id in sorted(set(targets.values())):
            await self._require_stable_tape(connection, authority, tape_id)

        for topic_id in sorted(targets):
            row = await connection.fetchrow(
                self._SELECT_TOPIC_SESSION_TAPE_FOR_UPDATE_SQL,
                topic_id,
            )
            if row is None:
                raise KeyError(f"topic not found: {topic_id}")
            row_dict = dict(row)
            session_id = _required_str(row_dict, "session_id")
            tape_id = _required_str(row_dict, "tape_id")
            if session_id != authority.session_id or tape_id != targets[topic_id]:
                raise SessionOwnershipConflictError(
                    "topic target belongs to another owner"
                )
        return targets

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


class FencedPGTopicStore(PGTopicStore):
    def __init__(
        self,
        *,
        durable_store: PGDurableStore,
        pool: PGPool,
        authority_for_session: Callable[[str], OwnerAuthority],
    ) -> None:
        self._durable_store = durable_store
        self._delegate = PGTopicStore(pool=pool)
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
        metadata: JSONObject,
    ) -> TopicRecord:
        session_id = await self._require_session_id_for_topic(topic_id)
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
        metadata: JSONObject,
    ) -> TopicRecord:
        session_id = await self._require_session_id_for_topic(topic_id)
        return await self._durable_store.abort_topic(
            self._authority_for_session(session_id),
            topic_id,
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )

    async def delete_topic(self, topic_id: str) -> None:
        session_id = await self._require_session_id_for_topic(topic_id)
        await self._durable_store.delete_topic(
            self._authority_for_session(session_id),
            topic_id,
        )

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        return await self._delegate.load_topic(topic_id)

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
        return await self._delegate.list_topics(
            session_id=session_id,
            tape_id=tape_id,
            status=status,
            after_created_at=after_created_at,
            after_topic_id=after_topic_id,
            limit=limit,
            offset=offset,
        )

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        return await self._delegate.find_open_topic(
            session_id=session_id,
            tape_id=tape_id,
        )

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        session_id = await self._require_session_id_for_topic(record.topic_id)
        return await self._durable_store.record_topic_anchor(
            self._authority_for_session(session_id),
            record,
        )

    async def list_topic_anchors(self, topic_id: str) -> list[TopicAnchorRecord]:
        return await self._delegate.list_topic_anchors(topic_id)

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        session_id = await self._require_session_id_for_topic(record.source_topic_id)
        return await self._durable_store.record_recall_link(
            self._authority_for_session(session_id),
            record,
        )

    async def list_recall_links(
        self,
        source_topic_id: str,
    ) -> list[TopicRecallLinkRecord]:
        return await self._delegate.list_recall_links(source_topic_id)

    async def update_topic_cost(
        self,
        delta: TopicCostRecord,
    ) -> TopicCostRecord:
        session_id = await self._require_session_id_for_topic(delta.topic_id)
        return await self._durable_store.update_topic_cost(
            self._authority_for_session(session_id),
            delta,
        )

    async def load_topic_cost(self, topic_id: str) -> TopicCostRecord | None:
        return await self._delegate.load_topic_cost(topic_id)

    async def _require_session_id_for_topic(self, topic_id: str) -> str:
        session_id = await self._durable_store.session_id_for_topic(topic_id)
        if session_id is None:
            raise KeyError(f"topic not found: {topic_id}")
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


def _required_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres row must include int {key}")
    return value


def _optional_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres row must include string or None {key}")
    return value


def _optional_int(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres row must include int or None {key}")
    return value


def _optional_dict(row: dict[str, object], key: str) -> dict[str, object] | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"postgres row must include dict or None {key}")
    return cast(dict[str, object], value)


def _required_datetime(row: dict[str, object], key: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres row must include datetime {key}")
    return value


@dataclass(frozen=True)
class _PgFactSource:
    state: SessionFactSourceState
    session_seq_int: int
    retention_floor_int: int
    projection_epoch_int: int
    projection: str


def _fact_source_from_pg_row(row: dict[str, object]) -> _PgFactSource:
    session_seq = _required_int(row, "session_seq")
    retention_floor = _required_int(row, "retention_floor")
    projection_epoch = _required_int(row, "projection_epoch")
    projection = _required_str(row, "projection")
    session_id = _required_str(row, "session_id")
    return _PgFactSource(
        state=SessionFactSourceState(
            session_id=session_id,
            session_seq=format_u64(session_seq),
            retention_floor=format_u64(retention_floor),
            projection=projection,
            projection_epoch=format_u64(projection_epoch),
            trusted_handoff=stored_trusted_handoff(
                session_id=session_id,
                session_seq=_optional_int(row, "trusted_handoff_seq"),
                epoch=_optional_int(row, "trusted_handoff_epoch"),
                projection=_optional_str(row, "trusted_handoff_projection"),
                payload=_optional_dict(row, "trusted_handoff_payload"),
            ),
        ),
        session_seq_int=session_seq,
        retention_floor_int=retention_floor,
        projection_epoch_int=projection_epoch,
        projection=projection,
    )


def _event_record_from_pg_row(row: dict[str, object]) -> EventRecord:
    return EventRecord(
        event_id=_required_str(row, "event_id"),
        session_id=_required_str(row, "session_id"),
        event_kind=_required_str(row, "event_kind"),
        payload=cast(JSONObject, _required_dict(row, "payload")),
        created_at=_required_datetime(row, "created_at"),
        session_seq=format_u64(_required_int(row, "session_seq")),
        projection_epoch=format_u64(_required_int(row, "projection_epoch")),
    )


def _mailbox_from_pg_row(row: dict[str, object]) -> MailboxDispositionSlot:
    return MailboxDispositionSlot(
        slot_id=_required_str(row, "slot_id"),
        lane=_required_str(row, "lane"),
        disposition=_required_str(row, "disposition"),
        payload=cast(JSONObject, _required_dict(row, "payload")),
    )


def _effect_from_pg_row(row: dict[str, object]) -> EffectLedgerSlot:
    return EffectLedgerSlot(
        effect_id=_required_str(row, "effect_id"),
        status=_required_str(row, "status"),
        payload=cast(JSONObject, _required_dict(row, "payload")),
    )


def _receipt_from_pg_row(row: dict[str, object]) -> OperationReceiptSlot:
    return OperationReceiptSlot(
        receipt_id=_required_str(row, "receipt_id"),
        generation=_required_str(row, "generation"),
        payload=cast(JSONObject, _required_dict(row, "payload")),
        compensation_effect_id=_optional_str(row, "compensation_effect_id"),
    )
