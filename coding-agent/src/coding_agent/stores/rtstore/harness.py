"""Harness UoW, cursor, effect, and receipt types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from coding_agent.stores.rtstore.records import (
    AgentRunRecord,
    JSONObject,
)
from coding_agent.stores.rtstore.validate import (
    _require_datetime,
    _require_json_object,
    _require_non_empty,
)

DEFAULT_HARNESS_PROJECTION: Final[str] = "default"


class AuthoritativeWriteRefusedError(RuntimeError):
    """Raised when a derived store is asked to accept a harness unit of work."""


class CursorEpochMismatchError(ValueError):
    """Raised when a delta/settled cursor is bound to the wrong projection or epoch."""


class KeyExpiredError(LookupError):
    """Raised when a cursor sits below the retention floor.

    Callers must either replay from ``retention_floor`` or accept a trusted handoff.
    """

    def __init__(
        self,
        *,
        session_id: str,
        retention_floor: str,
        cursor_seq: str,
    ) -> None:
        _require_non_empty("session_id", session_id)
        parse_u64(retention_floor, field_name="retention_floor")
        parse_u64(cursor_seq, field_name="cursor_seq")
        super().__init__(
            f"key expired for session {session_id}: cursor {cursor_seq} "
            f"is below retention floor {retention_floor}"
        )
        self.session_id = session_id
        self.retention_floor = retention_floor
        self.cursor_seq = cursor_seq


def format_u64(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("u64 must be an integer")
    if value < 0:
        raise ValueError("u64 must be non-negative")
    return str(value)


def parse_u64(value: str, *, field_name: str) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError(f"{field_name} must be a decimal u64 string")
    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{field_name} must be a decimal u64 string")
    return int(value)


def assert_raw_cursor_not_expired(cursor: RawCursor, retention_floor: str) -> None:
    floor = parse_u64(retention_floor, field_name="retention_floor")
    cursor_seq = parse_u64(cursor.session_seq, field_name="session_seq")
    if cursor_seq + 1 < floor:
        raise KeyExpiredError(
            session_id=cursor.session_id,
            retention_floor=retention_floor,
            cursor_seq=cursor.session_seq,
        )


def assert_projection_binding(
    cursor: ProjectionCursor,
    state: SessionFactSourceState,
) -> None:
    if cursor.session_id != state.session_id:
        raise CursorEpochMismatchError(
            f"projection cursor session mismatch: {cursor.session_id}"
        )
    if cursor.projection != state.projection:
        raise CursorEpochMismatchError(
            f"projection cursor bound to projection {cursor.projection}, "
            f"current is {state.projection}"
        )
    if cursor.epoch != state.projection_epoch:
        raise CursorEpochMismatchError(
            f"projection cursor bound to epoch {cursor.epoch}, "
            f"current is {state.projection_epoch}"
        )


def assert_trusted_handoff(
    handoff: TrustedHandoff,
    state: SessionFactSourceState,
) -> None:
    if handoff.session_id != state.session_id:
        raise CursorEpochMismatchError(
            f"trusted handoff session mismatch: {handoff.session_id}"
        )
    if handoff.projection != state.projection:
        raise CursorEpochMismatchError(
            f"trusted handoff bound to projection {handoff.projection}, "
            f"current is {state.projection}"
        )
    if handoff.epoch != state.projection_epoch:
        raise CursorEpochMismatchError(
            f"trusted handoff bound to epoch {handoff.epoch}, "
            f"current is {state.projection_epoch}"
        )
    handoff_seq = parse_u64(handoff.session_seq, field_name="session_seq")
    current_seq = parse_u64(state.session_seq, field_name="session_seq")
    if handoff_seq > current_seq:
        raise CursorEpochMismatchError("trusted handoff is ahead of the physical log")


_EFFECT_STATUS_RANKS: Final[dict[str, int]] = {
    "prepared": 1,
    "dispatched": 2,
    "unknown": 3,
    "failed": 3,
    "completed": 4,
    "settled": 4,
}


def effect_status_rank(status: str) -> int:
    _require_non_empty("status", status)
    return _EFFECT_STATUS_RANKS.get(status, 0)


def effect_status_may_replace(*, current: str, incoming: str) -> bool:
    return effect_status_rank(incoming) >= effect_status_rank(current)


def receipt_generation_may_replace(*, current: str, incoming: str) -> bool:
    return parse_u64(incoming, field_name="generation") > parse_u64(
        current, field_name="generation"
    )


def stored_trusted_handoff(
    *,
    session_id: str,
    session_seq: int | None,
    epoch: int | None,
    projection: str | None,
    payload: JSONObject | None,
) -> TrustedHandoff | None:
    present = (
        session_seq is not None,
        epoch is not None,
        projection is not None,
        payload is not None,
    )
    if not any(present):
        return None
    if not all(present):
        raise TypeError("trusted handoff columns must be stored together")
    if session_seq is None or epoch is None or projection is None or payload is None:
        raise TypeError("trusted handoff columns must be stored together")
    return TrustedHandoff(
        session_id=session_id,
        session_seq=format_u64(session_seq),
        projection=projection,
        epoch=format_u64(epoch),
        payload=payload,
    )


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    session_id: str
    event_kind: str
    payload: JSONObject
    created_at: datetime
    session_seq: str | None = None
    projection_epoch: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("event_id", self.event_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("event_kind", self.event_kind)
        _require_json_object("payload", self.payload)
        _require_datetime("created_at", self.created_at)
        if self.session_seq is not None:
            parse_u64(self.session_seq, field_name="session_seq")
        if self.projection_epoch is not None:
            parse_u64(self.projection_epoch, field_name="projection_epoch")


@dataclass(frozen=True)
class MailboxDispositionSlot:
    slot_id: str
    lane: str
    disposition: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("slot_id", self.slot_id)
        _require_non_empty("lane", self.lane)
        _require_non_empty("disposition", self.disposition)
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class EffectLedgerSlot:
    effect_id: str
    status: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("effect_id", self.effect_id)
        _require_non_empty("status", self.status)
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class OperationReceiptSlot:
    receipt_id: str
    generation: str
    payload: JSONObject = field(default_factory=dict)
    compensation_effect_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("receipt_id", self.receipt_id)
        parse_u64(self.generation, field_name="generation")
        _require_json_object("payload", self.payload)
        if self.compensation_effect_id is not None:
            _require_non_empty("compensation_effect_id", self.compensation_effect_id)


@dataclass(frozen=True)
class AuthoritativeUnitOfWork:
    event: EventRecord
    session_state: JSONObject
    mailbox: MailboxDispositionSlot | None = None
    effect: EffectLedgerSlot | None = None
    receipt: OperationReceiptSlot | None = None
    run_state: AgentRunRecord | None = None
    require_settled_parent_run_id: str | None = None
    require_unsettled_root_run_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event, EventRecord):
            raise TypeError("event must be an EventRecord")
        _require_json_object("session_state", self.session_state)
        if self.mailbox is not None and not isinstance(
            self.mailbox, MailboxDispositionSlot
        ):
            raise TypeError("mailbox must be a MailboxDispositionSlot")
        if self.effect is not None and not isinstance(self.effect, EffectLedgerSlot):
            raise TypeError("effect must be an EffectLedgerSlot")
        if self.receipt is not None and not isinstance(
            self.receipt, OperationReceiptSlot
        ):
            raise TypeError("receipt must be an OperationReceiptSlot")
        if self.run_state is not None and not isinstance(
            self.run_state, AgentRunRecord
        ):
            raise TypeError("run_state must be an AgentRunRecord")
        if self.require_settled_parent_run_id is not None:
            _require_non_empty(
                "require_settled_parent_run_id",
                self.require_settled_parent_run_id,
            )
        if self.require_unsettled_root_run_id is not None:
            _require_non_empty(
                "require_unsettled_root_run_id",
                self.require_unsettled_root_run_id,
            )
            if (
                self.run_state is not None
                and self.run_state.run_id != self.require_unsettled_root_run_id
            ):
                raise ValueError(
                    "root settlement run_state does not match required run"
                )
        if self.event.session_seq is not None:
            raise ValueError("event.session_seq is allocated by the store")
        if self.event.projection_epoch is not None:
            raise ValueError("event.projection_epoch is allocated by the store")


@dataclass(frozen=True)
class RawCursor:
    session_id: str
    session_seq: str

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")


@dataclass(frozen=True)
class ProjectionCursor:
    kind: str
    session_id: str
    projection: str
    epoch: str
    session_seq: str

    def __post_init__(self) -> None:
        if self.kind not in {"delta", "settled"}:
            raise ValueError("kind must be 'delta' or 'settled'")
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("projection", self.projection)
        parse_u64(self.epoch, field_name="epoch")
        parse_u64(self.session_seq, field_name="session_seq")


@dataclass(frozen=True)
class TrustedHandoff:
    session_id: str
    session_seq: str
    projection: str
    epoch: str
    payload: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")
        _require_non_empty("projection", self.projection)
        parse_u64(self.epoch, field_name="epoch")
        _require_json_object("payload", self.payload)


@dataclass(frozen=True)
class SessionFactSourceState:
    session_id: str
    session_seq: str
    retention_floor: str
    projection: str
    projection_epoch: str
    trusted_handoff: TrustedHandoff | None = None

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        parse_u64(self.session_seq, field_name="session_seq")
        parse_u64(self.retention_floor, field_name="retention_floor")
        _require_non_empty("projection", self.projection)
        parse_u64(self.projection_epoch, field_name="projection_epoch")
        if self.trusted_handoff is not None:
            if not isinstance(self.trusted_handoff, TrustedHandoff):
                raise TypeError("trusted_handoff must be a TrustedHandoff")
            if self.trusted_handoff.session_id != self.session_id:
                raise ValueError("trusted_handoff.session_id must match session_id")


@dataclass(frozen=True)
class RetentionFloorReplay:
    events: list[EventRecord]
    raw_cursor: RawCursor
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.events, list):
            raise TypeError("events must be a list")
        for event in self.events:
            if not isinstance(event, EventRecord):
                raise TypeError("events must contain EventRecord values")
        if not isinstance(self.raw_cursor, RawCursor):
            raise TypeError("raw_cursor must be a RawCursor")
        if not isinstance(self.complete, bool):
            raise TypeError("complete must be a bool")

    @classmethod
    def from_page(
        cls,
        *,
        session_id: str,
        events: list[EventRecord],
        limit: int,
        retention_floor: int,
        head_session_seq: str,
    ) -> RetentionFloorReplay:
        if events:
            last_seq = events[-1].session_seq
            if last_seq is None:
                raise ValueError("replayed event must include session_seq")
            return cls(
                events=events,
                raw_cursor=RawCursor(session_id=session_id, session_seq=last_seq),
                complete=len(events) < limit or last_seq == head_session_seq,
            )
        return cls(
            events=events,
            raw_cursor=RawCursor(
                session_id=session_id,
                session_seq=format_u64(max(retention_floor, 1) - 1),
            ),
            complete=True,
        )


@dataclass(frozen=True)
class AuthoritativeCommit:
    event: EventRecord
    projection: str
    projection_epoch: str
    raw_cursor: RawCursor
    idempotent: bool = False

    def __post_init__(self) -> None:
        if self.event.session_seq is None:
            raise ValueError("committed event must include session_seq")
        if self.event.projection_epoch is None:
            raise ValueError("committed event must include projection_epoch")
        _require_non_empty("projection", self.projection)
        parse_u64(self.projection_epoch, field_name="projection_epoch")
        if self.raw_cursor.session_id != self.event.session_id:
            raise ValueError("raw cursor session_id must match the event")
        if self.raw_cursor.session_seq != self.event.session_seq:
            raise ValueError("raw cursor must land on the committed session_seq")
        if self.event.projection_epoch != self.projection_epoch:
            raise ValueError(
                "committed event.projection_epoch must match commit.projection_epoch"
            )
