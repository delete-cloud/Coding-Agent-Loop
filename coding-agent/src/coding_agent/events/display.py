from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import uuid
from typing import Literal, cast

from coding_agent.stores.runtime_store import JSONObject, JSONValue, RuntimeEventRecord

DisplayEventKind = Literal[
    "assistant_text_delta",
    "thinking_delta",
    "tool_call",
    "tool_result",
    "approval_prompt",
    "approval_result",
    "progress_update",
    "final_result",
]


@dataclass(frozen=True, slots=True)
class DisplayEvent:
    source_event_id: str
    run_id: str
    sequence: int | None
    display_kind: DisplayEventKind
    payload: JSONObject
    created_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("source_event_id", self.source_event_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("display_kind", self.display_kind)
        if self.sequence is not None and self.sequence <= 0:
            raise ValueError("sequence must be positive")


@dataclass(frozen=True, slots=True)
class DisplayEventStreamProjector:
    session_id: str
    current_run_id: Callable[[], str | None]
    live_event_id: Callable[[], str] = lambda: uuid.uuid4().hex

    def __post_init__(self) -> None:
        _require_non_empty("session_id", self.session_id)

    def project(self, event: Mapping[str, str]) -> dict[str, str] | None:
        display_event = project_wire_sse_event_to_display(
            event,
            source_event_id=f"live:{self.session_id}:{self.live_event_id()}",
            current_run_id=self.current_run_id(),
        )
        if display_event is None:
            return None
        return display_event_sse_response(display_event)


def display_event_sse_response(record: DisplayEvent) -> dict[str, str]:
    return {
        "event": record.display_kind,
        "data": json.dumps(
            {
                "source_event_id": record.source_event_id,
                "run_id": record.run_id,
                "sequence": record.sequence,
                "display_kind": record.display_kind,
                "payload": record.payload,
                "created_at": record.created_at.isoformat(),
            }
        ),
    }


def project_runtime_events_to_display(
    events: Iterable[RuntimeEventRecord],
) -> list[DisplayEvent]:
    projected: list[DisplayEvent] = []
    for event in events:
        display = project_runtime_event_to_display(event)
        if display is not None:
            projected.append(display)
    return projected


def project_runtime_event_to_display(
    event: RuntimeEventRecord,
) -> DisplayEvent | None:
    message = _runtime_event_message(event.payload)
    match event.event_kind:
        case "wire.StreamDelta":
            return _display_event(
                event,
                "assistant_text_delta",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "content": message.get("content"),
                        "role": message.get("role"),
                    }
                ),
            )
        case "wire.ThinkingDelta":
            return _display_event(
                event,
                "thinking_delta",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "text": message.get("text"),
                    }
                ),
            )
        case "wire.ToolCallDelta":
            return _display_event(
                event,
                "tool_call",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "call_id": message.get("call_id"),
                        "tool_name": message.get("tool_name"),
                        "arguments": message.get("arguments"),
                    }
                ),
            )
        case "wire.ToolResultDelta":
            return _display_event(
                event,
                "tool_result",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "call_id": message.get("call_id"),
                        "tool_name": message.get("tool_name"),
                        "display_result": message.get("display_result"),
                        "is_error": message.get("is_error"),
                    }
                ),
            )
        case "wire.ApprovalRequest":
            return _display_event(
                event,
                "approval_prompt",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "request_id": message.get("request_id"),
                        "timeout_seconds": message.get("timeout_seconds"),
                        "tool_call": message.get("tool_call"),
                    }
                ),
            )
        case "wire.ApprovalResponse":
            return _display_event(
                event,
                "approval_result",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "request_id": message.get("request_id"),
                        "approved": message.get("approved"),
                        "feedback": message.get("feedback"),
                    }
                ),
            )
        case "wire.TurnStatusDelta":
            return _display_event(
                event,
                "progress_update",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "phase": message.get("phase"),
                        "elapsed_seconds": message.get("elapsed_seconds"),
                        "tokens_in": message.get("tokens_in"),
                        "tokens_out": message.get("tokens_out"),
                        "model_name": message.get("model_name"),
                        "context_percent": message.get("context_percent"),
                    }
                ),
            )
        case "wire.TurnEnd":
            return _display_event(
                event,
                "final_result",
                _compact_payload(
                    {
                        "agent_id": message.get("agent_id"),
                        "turn_id": message.get("turn_id"),
                        "completion_status": message.get("completion_status"),
                    }
                ),
            )
        case _:
            return None


def project_wire_sse_event_to_display(
    event: Mapping[str, str],
    *,
    source_event_id: str,
    current_run_id: str | None,
    created_at: datetime | None = None,
) -> DisplayEvent | None:
    event_name = event.get("event")
    if not event_name or event_name == "ping":
        return None
    payload = _wire_sse_payload(event)
    run_id = _wire_sse_run_id(payload, current_run_id=current_run_id)
    if run_id is None:
        return None
    event_record = RuntimeEventRecord(
        event_id=source_event_id,
        run_id=run_id,
        event_kind=f"wire.{event_name}",
        payload={
            "message_type": event_name,
            "message": payload,
        },
        created_at=_wire_sse_created_at(payload, fallback=created_at),
    )
    return project_runtime_event_to_display(event_record)


def _display_event(
    event: RuntimeEventRecord,
    display_kind: DisplayEventKind,
    payload: JSONObject,
) -> DisplayEvent:
    return DisplayEvent(
        source_event_id=event.event_id,
        run_id=event.run_id,
        sequence=event.sequence,
        display_kind=display_kind,
        payload=payload,
        created_at=event.created_at,
    )


def _runtime_event_message(payload: JSONObject) -> Mapping[str, JSONValue]:
    message = payload.get("message")
    if isinstance(message, Mapping):
        return cast(Mapping[str, JSONValue], message)
    return payload


def _wire_sse_payload(event: Mapping[str, str]) -> JSONObject:
    raw_data = event.get("data")
    if raw_data is None or raw_data == "":
        return {}
    try:
        decoded = json.loads(raw_data)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, Mapping):
        return {}
    return _compact_payload(decoded)


def _wire_sse_run_id(
    payload: Mapping[str, JSONValue],
    *,
    current_run_id: str | None,
) -> str | None:
    for key in ("run_id", "turn_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if current_run_id is not None and current_run_id.strip():
        return current_run_id
    return None


def _wire_sse_created_at(
    payload: Mapping[str, JSONValue],
    *,
    fallback: datetime | None,
) -> datetime:
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str) and timestamp.strip():
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            pass
    return fallback if fallback is not None else datetime.now(UTC)


def _compact_payload(payload: Mapping[str, object]) -> JSONObject:
    compact: JSONObject = {}
    for key, value in payload.items():
        if value is None:
            continue
        compact[key] = _json_value(value)
    return compact


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return str(value)


def _require_non_empty(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
