"""Wire-to-SSE mapping, broadcast, and owned event-stream generators."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal


from coding_agent.events import DisplayEventStreamProjector
from coding_agent.events.connected_chat import (
    CONNECTED_CHAT_CONTRACT_VERSION,
    ChatCursorError,
    ChatEvent,
)
from coding_agent.server.schemas import (
    ConnectedChatEventSchema,
    ConnectedChatStreamControlSchema,
)
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)
from coding_agent.server.session_manager import Session
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


@dataclass(frozen=True)
class StreamControl:
    kind: Literal["replay_required"]
    reason: Literal["subscriber_queue_overflow", "ownership_lost", "sequence_loss"]
    cursor: str


def _chat_stream_openapi_response(description: str) -> dict[str, object]:
    return {
        "model": ConnectedChatEventSchema | ConnectedChatStreamControlSchema,
        "description": description,
        "content": {
            "text/event-stream": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/ConnectedChatEventSchema"},
                        {
                            "$ref": "#/components/schemas/ConnectedChatStreamControlSchema"
                        },
                    ]
                },
                "examples": {
                    "chat_event": {
                        "summary": "Canonical connected-chat event frame data",
                        "value": {
                            "contract_version": "1.0.0",
                            "source_event_id": "evt-user-01",
                            "session_seq": "12",
                            "session_id": "session-01",
                            "run_id": "run-01",
                            "kind": "user_prompt",
                            "created_at": "2026-08-24T00:00:00Z",
                            "payload": {"text": "Run tests"},
                        },
                    },
                    "stream_control": {
                        "summary": "Replay-required stream control frame data",
                        "value": {
                            "contract_version": "1.0.0",
                            "kind": "replay_required",
                            "reason": "subscriber_queue_overflow",
                            "cursor": "eyJhZnRlcl9zZXEiOiIxMiIsImVwb2NoIjoiNyIsImhpZ2hfd2F0ZXJfc2VxIjoiMjAiLCJraW5kIjoiY2hhdCIsInByb2plY3Rpb24iOiJjb25uZWN0ZWQtY2hhdCIsInNlc3Npb25faWQiOiJzZXNzaW9uLTAxIiwidiI6MX0",
                        },
                    },
                },
            }
        },
    }


def _canonical_chat_timestamp(value: datetime) -> str:
    text = value.isoformat()
    if value.utcoffset() == timedelta(0):
        return text.replace("+00:00", "Z")
    return text


def _chat_event_sse_frame(event: ChatEvent) -> dict[str, str]:
    """Encode one canonical ChatEvent as an SSE chat_event frame."""
    if not isinstance(event, ChatEvent):
        raise TypeError("chat_event frame requires a ChatEvent")
    envelope = {
        "contract_version": event.contract_version,
        "source_event_id": event.source_event_id,
        "session_seq": event.session_seq,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "kind": event.kind,
        "created_at": _canonical_chat_timestamp(event.created_at),
        "payload": event.payload,
    }
    return {
        "event": "chat_event",
        "id": event.session_seq,
        "data": json.dumps(envelope, ensure_ascii=False),
    }


def _stream_control_sse_frame(control: StreamControl) -> dict[str, str]:
    """Encode one StreamControl as an SSE stream_control frame."""
    if not isinstance(control, StreamControl):
        raise TypeError("stream_control frame requires a StreamControl")
    return {
        "event": "stream_control",
        "data": json.dumps(
            {
                "contract_version": CONNECTED_CHAT_CONTRACT_VERSION,
                "kind": control.kind,
                "reason": control.reason,
                "cursor": control.cursor,
            }
        ),
    }


async def _chat_stream_sse_frames(
    stream: AsyncIterator[Any],
) -> AsyncIterator[dict[str, str]]:
    """Passively encode a manager chat stream; never settles any run."""
    try:
        async for item in stream:
            if isinstance(item, ChatEvent):
                yield _chat_event_sse_frame(item)
            elif isinstance(item, StreamControl):
                yield _stream_control_sse_frame(item)
            else:
                raise TypeError(f"unsupported chat stream item: {type(item).__name__}")
    finally:
        close = getattr(stream, "aclose", None)
        if callable(close):
            await close()


class ChatSubscriber:
    def __init__(self, queue_size: int) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        self.overflowed = asyncio.Event()

    def publish(self, event: Any) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed.set()


class ChatFollowBridge:
    def __init__(
        self,
        *,
        session_id: str,
        projection_epoch: str,
        register: Callable[[ChatSubscriber], Awaitable[None]],
        capture_high_water: Callable[[], Awaitable[str]],
        replay: Callable[[str, str], Awaitable[tuple[Any, ...]]],
        verify_ownership: Callable[[], Awaitable[bool]],
        unregister: Callable[[ChatSubscriber], Awaitable[None]],
        queue_size: int = 100,
    ) -> None:
        self.session_id = session_id
        self.projection_epoch = projection_epoch
        self._register = register
        self._capture_high_water = capture_high_water
        self._replay = replay
        self._verify_ownership = verify_ownership
        self._unregister = unregister
        self._queue_size = queue_size

    def cursor(self, after_seq: str, high_water_seq: str) -> str:
        from coding_agent.events.connected_chat import (
            CONNECTED_CHAT_PROJECTION,
            ConnectedChatCursor,
            encode_chat_cursor,
        )

        return encode_chat_cursor(
            ConnectedChatCursor(
                v=1,
                kind="chat",
                session_id=self.session_id,
                projection=CONNECTED_CHAT_PROJECTION,
                epoch=self.projection_epoch,
                after_seq=after_seq,
                high_water_seq=high_water_seq,
            )
        )

    async def follow(self, *, after_seq: str) -> AsyncIterator[Any]:
        subscriber = ChatSubscriber(self._queue_size)
        last_safe = after_seq
        high_water = after_seq
        try:
            await self._register(subscriber)
        except SessionOwnershipConflictError:
            yield StreamControl(
                "replay_required",
                "ownership_lost",
                self.cursor(last_safe, high_water),
            )
            return
        try:
            if not await self._verify_ownership():
                yield StreamControl(
                    "replay_required",
                    "ownership_lost",
                    self.cursor(last_safe, high_water),
                )
                return
            high_water = await self._capture_high_water()
            try:
                replayed = await self._replay(after_seq, high_water)
            except ChatCursorError:
                yield StreamControl(
                    "replay_required",
                    "cursor_wrong_epoch",
                    self.cursor(last_safe, high_water),
                )
                return
            for event in replayed:
                yield event
                last_safe = event.session_seq
            while True:
                if subscriber.overflowed.is_set():
                    yield StreamControl(
                        "replay_required",
                        "subscriber_queue_overflow",
                        self.cursor(last_safe, high_water),
                    )
                    return
                event_task = asyncio.create_task(subscriber.queue.get())
                overflow_task = asyncio.create_task(subscriber.overflowed.wait())
                owner_task = asyncio.create_task(asyncio.sleep(30.0))
                try:
                    await asyncio.wait(
                        {event_task, overflow_task, owner_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for waited in (event_task, overflow_task, owner_task):
                        waited.cancel()
                    await asyncio.gather(
                        event_task,
                        overflow_task,
                        owner_task,
                        return_exceptions=True,
                    )
                if overflow_task.done() and not overflow_task.cancelled() and subscriber.overflowed.is_set():
                    yield StreamControl(
                        "replay_required",
                        "subscriber_queue_overflow",
                        self.cursor(last_safe, high_water),
                    )
                    return
                if event_task.done() and not event_task.cancelled():
                    event = event_task.result()
                    if int(event.session_seq) <= int(high_water):
                        continue
                    if not await self._verify_ownership():
                        yield StreamControl(
                            "replay_required",
                            "ownership_lost",
                            self.cursor(last_safe, high_water),
                        )
                        return
                    if int(event.session_seq) != int(last_safe) + 1:
                        try:
                            recovered = await self._replay(last_safe, event.session_seq)
                        except ChatCursorError:
                            yield StreamControl(
                                "replay_required",
                                "cursor_wrong_epoch",
                                self.cursor(last_safe, high_water),
                            )
                            return
                        if not recovered or recovered[-1].session_seq != event.session_seq:
                            yield StreamControl(
                                "replay_required",
                                "sequence_loss",
                                self.cursor(last_safe, high_water),
                            )
                            return
                        for recovered_event in recovered:
                            if int(recovered_event.session_seq) <= int(last_safe):
                                continue
                            yield recovered_event
                            last_safe = recovered_event.session_seq
                            high_water = last_safe
                        continue
                    yield event
                    last_safe = event.session_seq
                    high_water = last_safe
                    continue
                if not await self._verify_ownership():
                    yield StreamControl(
                        "replay_required",
                        "ownership_lost",
                        self.cursor(last_safe, high_water),
                    )
                    return
        finally:
            await asyncio.shield(self._unregister(subscriber))


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
    "_chat_event_sse_frame",
    "_chat_stream_openapi_response",
    "_chat_stream_sse_frames",
    "_cleanup_event_queue_on_disconnect",
    "_display_event_stream_transform",
    "_http_safe_tool_result_payload",
    "_legacy_event_stream_transform",
    "_owned_session_event_generator",
    "_stream_control_sse_frame",
    "_wire_message_to_event",
    "stream_wire_messages",
]
