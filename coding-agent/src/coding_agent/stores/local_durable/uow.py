"""Authoritative harness unit-of-work commit."""

from __future__ import annotations

import json
from dataclasses import replace

from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    RootRunAlreadySettledError,
    TurnInProgressError,
    build_chat_admission,
    build_root_settlement,
)
from coding_agent.stores.runtime_store import (
    AuthoritativeCommit,
    AuthoritativeUnitOfWork,
    EventRecord,
    RawCursor,
    _agent_run_sqlite_values,
    _datetime_to_json,
    _json_object_from_sql,
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
    async def settle_root_run(
        self,
        authority: OwnerAuthority,
        *,
        run_id: str,
        outcome: str,
        result: str | None,
        error: str | None,
    ) -> AuthoritativeCommit:
        from coding_agent.stores.runtime_store import SQLiteRuntimeStore

        run = await SQLiteRuntimeStore(self._path).load_agent_run(run_id)
        if run is None or run.session_id != authority.session_id:
            raise KeyError(f"root run not found: {run_id}")
        session = self.load_session(authority.session_id)
        if session is None:
            raise KeyError(f"session not found: {authority.session_id}")
        unit = build_root_settlement(
            run=run,
            session_state=session,
            outcome=outcome,
            result=result,
            error=error,
        )
        return await self.commit_authoritative_uow(authority, unit)

    async def admit_chat_command(
        self,
        authority: OwnerAuthority,
        *,
        prompt: str,
        command_id: str,
        parent_run_id: str | None,
        session_state: dict[str, object],
    ) -> ChatCommandAdmission:
        unit, admission = build_chat_admission(
            session_id=authority.session_id,
            prompt=prompt,
            command_id=command_id,
            parent_run_id=parent_run_id,
            session_state=session_state,
        )
        commit = await self.commit_authoritative_uow(authority, unit)
        return replace(
            admission,
            session_seq=commit.event.session_seq,
            idempotent=commit.idempotent,
        )

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
            if unit.require_unsettled_root_run_id is not None:
                current_run = connection.execute(
                    "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                    (unit.require_unsettled_root_run_id, authority.session_id),
                ).fetchone()
                if current_run is None:
                    raise KeyError(
                        f"root run not found: {unit.require_unsettled_root_run_id}"
                    )
                if current_run["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    if unit.event.event_kind != "root_terminal":
                        raise RootRunAlreadySettledError(
                            "chat event after root settlement"
                        )
                    existing_terminal = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_terminal is None:
                        raise ValueError("settled root run is missing root_terminal")
                    event = _event_record_from_sqlite_row(existing_terminal)
                    if event.payload != unit.event.payload:
                        raise RootRunAlreadySettledError(
                            "root run settled with a different outcome"
                        )
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored root_terminal is incomplete")
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )
            chat_receipt = (
                unit.receipt is not None
                and unit.receipt.receipt_id.startswith("chat-command:")
            )
            if chat_receipt:
                existing_receipt_row = connection.execute(
                    """
                    SELECT generation, payload, compensation_effect_id
                    FROM session_receipt_slots
                    WHERE session_id = ? AND receipt_id = ?
                    """,
                    (authority.session_id, unit.receipt.receipt_id),
                ).fetchone()
                if existing_receipt_row is not None:
                    if (
                        existing_receipt_row["generation"] != unit.receipt.generation
                        or _json_object_from_sql(
                            existing_receipt_row["payload"], context="receipt slot"
                        )
                        != unit.receipt.payload
                        or existing_receipt_row["compensation_effect_id"]
                        != unit.receipt.compensation_effect_id
                    ):
                        raise ChatCommandConflictError(
                            "command ID was reused with different input"
                        )
                    existing_event_row = connection.execute(
                        "SELECT * FROM session_event_records WHERE event_id = ?",
                        (unit.event.event_id,),
                    ).fetchone()
                    if existing_event_row is None:
                        raise ValueError(
                            "chat receipt references a missing admission event"
                        )
                    event = _event_record_from_sqlite_row(existing_event_row)
                    if event.session_id != authority.session_id:
                        raise SessionOwnershipConflictError(
                            "chat receipt event belongs to another session"
                        )
                    if event.session_seq is None or event.projection_epoch is None:
                        raise ValueError("stored admission event is incomplete")
                    fact = self._ensure_fact_source(connection, authority.session_id)
                    return AuthoritativeCommit(
                        event=event,
                        projection=fact.projection,
                        projection_epoch=event.projection_epoch,
                        raw_cursor=RawCursor(
                            session_id=authority.session_id,
                            session_seq=event.session_seq,
                        ),
                        idempotent=True,
                    )

                session_row = connection.execute(
                    "SELECT payload FROM agent_http_sessions WHERE session_id = ?",
                    (authority.session_id,),
                ).fetchone()
                if session_row is not None:
                    durable_session = _json_object_from_sql(
                        session_row["payload"], context="session payload"
                    )
                    active_run_id = durable_session.get("turn_id")
                    if active_run_id is not None:
                        if not isinstance(active_run_id, str):
                            raise ValueError("durable turn_id must be a string")
                        active_run = connection.execute(
                            "SELECT status FROM agent_runs WHERE run_id = ? AND session_id = ?",
                            (active_run_id, authority.session_id),
                        ).fetchone()
                        if active_run is None or active_run["status"] in {
                            "queued",
                            "requested",
                            "claimed",
                            "running",
                            "cancelling",
                        }:
                            raise TurnInProgressError("a root turn is already active")

            run_state = unit.run_state
            if unit.require_settled_parent_run_id is not None:
                parent = connection.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = ? AND superseded_by_checkpoint_id IS NULL
                    ORDER BY started_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (authority.session_id,),
                ).fetchone()
                if (
                    parent is None
                    or parent["run_id"] != unit.require_settled_parent_run_id
                    or parent["status"]
                    not in {"completed", "failed", "cancelled", "interrupted"}
                ):
                    raise ResumeSourceUnsettledError(
                        "resume source is not the latest active settled run"
                    )
                if run_state is None:
                    raise ValueError("resume admission requires a run")
                metadata = dict(run_state.metadata)
                metadata.update(
                    {
                        "previous_run_id": parent["run_id"],
                        "resume_from_run_id": parent["run_id"],
                        "resume_reason": "user_resume",
                        "resume_context_injected": True,
                    }
                )
                parent_metadata = _json_object_from_sql(
                    parent["metadata"], context="run metadata"
                )
                previous_event_id = parent_metadata.get("last_event_id")
                if previous_event_id is not None:
                    if not isinstance(previous_event_id, str) or not previous_event_id:
                        raise ValueError("last_event_id must be a non-empty string")
                    metadata["resume_from_event_id"] = previous_event_id
                run_state = replace(run_state, metadata=metadata)
            if tape_id:
                self._bind_tape(connection, authority.session_id, tape_id)
            if run_state is not None:
                if run_state.tape_id is None:
                    raise SessionOwnershipConflictError(
                        "run target is not bound to a tape"
                    )
                self._assert_tape_belongs_to_session(
                    connection,
                    tape_id=run_state.tape_id,
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
            if run_state is not None:
                existing = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE run_id = ?",
                    (run_state.run_id,),
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
                    _agent_run_sqlite_values(run_state),
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
