"""PostgreSQL runtime-store SQL constants."""

from __future__ import annotations

from typing import Final


class PGRuntimeSqlMixin:
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
        superseded_by_checkpoint_id TEXT,
        superseded_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS agent_runs_session_id_idx
        ON agent_runs (session_id, started_at, run_id);

    ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS superseded_by_checkpoint_id TEXT;
    ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

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
          AND metadata->>'executor_ref_kind' IN ('external_worker', 'local_attached')
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
        error,
        superseded_by_checkpoint_id,
        superseded_at
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12, $13
    )
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
          AND metadata->>'executor_ref_kind' IN ('external_worker', 'local_attached')
          AND metadata->>'executor_kind' = $2
          AND ($1::text IS NULL OR session_id = $1)
          AND superseded_at IS NULL
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
