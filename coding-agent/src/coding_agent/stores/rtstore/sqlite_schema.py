"""SQLite runtime-store DDL."""

from __future__ import annotations

CREATE_SCHEMA_SQL = """
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
        error TEXT,
        superseded_by_checkpoint_id TEXT,
        superseded_at TEXT
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
