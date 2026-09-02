"""Harness fact-source, mailbox, effect, and receipt SQL."""

from __future__ import annotations


class PgHarnessSqlMixin:
    _CREATE_HARNESS_FACT_SOURCE_SQL = """
    CREATE TABLE IF NOT EXISTS session_fact_source (
        session_id TEXT PRIMARY KEY,
        session_seq BIGINT NOT NULL,
        retention_floor BIGINT NOT NULL,
        projection TEXT NOT NULL,
        projection_epoch BIGINT NOT NULL,
        dispatch_generation BIGINT NOT NULL DEFAULT 0,
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
        admitted_session_seq BIGINT,
        admitted_dispatch_generation BIGINT,
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
    CREATE TABLE IF NOT EXISTS session_effect_reconciliation_evidence (
        session_id TEXT NOT NULL,
        evidence_ref TEXT NOT NULL,
        effect_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        authorization_transition_id TEXT NOT NULL,
        reconciliation_owner_epoch BIGINT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, evidence_ref),
        UNIQUE (
            session_id, effect_id, attempt_id, authorization_transition_id
        )
    );
    CREATE TABLE IF NOT EXISTS session_executor_attempts (
        session_id TEXT NOT NULL,
        effect_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        authorization_transition_id TEXT NOT NULL,
        dispatch_owner_epoch BIGINT NOT NULL,
        status TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (
            session_id, effect_id, attempt_id, authorization_transition_id
        )
    );
    CREATE TABLE IF NOT EXISTS session_child_bindings (
        session_id TEXT NOT NULL,
        parent_effect_id TEXT NOT NULL,
        child_run_id TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, parent_effect_id),
        UNIQUE (session_id, child_run_id)
    );
    CREATE TABLE IF NOT EXISTS session_recovery_leases (
        session_id TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        child_run_id TEXT NOT NULL,
        status TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, lease_id)
    );
    CREATE TABLE IF NOT EXISTS session_receipt_slots (
        session_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        generation TEXT NOT NULL,
        payload JSONB NOT NULL,
        compensation_effect_id TEXT,
        PRIMARY KEY (session_id, receipt_id)
    );
    CREATE TABLE IF NOT EXISTS session_projector_cursors (
        session_id TEXT PRIMARY KEY,
        last_session_seq BIGINT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS session_projector_sinks (
        session_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        sink TEXT NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (session_id, event_id, sink)
    );
    CREATE TABLE IF NOT EXISTS session_operation_states (
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        revision BIGINT NOT NULL,
        projection_epoch BIGINT NOT NULL,
        transition_id TEXT NOT NULL,
        fact_seq_start BIGINT,
        fact_seq_end BIGINT,
        value JSONB NOT NULL,
        PRIMARY KEY (session_id, run_id),
        CHECK (
            (fact_seq_start IS NULL AND fact_seq_end IS NULL)
            OR (
                fact_seq_start IS NOT NULL
                AND fact_seq_end IS NOT NULL
                AND fact_seq_start <= fact_seq_end
            )
        )
    );
    CREATE TABLE IF NOT EXISTS session_transition_receipts (
        session_id TEXT NOT NULL,
        projection_epoch BIGINT NOT NULL,
        transition_id TEXT NOT NULL,
        mutation_fingerprint TEXT NOT NULL,
        result JSONB NOT NULL,
        PRIMARY KEY (session_id, projection_epoch, transition_id)
    );
    CREATE TABLE IF NOT EXISTS runtime_activation (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        new_sessions_enabled BOOLEAN NOT NULL DEFAULT FALSE
    );
    INSERT INTO runtime_activation (singleton, new_sessions_enabled)
    VALUES (1, FALSE)
    ON CONFLICT (singleton) DO NOTHING;
    """

    _CREATE_RUNTIME_ACTIVATION_SQL = """
    CREATE TABLE IF NOT EXISTS runtime_activation (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        new_sessions_enabled BOOLEAN NOT NULL DEFAULT FALSE
    );
    INSERT INTO runtime_activation (singleton, new_sessions_enabled)
    VALUES (1, FALSE)
    ON CONFLICT (singleton) DO NOTHING;
    """

    _MIGRATE_HARNESS_FACT_SOURCE_SQL = """
    ALTER TABLE session_fact_source
        ADD COLUMN IF NOT EXISTS trusted_handoff_seq BIGINT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_epoch BIGINT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_projection TEXT,
        ADD COLUMN IF NOT EXISTS trusted_handoff_payload JSONB,
        ADD COLUMN IF NOT EXISTS trusted_handoff_accepted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS dispatch_generation BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE session_mailbox_slots
        ADD COLUMN IF NOT EXISTS admitted_session_seq BIGINT,
        ADD COLUMN IF NOT EXISTS admitted_dispatch_generation BIGINT
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

    _UPDATE_FACT_SOURCE_COMMAND_ADMISSION_SQL = """
    UPDATE session_fact_source
    SET session_seq = $2, dispatch_generation = $3
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

    _SELECT_SESSION_EVENT_BY_ID_SQL = """
    SELECT * FROM session_event_records
    WHERE event_id = $1
    """

    _PROMOTE_SESSION_EVENT_EPOCH_SQL = """
    UPDATE session_event_records
    SET projection_epoch = $2
    WHERE event_id = $1
    RETURNING *
    """

    _DELETE_TURN_MAILBOX_SLOTS_SQL = """
    DELETE FROM session_mailbox_slots
    WHERE session_id = $1
      AND slot_id LIKE 'turn:%'
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

    _SELECT_MAILBOX_SLOT_FOR_UPDATE_SQL = """
    SELECT * FROM session_mailbox_slots
    WHERE session_id = $1 AND slot_id = $2
    FOR UPDATE
    """

    _INSERT_RUNTIME_COMMAND_SQL = """
    INSERT INTO session_mailbox_slots (
        session_id,
        slot_id,
        lane,
        disposition,
        admitted_session_seq,
        admitted_dispatch_generation,
        payload
    )
    VALUES ($1, $2, 'runtime', 'pending', $3, $4, $5::jsonb)
    RETURNING *
    """

    _SELECT_RUNTIME_COMMAND_MAILBOX_SQL = """
    SELECT
        source.dispatch_generation,
        mailbox.slot_id,
        mailbox.disposition,
        mailbox.admitted_session_seq,
        mailbox.admitted_dispatch_generation,
        mailbox.payload
    FROM session_fact_source AS source
    LEFT JOIN session_mailbox_slots AS mailbox
        ON mailbox.session_id = source.session_id
        AND mailbox.admitted_session_seq IS NOT NULL
        AND mailbox.disposition IN ('pending', 'admitted')
    WHERE source.session_id = $1
    ORDER BY mailbox.admitted_session_seq
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

    _SELECT_EFFECT_SLOT_FOR_UPDATE_SQL = """
    SELECT * FROM session_effect_slots
    WHERE session_id = $1 AND effect_id = $2
    FOR UPDATE
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

    _SELECT_OPERATION_STATE_SQL = """
    SELECT * FROM session_operation_states
    WHERE session_id = $1 AND run_id = $2
    """

    _SELECT_OPERATION_STATE_FOR_UPDATE_SQL = """
    SELECT * FROM session_operation_states
    WHERE session_id = $1 AND run_id = $2
    FOR UPDATE
    """

    _UPSERT_OPERATION_STATE_SQL = """
    INSERT INTO session_operation_states (
        session_id, run_id, revision, projection_epoch, transition_id,
        fact_seq_start, fact_seq_end, value
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
    ON CONFLICT (session_id, run_id)
    DO UPDATE SET
        revision = EXCLUDED.revision,
        projection_epoch = EXCLUDED.projection_epoch,
        transition_id = EXCLUDED.transition_id,
        fact_seq_start = EXCLUDED.fact_seq_start,
        fact_seq_end = EXCLUDED.fact_seq_end,
        value = EXCLUDED.value
    RETURNING *
    """

    _SELECT_TRANSITION_RECEIPT_SQL = """
    SELECT * FROM session_transition_receipts
    WHERE session_id = $1 AND projection_epoch = $2 AND transition_id = $3
    """

    _INSERT_TRANSITION_RECEIPT_SQL = """
    INSERT INTO session_transition_receipts (
        session_id, projection_epoch, transition_id, mutation_fingerprint, result
    )
    VALUES ($1, $2, $3, $4, $5::jsonb)
    RETURNING *
    """

    _SELECT_RECONCILIATION_EVIDENCE_SQL = """
    SELECT * FROM session_effect_reconciliation_evidence
    WHERE session_id = $1 AND evidence_ref = $2
    FOR UPDATE
    """

    _SELECT_RECONCILIATION_EVIDENCE_IDENTITY_SQL = """
    SELECT * FROM session_effect_reconciliation_evidence
    WHERE session_id = $1 AND effect_id = $2 AND attempt_id = $3
      AND authorization_transition_id = $4
    FOR UPDATE
    """

    _INSERT_RECONCILIATION_EVIDENCE_SQL = """
    INSERT INTO session_effect_reconciliation_evidence (
        session_id, evidence_ref, effect_id, attempt_id,
        authorization_transition_id, reconciliation_owner_epoch, payload
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
    RETURNING *
    """

    _SELECT_EXECUTOR_ATTEMPT_SQL = """
    SELECT * FROM session_executor_attempts
    WHERE session_id = $1 AND effect_id = $2 AND attempt_id = $3
      AND authorization_transition_id = $4
    FOR UPDATE
    """

    _INSERT_EXECUTOR_ATTEMPT_SQL = """
    INSERT INTO session_executor_attempts (
        session_id, effect_id, attempt_id, authorization_transition_id,
        dispatch_owner_epoch, status, payload
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
    RETURNING *
    """

    _UPDATE_EXECUTOR_ATTEMPT_SQL = """
    UPDATE session_executor_attempts
    SET status = $5, payload = $6::jsonb
    WHERE session_id = $1 AND effect_id = $2 AND attempt_id = $3
      AND authorization_transition_id = $4
    RETURNING *
    """
