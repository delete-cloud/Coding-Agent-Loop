"""Owned runtime, checkpoint, and topic-reconcile SQL."""

from __future__ import annotations

from agentkit.storage.pg import (
    PGCheckpointStore,
    PGTapeStore,
)


class PgRuntimeSqlMixin:
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
