"""SQLite fact-source row codecs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from coding_agent.stores.runtime_store import (
    EventRecord,
    SessionFactSourceState,
    _json_object_from_sql,
    _sqlite_optional_int,
    _sqlite_optional_str,
    _sqlite_required_datetime,
    _sqlite_required_int,
    _sqlite_required_str,
    format_u64,
    stored_trusted_handoff,
)


@dataclass(frozen=True)
class _SqliteFactSource:
    state: SessionFactSourceState
    session_seq_int: int
    retention_floor_int: int
    projection_epoch_int: int
    dispatch_generation_int: int
    projection: str


def _fact_source_from_sqlite_row(row: sqlite3.Row) -> _SqliteFactSource:
    session_seq = _sqlite_required_int(row, "session_seq", context="fact source")
    retention_floor = _sqlite_required_int(
        row, "retention_floor", context="fact source"
    )
    projection_epoch = _sqlite_required_int(
        row, "projection_epoch", context="fact source"
    )
    dispatch_generation = _sqlite_required_int(
        row, "dispatch_generation", context="fact source"
    )
    projection = _sqlite_required_str(row, "projection", context="fact source")
    session_id = _sqlite_required_str(row, "session_id", context="fact source")
    raw_handoff_payload = row["trusted_handoff_payload"]
    handoff_payload = (
        None
        if raw_handoff_payload is None
        else _json_object_from_sql(raw_handoff_payload, context="trusted handoff")
    )
    return _SqliteFactSource(
        state=SessionFactSourceState(
            session_id=session_id,
            session_seq=format_u64(session_seq),
            retention_floor=format_u64(retention_floor),
            projection=projection,
            projection_epoch=format_u64(projection_epoch),
            dispatch_generation=format_u64(dispatch_generation),
            trusted_handoff=stored_trusted_handoff(
                session_id=session_id,
                session_seq=_sqlite_optional_int(
                    row, "trusted_handoff_seq", context="fact source"
                ),
                epoch=_sqlite_optional_int(
                    row, "trusted_handoff_epoch", context="fact source"
                ),
                projection=_sqlite_optional_str(
                    row, "trusted_handoff_projection", context="fact source"
                ),
                payload=handoff_payload,
            ),
        ),
        session_seq_int=session_seq,
        retention_floor_int=retention_floor,
        projection_epoch_int=projection_epoch,
        dispatch_generation_int=dispatch_generation,
        projection=projection,
    )


def _event_record_from_sqlite_row(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=_sqlite_required_str(row, "event_id", context="session event"),
        session_id=_sqlite_required_str(row, "session_id", context="session event"),
        event_kind=_sqlite_required_str(row, "event_kind", context="session event"),
        payload=_json_object_from_sql(row["payload"], context="session event"),
        created_at=_sqlite_required_datetime(
            row, "created_at", context="session event"
        ),
        session_seq=format_u64(
            _sqlite_required_int(row, "session_seq", context="session event")
        ),
        projection_epoch=format_u64(
            _sqlite_required_int(row, "projection_epoch", context="session event")
        ),
    )
