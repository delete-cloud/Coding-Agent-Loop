"""Session, tape-binding, and owner-lock SQL."""

from __future__ import annotations


class PgSessionSqlMixin:
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
