"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

import json
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    EventRecord,
    RawCursor,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _json_to_sql,
    effect_status_may_replace,
    format_u64,
    parse_u64,
    receipt_generation_may_replace,
)
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.local_durable.fact_source_rows import (
    _event_record_from_sqlite_row,
)
from coding_agent.stores.local_durable.helpers import _require_json_object


class LocalUnitOfWorkMixin:
    async def commit_authoritative_uow(
        self,
        authority: OwnerAuthority,
        unit: AuthoritativeUnitOfWork,
    ) -> AuthoritativeCommit:
        if unit.event.session_id != authority.session_id:
            raise SessionOwnershipConflictError("event belongs to another session")
        _require_json_object("session payload", unit.session_state)
        if unit.session_state.get("id") != authority.session_id:
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        payload_session_id = unit.session_state.get("session_id")
        if (
            payload_session_id is not None
            and payload_session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError(
                "session payload belongs to another owner"
            )
        tape_id = unit.session_state.get("tape_id")
        if tape_id is not None and not isinstance(tape_id, str):
            raise TypeError("session payload tape_id must be a string")
        if (
            unit.run_state is not None
            and unit.run_state.session_id != authority.session_id
        ):
            raise SessionOwnershipConflictError("agent run belongs to another session")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_authority(connection, authority)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            if unit.run_state is not None:
                if unit.run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                self._assert_tape_belongs_to_session(
                    connection,
                    tape_id=unit.run_state.tape_id,
                    session_id=authority.session_id,
                )
            fact = self._ensure_fact_source(connection, authority.session_id)
            existing_row = connection.execute(
                """
                SELECT * FROM session_event_records
                WHERE event_id = ?
                """,
                (unit.event.event_id,),
            ).fetchone()
            idempotent = False
            if existing_row is not None:
                existing_event = _event_record_from_sqlite_row(existing_row)
                if existing_event.session_id != authority.session_id:
                    raise SessionOwnershipConflictError(
                        "event belongs to another session"
                    )
                if existing_event.session_seq is None:
                    raise ValueError("existing event must include session_seq")
                if existing_event.projection_epoch is None:
                    raise ValueError("existing event must include projection_epoch")
                next_seq = parse_u64(
                    existing_event.session_seq, field_name="session_seq"
                )
                existing_epoch = parse_u64(
                    existing_event.projection_epoch, field_name="projection_epoch"
                )
                if existing_epoch != fact.projection_epoch_int:
                    connection.execute(
                        """
                        UPDATE session_event_records
                        SET projection_epoch = ?
                        WHERE event_id = ?
                        """,
                        (fact.projection_epoch_int, unit.event.event_id),
                    )
                    promoted_row = connection.execute(
                        """
                        SELECT * FROM session_event_records
                        WHERE event_id = ?
                        """,
                        (unit.event.event_id,),
                    ).fetchone()
                    if promoted_row is None:
                        raise RuntimeError("failed to promote event projection_epoch")
                    event = _event_record_from_sqlite_row(promoted_row)
                else:
                    event = existing_event
                idempotent = True
            else:
                next_seq = fact.session_seq_int + 1
                connection.execute(
                    """
                    UPDATE session_fact_source
                    SET session_seq = ?
                    WHERE session_id = ?
                    """,
                    (next_seq, authority.session_id),
                )
                connection.execute(
                    """
                    INSERT INTO session_event_records (
                        session_id, session_seq, event_id, event_kind, payload,
                        created_at, projection_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authority.session_id,
                        next_seq,
                        unit.event.event_id,
                        unit.event.event_kind,
                        _json_to_sql(unit.event.payload),
                        _datetime_to_json(unit.event.created_at),
                        fact.projection_epoch_int,
                    ),
                )
                event = EventRecord(
                    event_id=unit.event.event_id,
                    session_id=authority.session_id,
                    event_kind=unit.event.event_kind,
                    payload=unit.event.payload,
                    created_at=unit.event.created_at,
                    session_seq=format_u64(next_seq),
                    projection_epoch=format_u64(fact.projection_epoch_int),
                )
            connection.execute(
                """
                INSERT INTO agent_http_sessions (session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id)
                DO UPDATE SET payload = excluded.payload,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (authority.session_id, json.dumps(unit.session_state, sort_keys=True)),
            )
            if unit.run_state is not None:
                existing = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE run_id = ?",
                    (unit.run_state.run_id,),
                ).fetchone()
                if (
                    existing is not None
                    and existing["session_id"] != authority.session_id
                ):
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
                    _agent_run_sqlite_values(unit.run_state),
                )
            if unit.mailbox is not None:
                connection.execute(
                    """
                    INSERT INTO session_mailbox_slots (
                        session_id, slot_id, lane, disposition, payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, slot_id)
                    DO UPDATE SET
                        lane = excluded.lane,
                        disposition = excluded.disposition,
                        payload = excluded.payload
                    """,
                    (
                        authority.session_id,
                        unit.mailbox.slot_id,
                        unit.mailbox.lane,
                        unit.mailbox.disposition,
                        _json_to_sql(unit.mailbox.payload),
                    ),
                )
            if unit.effect is not None:
                existing_effect = connection.execute(
                    """
                    SELECT status FROM session_effect_slots
                    WHERE session_id = ? AND effect_id = ?
                    """,
                    (authority.session_id, unit.effect.effect_id),
                ).fetchone()
                if existing_effect is None or effect_status_may_replace(
                    current=existing_effect["status"],
                    incoming=unit.effect.status,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_effect_slots (
                            session_id, effect_id, status, payload
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id, effect_id)
                        DO UPDATE SET
                            status = excluded.status,
                            payload = excluded.payload
                        """,
                        (
                            authority.session_id,
                            unit.effect.effect_id,
                            unit.effect.status,
                            _json_to_sql(unit.effect.payload),
                        ),
                    )
            if unit.receipt is not None:
                existing_receipt = connection.execute(
                    """
                    SELECT generation FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt is None or receipt_generation_may_replace(
                    current=existing_receipt["generation"],
                    incoming=unit.receipt.generation,
                ):
                    connection.execute(
                        """
                        INSERT INTO session_receipt_slots (
                            session_id, receipt_id, generation, payload,
                            compensation_effect_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, receipt_id)
                        DO UPDATE SET
                            generation = excluded.generation,
                            payload = excluded.payload,
                            compensation_effect_id = excluded.compensation_effect_id
                        """,
                        (
                            authority.session_id,
                            unit.receipt.receipt_id,
                            unit.receipt.generation,
                            _json_to_sql(unit.receipt.payload),
                            unit.receipt.compensation_effect_id,
                        ),
                    )
        return AuthoritativeCommit(
            event=event,
            projection=fact.projection,
            projection_epoch=format_u64(fact.projection_epoch_int),
            raw_cursor=RawCursor(
                session_id=authority.session_id,
                session_seq=format_u64(next_seq),
            ),
            idempotent=idempotent,
        )
