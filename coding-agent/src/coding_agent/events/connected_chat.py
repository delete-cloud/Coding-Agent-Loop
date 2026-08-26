"""Connected-chat event contracts and opaque cursor codec."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final, Literal, TypeAlias

from coding_agent.stores.rtstore.harness import (
    SessionFactSourceState,
    parse_u64,
)
from coding_agent.stores.rtstore.json_types import JSONObject
from coding_agent.stores.rtstore.records import AgentRunRecord
from coding_agent.stores.rtstore.validate import (
    _require_datetime,
    _require_json_object,
    _require_non_empty,
)

CONNECTED_CHAT_CONTRACT_VERSION: Final[str] = "1.0.0"
CONNECTED_CHAT_PROJECTION: Final[str] = "connected-chat"
CHAT_EVENT_KINDS: Final[tuple[str, ...]] = (
    "user_prompt",
    "assistant_message",
    "thinking",
    "progress",
    "tool_call",
    "tool_result",
    "root_terminal",
)
ChatEventKind: TypeAlias = Literal[
    "user_prompt",
    "assistant_message",
    "thinking",
    "progress",
    "tool_call",
    "tool_result",
    "root_terminal",
]

_CURSOR_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "v",
        "kind",
        "session_id",
        "projection",
        "epoch",
        "after_seq",
        "high_water_seq",
    }
)


class ChatCommandConflictError(ValueError):
    """Raised when a command ID is reused with different input."""


class ResumeSourceUnsettledError(ValueError):
    """Raised when Resume does not target the latest active settled run."""


class TurnInProgressError(ValueError):
    """Raised when a distinct root command targets an active turn."""


class RootRunAlreadySettledError(ValueError):
    """Raised when a competing outcome targets an already-settled root run."""


@dataclass(frozen=True)
class ChatCommandAdmission:
    session_id: str
    run_id: str
    command_id: str
    parent_run_id: str | None
    session_seq: str | None = None
    idempotent: bool = False

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("command_id", self.command_id)
        if self.parent_run_id is not None:
            _require_non_empty("parent_run_id", self.parent_run_id)
        if self.session_seq is not None:
            parse_u64(self.session_seq, field_name="session_seq")


class ChatProjectionCorruptionError(ValueError):
    """Raised when authoritative chat facts violate run ownership."""


class ChatCursorError(ValueError):
    """Checked cursor failure for later HTTP boundary mapping."""

    def __init__(
        self,
        code: str,
        *,
        status: int,
        replay_required: bool,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.replay_required = replay_required


@dataclass(frozen=True)
class ConnectedChatCursor:
    v: int
    kind: str
    session_id: str
    projection: str
    epoch: str
    after_seq: str
    high_water_seq: str

    def __post_init__(self) -> None:
        if isinstance(self.v, bool) or self.v != 1:
            raise ValueError("v must be 1")
        if self.kind != "chat":
            raise ValueError("kind must be 'chat'")
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("projection", self.projection)
        parse_u64(self.epoch, field_name="epoch")
        after = parse_u64(self.after_seq, field_name="after_seq")
        high_water = parse_u64(self.high_water_seq, field_name="high_water_seq")
        if after > high_water:
            raise ValueError("after_seq cannot exceed high_water_seq")


@dataclass(frozen=True)
class ChatEvent:
    source_event_id: str
    session_seq: str
    session_id: str
    run_id: str | None
    kind: ChatEventKind
    created_at: datetime
    payload: JSONObject
    contract_version: str = CONNECTED_CHAT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONNECTED_CHAT_CONTRACT_VERSION:
            raise ValueError("unsupported connected-chat contract version")
        _require_non_empty("source_event_id", self.source_event_id)
        parse_u64(self.session_seq, field_name="session_seq")
        _require_non_empty("session_id", self.session_id)
        if self.run_id is not None:
            _require_non_empty("run_id", self.run_id)
        if self.kind not in CHAT_EVENT_KINDS:
            raise ValueError(f"unknown connected-chat event kind: {self.kind}")
        _require_datetime("created_at", self.created_at)
        _require_json_object("payload", self.payload)
        _validate_chat_payload(self.kind, self.payload)


@dataclass(frozen=True)
class ChatSnapshot:
    session_id: str
    projection: str
    projection_epoch: str
    snapshot_cursor: str
    next_cursor: str | None
    events: tuple[ChatEvent, ...]
    contract_version: str = CONNECTED_CHAT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONNECTED_CHAT_CONTRACT_VERSION:
            raise ValueError("unsupported connected-chat contract version")
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("projection", self.projection)
        parse_u64(self.projection_epoch, field_name="projection_epoch")
        _require_non_empty("snapshot_cursor", self.snapshot_cursor)
        if self.next_cursor is not None:
            _require_non_empty("next_cursor", self.next_cursor)
        if not isinstance(self.events, tuple) or not all(
            isinstance(event, ChatEvent) for event in self.events
        ):
            raise TypeError("events must be a tuple of ChatEvent values")


def build_chat_admission(
    *,
    session_id: str,
    prompt: str,
    command_id: str,
    parent_run_id: str | None,
    session_state: JSONObject,
) -> tuple[object, ChatCommandAdmission]:
    from coding_agent.stores.rtstore.harness import (
        AuthoritativeUnitOfWork,
        EventRecord,
        OperationReceiptSlot,
    )

    _require_non_empty("session_id", session_id)
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("prompt must be a non-empty string")
    _require_non_empty("command_id", command_id)
    _require_json_object("session_state", session_state)
    tape_id = session_state.get("tape_id")
    if not isinstance(tape_id, str) or not tape_id:
        raise ValueError("session_state must include tape_id")
    run_identity = json.dumps(
        {"command_id": command_id, "session_id": session_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"cal:{run_identity}").hex
    admission = ChatCommandAdmission(
        session_id=session_id,
        run_id=run_id,
        command_id=command_id,
        parent_run_id=parent_run_id,
    )
    admitted_session_state = dict(session_state)
    admitted_session_state["turn_id"] = run_id
    admitted_session_state["turn_in_progress"] = True
    receipt_payload: JSONObject = {
        "command_id": command_id,
        "parent_run_id": parent_run_id,
        "prompt": prompt,
        "run_id": run_id,
    }
    now = datetime.now(UTC)
    unit = AuthoritativeUnitOfWork(
        event=EventRecord(
            event_id=f"{session_id}:chat-command:{command_id}",
            session_id=session_id,
            event_kind="user_prompt",
            payload={"run_id": run_id, "text": prompt},
            created_at=now,
        ),
        session_state=admitted_session_state,
        receipt=OperationReceiptSlot(
            receipt_id=f"chat-command:{command_id}",
            generation="0",
            payload=receipt_payload,
        ),
        run_state=AgentRunRecord(
            run_id=run_id,
            session_id=session_id,
            tape_id=tape_id,
            parent_run_id=parent_run_id,
            agent_id=None,
            status="requested",
            started_at=now,
            metadata={"command_id": command_id},
            result={},
        ),
        require_settled_parent_run_id=parent_run_id,
    )
    return unit, admission


def build_root_settlement(
    *,
    run: AgentRunRecord,
    session_state: JSONObject,
    outcome: Literal["completed", "failed", "cancelled", "interrupted"],
    result: str | None,
    error: str | None,
) -> object:
    from dataclasses import replace

    from coding_agent.stores.rtstore.harness import AuthoritativeUnitOfWork, EventRecord

    if outcome not in {"completed", "failed", "cancelled", "interrupted"}:
        raise ValueError("invalid root settlement outcome")
    now = datetime.now(UTC)
    settled_session = dict(session_state)
    settled_session["turn_id"] = run.run_id
    settled_session["turn_in_progress"] = False
    settled_session["turn_status"] = outcome
    settled_run = replace(
        run,
        status=outcome,
        ended_at=now,
        result={} if result is None else {"text": result},
        error=error,
    )
    return AuthoritativeUnitOfWork(
        event=EventRecord(
            event_id=f"{run.run_id}:root_terminal",
            session_id=run.session_id,
            event_kind="root_terminal",
            payload={
                "run_id": run.run_id,
                "outcome": outcome,
                "result": result,
                "error": (
                    None
                    if error is None
                    else {"code": "adapter_failed", "message": error}
                ),
            },
            created_at=now,
        ),
        session_state=settled_session,
        run_state=settled_run,
        require_unsettled_root_run_id=run.run_id,
    )


def project_chat_event(
    record: object,
    run: AgentRunRecord | None,
) -> ChatEvent | None:
    from coding_agent.stores.rtstore.harness import EventRecord

    if not isinstance(record, EventRecord):
        raise TypeError("record must be an EventRecord")
    if record.event_kind not in CHAT_EVENT_KINDS:
        return None
    if record.session_seq is None:
        raise ValueError("projected event must include session_seq")
    payload = dict(record.payload)
    payload_run_id = payload.pop("run_id", None)
    if payload_run_id is not None and not isinstance(payload_run_id, str):
        raise TypeError("chat event run_id must be a string")
    if run is not None:
        if payload_run_id != run.run_id or run.session_id != record.session_id:
            raise ChatProjectionCorruptionError(
                "chat event run metadata does not belong to its session"
            )
        if run.superseded_by_checkpoint_id is not None:
            return None
    elif payload_run_id is not None:
        raise ChatProjectionCorruptionError(
            "chat event references missing same-session run metadata"
        )
    return ChatEvent(
        source_event_id=record.event_id,
        session_seq=record.session_seq,
        session_id=record.session_id,
        run_id=payload_run_id,
        kind=record.event_kind,
        created_at=record.created_at,
        payload=payload,
    )


def _validate_chat_payload(kind: ChatEventKind, payload: JSONObject) -> None:
    def required_string(field_name: str, *, allow_empty: bool = False) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise ValueError(f"{kind} payload {field_name} must be a string")
        return value

    if kind in {"user_prompt", "assistant_message", "thinking"}:
        required_string("text")
        return
    if kind == "progress":
        current = payload.get("current")
        total = payload.get("total")
        if (
            isinstance(current, bool)
            or not isinstance(current, int)
            or current < 0
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < current
        ):
            raise ValueError("progress payload requires 0 <= current <= total")
        required_string("label")
        return
    if kind == "tool_call":
        required_string("call_id")
        required_string("tool_name")
        if not isinstance(payload.get("arguments"), dict):
            raise ValueError("tool_call payload arguments must be an object")
        return
    if kind == "tool_result":
        required_string("call_id")
        required_string("output", allow_empty=True)
        if not isinstance(payload.get("is_error"), bool):
            raise ValueError("tool_result payload is_error must be a boolean")
        return
    outcome = payload.get("outcome")
    if outcome not in {"completed", "failed", "cancelled", "interrupted"}:
        raise ValueError("root_terminal payload outcome is invalid")
    result = payload.get("result")
    if result is not None and not isinstance(result, str):
        raise ValueError("root_terminal payload result must be nullable text")
    error = payload.get("error")
    if error is None or isinstance(error, str):
        return
    if not isinstance(error, dict):
        raise ValueError("root_terminal payload error must be nullable text or object")
    for field_name in ("code", "message"):
        value = error.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"root_terminal payload error {field_name} must be non-empty text"
            )


def encode_chat_cursor(cursor: ConnectedChatCursor) -> str:
    if not isinstance(cursor, ConnectedChatCursor):
        raise TypeError("cursor must be a ConnectedChatCursor")
    raw = json.dumps(
        asdict(cursor),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_chat_cursor(
    value: str,
    *,
    expected_session_id: str,
    fact_state: SessionFactSourceState,
) -> ConnectedChatCursor:
    _require_non_empty("expected_session_id", expected_session_id)
    if not isinstance(fact_state, SessionFactSourceState):
        raise TypeError("fact_state must be a SessionFactSourceState")
    try:
        payload = _decode_cursor_payload(value)
        cursor = ConnectedChatCursor(**payload)
        if encode_chat_cursor(cursor) != value:
            raise ValueError("cursor encoding is not canonical")
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        TypeError,
        ValueError,
    ):
        raise ChatCursorError(
            "cursor_malformed", status=400, replay_required=False
        ) from None

    if cursor.session_id != expected_session_id:
        raise ChatCursorError(
            "cursor_foreign_session", status=409, replay_required=False
        )
    if (
        cursor.projection != CONNECTED_CHAT_PROJECTION
        or cursor.epoch != fact_state.projection_epoch
    ):
        raise ChatCursorError("cursor_wrong_epoch", status=409, replay_required=True)

    after = parse_u64(cursor.after_seq, field_name="after_seq")
    high_water = parse_u64(cursor.high_water_seq, field_name="high_water_seq")
    floor = parse_u64(fact_state.retention_floor, field_name="retention_floor")
    head = parse_u64(fact_state.session_seq, field_name="session_seq")
    if after + 1 < floor:
        raise ChatCursorError("cursor_expired", status=410, replay_required=True)
    if after > high_water or high_water > head:
        raise ChatCursorError("cursor_future", status=409, replay_required=False)
    return cursor


def _decode_cursor_payload(value: str) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        raise ValueError("cursor must be a non-empty string")
    padding = "=" * (-len(value) % 4)
    raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS:
        raise ValueError("cursor fields are invalid")
    if isinstance(payload["v"], bool) or not isinstance(payload["v"], int):
        raise TypeError("cursor v must be an integer")
    for field_name in _CURSOR_FIELDS - {"v"}:
        if not isinstance(payload[field_name], str):
            raise TypeError(f"cursor {field_name} must be a string")
    return payload
