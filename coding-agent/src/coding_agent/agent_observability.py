"""Agent-turn observation recorder and sanitized projections."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from agentkit.observability import ObservationSink, SpanRecord
from agentkit.tape.extract import TurnTrace

AgentObservationStatus = Literal["started", "ok", "error", "cancelled"]

AgentObservationKind = Literal[
    "turn.started",
    "llm.usage",
    "tool.call.requested",
    "tool.result.observed",
    "turn.completed",
    "turn.failed",
    "turn.cancelled",
]

_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "content",
        "env",
        "message",
        "output",
        "password",
        "prompt",
        "raw",
        "result",
        "secret",
        "stderr",
        "stdout",
        "text",
        "token",
    }
)
_SAFE_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SAFE_STRUCTURAL_KEYS = frozenset(
    {
        "arg_shape",
        "result_shape",
    }
)


class AgentObservationStore(Protocol):
    def append(self, event: "AgentObservationEvent") -> None: ...


@dataclass(frozen=True)
class AgentObservationEvent:
    kind: AgentObservationKind
    status: AgentObservationStatus
    session_id: str
    run_id: str
    turn_id: str
    sequence_no: int
    attributes: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "status": self.status,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "sequence_no": self.sequence_no,
            "occurred_at": self.occurred_at,
            "attributes": self.attributes,
        }


@dataclass
class JsonlAgentObservationStore:
    root: Path

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, event: AgentObservationEvent) -> None:
        run_dir = self.root / "runs" / event.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "observations.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")


class AgentObservationRecorder:
    def __init__(
        self,
        *,
        store: AgentObservationStore,
        sink: ObservationSink | None = None,
    ) -> None:
        self._store = store
        self._sink = sink
        self._session_id: str | None = None
        self._run_id: str | None = None
        self._turn_id: str | None = None
        self._sequence_no = 0

    def start_turn(self, *, session_id: str, run_id: str, prompt: str) -> None:
        self._session_id = session_id
        self._run_id = run_id
        self._turn_id = run_id
        self._sequence_no = 0
        self._record(
            kind="turn.started",
            status="started",
            attributes={"user_length": len(prompt)},
        )

    def observe_llm_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        provider_name: str,
    ) -> None:
        self._record(
            kind="llm.usage",
            status="ok",
            attributes={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider_name": _safe_label(provider_name),
            },
        )

    def observe_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: Mapping[str, Any],
    ) -> None:
        self._record(
            kind="tool.call.requested",
            status="started",
            attributes={
                "tool_name": _safe_label(tool_name),
                "tool_call_id": tool_call_id,
                "arg_shape": _mapping_shape(arguments),
            },
        )

    def observe_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        result: Any,
        is_error: bool,
    ) -> None:
        self._record(
            kind="tool.result.observed",
            status="error" if is_error else "ok",
            attributes={
                "tool_name": _safe_label(tool_name),
                "tool_call_id": tool_call_id,
                "result_shape": _value_shape(result),
            },
        )

    def complete_turn(
        self, *, status: AgentObservationStatus, turn: TurnTrace | None
    ) -> None:
        kind: AgentObservationKind
        if status == "cancelled":
            kind = "turn.cancelled"
        elif status == "error":
            kind = "turn.failed"
        else:
            kind = "turn.completed"
        attributes: dict[str, Any] = {}
        if turn is not None:
            projection = sanitized_turn_projection(turn)
            output = cast(dict[str, Any], projection["output"])
            attributes = {
                "tool_call_count": output["tool_call_count"],
                "final_present": output["final_present"],
            }
        self._record(kind=kind, status=status, attributes=attributes)
        if turn is not None:
            self._record_turn_span(turn, status=status)

    def fail_turn(self, *, error_type: str) -> None:
        self._record(
            kind="turn.failed",
            status="error",
            attributes={"error_type": _safe_label(error_type)},
        )

    def cancel_turn(self) -> None:
        self._record(kind="turn.cancelled", status="cancelled", attributes={})

    def _record(
        self,
        *,
        kind: AgentObservationKind,
        status: AgentObservationStatus,
        attributes: Mapping[str, Any],
    ) -> None:
        if self._session_id is None or self._run_id is None or self._turn_id is None:
            return
        self._sequence_no += 1
        event = AgentObservationEvent(
            kind=kind,
            status=status,
            session_id=self._session_id,
            run_id=self._run_id,
            turn_id=self._turn_id,
            sequence_no=self._sequence_no,
            attributes=_safe_attributes(attributes),
        )
        try:
            self._store.append(event)
        except Exception:
            pass
        if self._sink is not None:
            try:
                self._sink.record_span(
                    SpanRecord(
                        name="agent.action.sanitized",
                        status="error" if status == "error" else "ok",
                        attributes={
                            "session_id": event.session_id,
                            "run_id": event.run_id,
                            "turn_id": event.turn_id,
                            "event_id": event.event_id,
                            "agent.event.kind": event.kind,
                            "agent.sequence_no": event.sequence_no,
                            **event.attributes,
                        },
                    )
                )
            except Exception:
                return

    def _record_turn_span(
        self,
        turn: TurnTrace,
        *,
        status: AgentObservationStatus,
    ) -> None:
        if self._sink is None or self._session_id is None or self._run_id is None:
            return
        projection = sanitized_turn_projection(turn)
        try:
            self._sink.record_span(
                SpanRecord(
                    name="agent.turn.sanitized",
                    status="error" if status == "error" else "ok",
                    attributes={
                        "session_id": self._session_id,
                        "run_id": self._run_id,
                        "turn_id": self._turn_id or self._run_id,
                        "gen_ai.operation.name": "invoke_agent",
                        "langfuse.observation.input": json.dumps(
                            projection["input"], sort_keys=True
                        ),
                        "langfuse.observation.output": json.dumps(
                            projection["output"], sort_keys=True
                        ),
                    },
                )
            )
        except Exception:
            return


def sanitized_turn_projection(turn: TurnTrace) -> dict[str, Any]:
    tool_calls = []
    for call in turn.tool_calls:
        tool_calls.append(
            {
                "tool_name": _safe_label(call.name),
                "arg_shape": _mapping_shape(call.arguments),
                "result_shape": (
                    _value_shape(call.result_content)
                    if call.result_content is not None
                    else None
                ),
                "status": _heuristic_result_status(call.result_content),
            }
        )
    return {
        "input": {
            "user_present": bool(turn.user_input),
            "user_length": len(turn.user_input),
        },
        "output": {
            "final_present": turn.final_output is not None,
            "final_length": len(turn.final_output or ""),
            "tool_call_count": len(turn.tool_calls),
            "tool_calls": tool_calls,
        },
    }


def _heuristic_result_status(value: str | None) -> str:
    if value is None:
        return "missing"
    folded = value[:80].casefold()
    if folded.startswith("error") or '"error"' in folded:
        return "error"
    return "ok"


def _mapping_shape(arguments: Mapping[str, Any]) -> dict[str, Any]:
    safe_keys: list[str] = []
    types: dict[str, str] = {}
    string_lengths: dict[str, int] = {}
    used: dict[str, int] = {}
    for key, value in arguments.items():
        safe_key = _safe_key_label(str(key))
        count = used.get(safe_key, 0)
        used[safe_key] = count + 1
        if count:
            safe_key = f"{safe_key}_{count + 1}"
        safe_keys.append(safe_key)
        types[safe_key] = _type_name(value)
        if isinstance(value, str):
            string_lengths[safe_key] = len(value)
    shape: dict[str, Any] = {
        "field_count": len(arguments),
        "safe_keys": safe_keys,
        "types": types,
    }
    if string_lengths:
        shape["string_lengths"] = string_lengths
    return shape


def _value_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "type": "str",
            "length": len(value),
            "line_count": len(value.splitlines()) or 1,
        }
    if isinstance(value, Mapping):
        return {"type": "dict", "field_count": len(value)}
    if isinstance(value, list | tuple):
        return {"type": "list", "item_count": len(value)}
    return {"type": _type_name(value)}


def _safe_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        if not _key_allowed(key):
            continue
        safe[key] = _safe_value(value)
    return safe


def _safe_value(value: Any) -> Any:
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, str):
        return _safe_label(value)
    if isinstance(value, Mapping):
        return {
            _safe_key_label(str(key)): _safe_value(item)
            for key, item in value.items()
            if _key_allowed(_safe_key_label(str(key)))
        }
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value]
    return _type_name(value)


def _safe_label(value: str) -> str:
    stripped = value.strip()
    if _SAFE_LABEL_PATTERN.fullmatch(stripped) and not _has_forbidden_part(stripped):
        return stripped
    return "unknown"


def _safe_key_label(value: str) -> str:
    stripped = value.strip()
    if _SAFE_LABEL_PATTERN.fullmatch(stripped) and not _has_forbidden_part(stripped):
        return stripped
    return "redacted_key"


def _key_allowed(value: str) -> bool:
    if value in _SAFE_STRUCTURAL_KEYS:
        return True
    return not _has_forbidden_part(value)


def _has_forbidden_part(value: str) -> bool:
    folded = value.casefold()
    return any(part in folded for part in _FORBIDDEN_KEY_PARTS)


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, Mapping):
        return "dict"
    if isinstance(value, list | tuple):
        return "list"
    if value is None:
        return "none"
    return type(value).__name__
