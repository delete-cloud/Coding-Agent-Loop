"""Session result reconstruction routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from fastapi import Depends, Request

from agentkit.result.models import TurnResult
from agentkit.tape.extract import ToolCallRecord, TurnTrace, extract_turns
from agentkit.tape.tape import Tape
from coding_agent.stores.runtime_store import (
    JSONObject,
)
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    SessionResultResponse,
)
from coding_agent.server.session_manager import Session

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import _get_visible_session
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


def _session_runtime_tape(session: Session) -> Tape | None:
    runtime_ctx = session.runtime_ctx
    if runtime_ctx is None:
        return None
    tape = getattr(runtime_ctx, "tape", None)
    if tape is None:
        return None
    if not isinstance(tape, Tape):
        raise TypeError("session runtime context has invalid tape")
    return tape


async def _session_result_tape(session: Session) -> Tape | None:
    runtime_tape = _session_runtime_tape(session)
    if runtime_tape is not None:
        return runtime_tape
    return await _bindings.module().session_manager._restore_tape(session.tape_id)


async def _session_result_latest_turn(session: Session) -> TurnTrace | None:
    tape = await _session_result_tape(session)
    if tape is None:
        return None
    turns = extract_turns(tape.snapshot())
    if not turns:
        return None
    return turns[-1]


def _runtime_event_message(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    message = payload.get("message")
    if isinstance(message, Mapping):
        return cast(Mapping[str, Any], message)
    return payload


def _runtime_event_string(message: Mapping[str, Any], key: str) -> str:
    value = message.get(key)
    return value if isinstance(value, str) else ""


def _runtime_event_arguments(message: Mapping[str, Any]) -> JSONObject:
    arguments = message.get("arguments")
    if isinstance(arguments, Mapping):
        return cast(JSONObject, dict(arguments))
    return {}


async def _session_result_runtime_run_id(session: Session) -> str | None:
    if session.current_turn_id is not None:
        return session.current_turn_id
    try:
        runs = await _bindings.module().session_manager.list_active_runtime_runs(
            session.id
        )
    except RuntimeError:
        return None
    if not runs:
        return None
    latest = max(
        runs,
        key=lambda run: run.ended_at or run.started_at,
    )
    return latest.run_id


async def _session_result_from_runtime_events(run_id: str) -> TurnResult | None:
    _ = await _bindings.module().session_manager.load_runtime_run(run_id)
    events = await _bindings.module().session_manager.replay_runtime_events(
        run_id, limit=1000
    )

    content_parts: list[str] = []
    tool_calls: dict[str, ToolCallRecord] = {}
    anonymous_tool_calls: list[ToolCallRecord] = []
    for event in events:
        message = _runtime_event_message(event.payload)
        if event.event_kind == "wire.StreamDelta":
            if _runtime_event_string(message, "agent_id"):
                continue
            content = _runtime_event_string(message, "content")
            if content:
                content_parts.append(content)
        elif event.event_kind == "wire.ToolCallDelta":
            record = ToolCallRecord(
                call_id=_runtime_event_string(message, "call_id"),
                name=_runtime_event_string(message, "tool_name"),
                arguments=_runtime_event_arguments(message),
            )
            if record.call_id:
                tool_calls[record.call_id] = record
            else:
                anonymous_tool_calls.append(record)

    final_output = "".join(content_parts) if content_parts else None
    if final_output is None and not tool_calls and not anonymous_tool_calls:
        return None
    return _bindings.module().result_from_turn_trace(
        TurnTrace(
            user_input="",
            tool_calls=tuple([*tool_calls.values(), *anonymous_tool_calls]),
            final_output=final_output,
        )
    )


def _session_result_failure_details(session: Session) -> str | None:
    details = session.last_failure_details
    if details is not None:
        return details
    if session.turn_status == "failed":
        return "Session turn failed; no failure details were recorded."
    return None


@router.get("/sessions/{session_id}/result", response_model=SessionResultResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_result(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionResultResponse:
    del request
    session = await _get_visible_session(session_id, auth_context)
    summary = session.as_dict()
    latest_turn = await _session_result_latest_turn(session)
    result_turn_id = session.current_turn_id
    if latest_turn is None:
        result_turn_id = await _session_result_runtime_run_id(session)
        turn_result = (
            None
            if result_turn_id is None
            else await _session_result_from_runtime_events(result_turn_id)
        )
    else:
        turn_result = _bindings.module().result_from_turn_trace(latest_turn)
    verification_summary = (
        None
        if turn_result is None or turn_result.verification_summary is None
        else turn_result.verification_summary.summary
    )
    return SessionResultResponse(
        session_id=session.id,
        status=summary["status"],
        turn_status=summary["turn_status"],
        turn_id=result_turn_id,
        workspace_id=summary["workspace_id"],
        origin=session.origin,
        provider_name=session.provider_name,
        model_name=session.model_name,
        final_answer=None if turn_result is None else turn_result.final_output,
        verification_summary=verification_summary,
        failure_details=_session_result_failure_details(session),
    )


__all__ = [
    "_runtime_event_arguments",
    "_runtime_event_message",
    "_runtime_event_string",
    "_session_result_failure_details",
    "_session_result_from_runtime_events",
    "_session_result_latest_turn",
    "_session_result_runtime_run_id",
    "_session_result_tape",
    "_session_runtime_tape",
    "get_session_result",
]
