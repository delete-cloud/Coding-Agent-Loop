"""PostgreSQL fact-source row codecs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from coding_agent.stores.runtime_store import (
    EffectLedgerSlot,
    EventRecord,
    JSONObject,
    MailboxDispositionSlot,
    OperationReceiptSlot,
    SessionFactSourceState,
    format_u64,
    stored_trusted_handoff,
)
from coding_agent.stores.pg_durable.helpers import (
    _optional_dict,
    _optional_int,
    _optional_str,
    _required_datetime,
    _required_dict,
    _required_int,
    _required_str,
)


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
