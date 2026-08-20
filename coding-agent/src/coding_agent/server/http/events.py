"""Wire-to-SSE mapping, broadcast, and owned event-stream generators."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any


from coding_agent.events import DisplayEventStreamProjector
from coding_agent.server.session_manager import Session
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorMessage,
    LocalWire,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallDelta,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireMessage,
)
from coding_agent.wire.protocol import (
    ThinkingDelta,
    ToolResultDelta,
    TurnStatusDelta,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _http_safe_tool_result_payload(msg: ToolResultDelta) -> dict[str, Any]:
    return {
        "session_id": msg.session_id,
        "agent_id": msg.agent_id,
        "tool_name": msg.tool_name,
        "call_id": msg.call_id,
        "result": None,
        "display_result": msg.display_result,
        "is_error": msg.is_error,
        "timestamp": msg.timestamp.isoformat(),
    }


def _wire_message_to_event(msg: WireMessage) -> dict[str, str]:
    """Convert wire message to SSE event."""
    match msg:
        case TurnEnd():
            return {
                "event": "TurnEnd",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "turn_id": msg.turn_id,
                        "completion_status": msg.completion_status,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case TurnBegin():
            return {
                "event": "TurnBegin",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case StreamDelta():
            return {
                "event": "StreamDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "content": msg.content,
                        "role": msg.role,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ThinkingDelta():
            return {
                "event": "ThinkingDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "text": msg.text,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case TurnStatusDelta():
            return {
                "event": "TurnStatusDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "phase": msg.phase,
                        "elapsed_seconds": msg.elapsed_seconds,
                        "tokens_in": msg.tokens_in,
                        "tokens_out": msg.tokens_out,
                        "model_name": msg.model_name,
                        "context_percent": msg.context_percent,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolCallDelta():
            return {
                "event": "ToolCallDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "tool_name": msg.tool_name,
                        "arguments": msg.arguments,
                        "call_id": msg.call_id,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolResultDelta():
            return {
                "event": "ToolResultDelta",
                "data": json.dumps(_http_safe_tool_result_payload(msg)),
            }
        case ToolCallBegin():
            return {
                "event": "ToolCallBegin",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "call_id": msg.call_id,
                        "tool": msg.tool,
                        "args": msg.args,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolCallEnd():
            return {
                "event": "ToolCallEnd",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "call_id": msg.call_id,
                        "result": msg.result,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ApprovalRequest():
            return {
                "event": "ApprovalRequest",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "request_id": msg.request_id,
                        "tool_call": {
                            "tool_name": msg.tool_call.tool_name
                            if msg.tool_call
                            else "",
                            "arguments": msg.tool_call.arguments
                            if msg.tool_call
                            else {},
                            "call_id": msg.tool_call.call_id if msg.tool_call else "",
                        },
                        "timeout_seconds": msg.timeout_seconds,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ApprovalResponse():
            return {
                "event": "ApprovalResponse",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "request_id": msg.request_id,
                        "approved": msg.approved,
                        "feedback": msg.feedback,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ErrorMessage():
            return {
                "event": "ErrorMessage",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case StepInfo():
            return {
                "event": "StepInfo",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "step_number": msg.step_number,
                        "max_steps": msg.max_steps,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case _:
            return {
                "event": "Unknown",
                "data": json.dumps(
                    {
                        "type": type(msg).__name__,
                        "session_id": getattr(msg, "session_id", None),
                        "agent_id": getattr(msg, "agent_id", None),
                    }
                ),
            }


async def _broadcast_event(session: Session, event: dict[str, str]) -> None:
    """Broadcast event to all connected clients."""
    result = session.broadcast_event_nowait(event)

    if result.full_pruned_count:
        logger.info(
            "Pruned %d full event queue(s) for session %s",
            result.full_pruned_count,
            session.id,
        )
    if result.failed_pruned_count:
        logger.info(
            "Pruned %d failed event queue(s) for session %s",
            result.failed_pruned_count,
            session.id,
        )


async def _cleanup_event_queue_on_disconnect(
    session_id: str,
    queue: asyncio.Queue[dict[str, str]],
) -> None:
    try:
        await asyncio.shield(
            _bindings.module().session_manager.remove_event_queue_async(
                session_id, queue
            )
        )
    except KeyError:
        logger.debug(
            "Event queue cleanup skipped for already-removed session %s",
            session_id,
            exc_info=True,
        )


def _legacy_event_stream_transform(event: dict[str, str]) -> dict[str, str] | None:
    return event


def _display_event_stream_transform(
    session: Session,
    event: dict[str, str],
) -> dict[str, str] | None:
    projector = DisplayEventStreamProjector(
        session_id=session.id,
        current_run_id=lambda: session.current_turn_id,
    )
    return projector.project(event)


async def _owned_session_event_generator(
    session_id: str,
    queue: asyncio.Queue[dict[str, str]],
    transform_event: Callable[[dict[str, str]], dict[str, str] | None],
) -> AsyncIterator[dict[str, str]]:
    try:
        while True:
            try:
                event = await _bindings.module().asyncio.wait_for(
                    queue.get(), timeout=30.0
                )
                try:
                    await _bindings.module().session_manager.verify_event_stream_ownership(
                        session_id
                    )
                except SessionOwnershipConflictError:
                    break
                outbound_event = transform_event(event)
                if outbound_event is not None:
                    yield outbound_event
                if event.get("event") == "SessionClosed":
                    break
            except asyncio.TimeoutError:
                if not await _bindings.module().session_manager.has_session_async(
                    session_id
                ):
                    break
                try:
                    await _bindings.module().session_manager.verify_event_stream_ownership(
                        session_id
                    )
                except SessionOwnershipConflictError:
                    break
                try:
                    if not await _bindings.module().session_manager.has_event_queue_async(
                        session_id, queue
                    ):
                        break
                except KeyError:
                    break
                yield {"event": "ping", "data": ""}
    except asyncio.CancelledError:
        raise
    finally:
        await _cleanup_event_queue_on_disconnect(session_id, queue)


async def stream_wire_messages(
    wire: LocalWire,
    task: asyncio.Task[Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Stream wire messages as SSE events.

    Consumes messages from the wire's outgoing queue and yields SSE events.
    Stops when a TurnEnd message is received.
    """
    while True:
        get_message_task = asyncio.create_task(wire.get_next_outgoing())
        try:
            if task is not None:
                done, pending = await asyncio.wait(
                    {get_message_task, task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done and get_message_task in pending:
                    get_message_task.cancel()
                    try:
                        await get_message_task
                    except asyncio.CancelledError:
                        pass
                    task.result()
                    break
                msg = get_message_task.result()
            else:
                msg = await get_message_task
            event = _wire_message_to_event(msg)
            yield event

            if isinstance(msg, TurnEnd) and not msg.agent_id:
                break
        except asyncio.CancelledError:
            if not get_message_task.done():
                get_message_task.cancel()
            # Client disconnected
            raise
        except Exception as e:
            if not get_message_task.done():
                get_message_task.cancel()
            logger.exception("Error streaming wire message")
            yield {
                "event": "Error",
                "data": json.dumps({"error": str(e)}),
            }
            break


__all__ = [
    "_broadcast_event",
    "_cleanup_event_queue_on_disconnect",
    "_display_event_stream_transform",
    "_http_safe_tool_result_payload",
    "_legacy_event_stream_transform",
    "_owned_session_event_generator",
    "_wire_message_to_event",
    "stream_wire_messages",
]
