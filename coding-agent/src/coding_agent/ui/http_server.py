"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorMessage,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireMessage,
)

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120
SESSION_IDLE_TIMEOUT_MINUTES = 30


@dataclass
class SessionState:
    """In-memory session state."""

    id: str
    created_at: datetime
    last_activity: datetime
    turn_in_progress: bool = False
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict]] = field(default_factory=list)


# In-memory session store
sessions: dict[str, SessionState] = {}

app = FastAPI(title="Coding Agent HTTP API")


def _session_to_dict(session: SessionState) -> dict:
    """Convert session state to dictionary."""
    return {
        "id": session.id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "turn_in_progress": session.turn_in_progress,
        "pending_approval": session.pending_approval is not None,
    }


def _wire_message_to_event(msg: WireMessage) -> dict:
    """Convert wire message to SSE event."""
    match msg:
        case TurnBegin():
            return {
                "event": "TurnBegin",
                "data": json.dumps({"timestamp": msg.timestamp.isoformat()}),
            }
        case TurnEnd():
            return {
                "event": "TurnEnd",
                "data": json.dumps({
                    "stop_reason": msg.stop_reason,
                    "final_message": msg.final_message,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case StreamDelta():
            return {
                "event": "StreamDelta",
                "data": json.dumps({
                    "text": msg.text,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ToolCallBegin():
            return {
                "event": "ToolCallBegin",
                "data": json.dumps({
                    "call_id": msg.call_id,
                    "tool": msg.tool,
                    "args": msg.args,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ToolCallEnd():
            return {
                "event": "ToolCallEnd",
                "data": json.dumps({
                    "call_id": msg.call_id,
                    "result": msg.result,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ApprovalRequest():
            return {
                "event": "ApprovalRequest",
                "data": json.dumps({
                    "call_id": msg.call_id,
                    "tool": msg.tool,
                    "args": msg.args,
                    "risk_level": msg.risk_level,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case StepInfo():
            return {
                "event": "StepInfo",
                "data": json.dumps({
                    "step_number": msg.step_number,
                    "max_steps": msg.max_steps,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ErrorMessage():
            return {
                "event": "ErrorMessage",
                "data": json.dumps({
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case _:
            return {
                "event": "Unknown",
                "data": json.dumps({"type": type(msg).__name__}),
            }


async def _broadcast_event(session: SessionState, event: dict) -> None:
    """Broadcast event to all connected clients."""
    # Remove closed queues
    session.event_queues = [q for q in session.event_queues if not q.full()]
    for queue in session.event_queues:
        try:
            await queue.put(event)
        except Exception:
            # Queue might be closed
            pass


async def _cleanup_idle_sessions() -> None:
    """Background task to clean up idle sessions."""
    while True:
        await asyncio.sleep(60)  # Check every minute
        now = datetime.now()
        expired = []
        for session_id, session in sessions.items():
            idle_time = now - session.last_activity
            if idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
                expired.append(session_id)
        for session_id in expired:
            logger.info(f"Cleaning up idle session: {session_id}")
            del sessions[session_id]


@app.on_event("startup")
async def startup_event() -> None:
    """Start background tasks."""
    asyncio.create_task(_cleanup_idle_sessions())


@app.post("/sessions")
async def create_session() -> dict:
    """Create new session."""
    session_id = str(uuid.uuid4())
    now = datetime.now()
    sessions[session_id] = SessionState(
        id=session_id,
        created_at=now,
        last_activity=now,
    )
    logger.info(f"Created session: {session_id}")
    return {"session_id": session_id}


@app.post("/sessions/{session_id}/prompt")
async def send_prompt(session_id: str, prompt: str) -> EventSourceResponse:
    """Send message, returns SSE stream.

    Returns 409 if a turn is already in progress.
    """
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = sessions[session_id]
    if session.turn_in_progress:
        raise HTTPException(status_code=409, detail="Turn already in progress")

    session.turn_in_progress = True
    session.last_activity = datetime.now()

    async def event_generator() -> AsyncIterator[dict]:
        """Generate SSE events for the turn."""
        try:
            # Emit TurnBegin
            begin_msg = TurnBegin()
            event = _wire_message_to_event(begin_msg)
            await _broadcast_event(session, event)
            yield event

            # Simulate streaming response (placeholder for actual agent loop)
            # This will be replaced with actual integration in Task 4
            chunks = [
                "I'll help you with that request.",
                " Let me analyze the task...",
                " Done!",
            ]
            for chunk in chunks:
                delta = StreamDelta(text=chunk)
                event = _wire_message_to_event(delta)
                await _broadcast_event(session, event)
                yield event
                await asyncio.sleep(0.1)  # Simulate streaming delay

            # Emit TurnEnd
            end_msg = TurnEnd(stop_reason="completed", final_message="Task completed")
            event = _wire_message_to_event(end_msg)
            await _broadcast_event(session, event)
            yield event

        except Exception as e:
            logger.exception("Error during turn")
            error_msg = ErrorMessage(content=str(e))
            event = _wire_message_to_event(error_msg)
            await _broadcast_event(session, event)
            yield event
        finally:
            session.turn_in_progress = False
            session.last_activity = datetime.now()

    return EventSourceResponse(event_generator())


class ApprovalRequestBody:
    """Request body for approval endpoint."""

    def __init__(self, request_id: str, approved: bool, feedback: str | None = None):
        self.request_id = request_id
        self.approved = approved
        self.feedback = feedback


@app.post("/sessions/{session_id}/approve")
async def approve_request(
    session_id: str,
    request_id: str,
    approved: bool,
    feedback: str | None = None,
) -> dict:
    """Respond to approval request."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = sessions[session_id]

    if session.pending_approval is None:
        raise HTTPException(status_code=400, detail="No pending approval request")

    if session.pending_approval.get("call_id") != request_id:
        raise HTTPException(status_code=400, detail="Request ID mismatch")

    session.approval_response = {
        "decision": "approve" if approved else "deny",
        "feedback": feedback,
    }
    session.approval_event.set()
    session.last_activity = datetime.now()

    return {
        "status": "ok",
        "request_id": request_id,
        "decision": "approved" if approved else "denied",
    }


@app.get("/sessions/{session_id}/events")
async def get_events(session_id: str) -> EventSourceResponse:
    """Persistent SSE event stream (fan-out supported)."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = sessions[session_id]
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    session.event_queues.append(queue)

    async def event_generator() -> AsyncIterator[dict]:
        """Generate events from queue."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            # Client disconnected
            if queue in session.event_queues:
                session.event_queues.remove(queue)
            raise

    return EventSourceResponse(event_generator())


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get session state."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    return _session_to_dict(sessions[session_id])


@app.delete("/sessions/{session_id}")
async def close_session(session_id: str) -> dict:
    """Close session and release resources."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = sessions[session_id]

    # Notify all connected clients
    await _broadcast_event(
        session,
        {"event": "SessionClosed", "data": json.dumps({"session_id": session_id})},
    )

    del sessions[session_id]
    logger.info(f"Closed session: {session_id}")

    return {"status": "closed", "session_id": session_id}


# Global approval handler for integration with agent loop
async def wait_for_approval(
    session_id: str,
    approval_req: ApprovalRequest,
) -> ApprovalResponse:
    """Wait for approval response from HTTP clients.

    This function is called by the agent loop when it needs approval.
    It will block until the user responds via the /approve endpoint
    or the timeout expires.
    """
    if session_id not in sessions:
        return ApprovalResponse(
            call_id=approval_req.call_id,
            decision="deny",
            feedback="Session not found",
        )

    session = sessions[session_id]

    if session.turn_in_progress:
        # Set pending approval and notify clients
        session.pending_approval = {
            "call_id": approval_req.call_id,
            "tool": approval_req.tool,
            "args": approval_req.args,
            "risk_level": approval_req.risk_level,
        }
        session.approval_event.clear()
        session.approval_response = None

        # Broadcast approval request to all connected clients
        event = _wire_message_to_event(approval_req)
        await _broadcast_event(session, event)

        # Wait for response or timeout
        try:
            await asyncio.wait_for(
                session.approval_event.wait(),
                timeout=APPROVAL_TIMEOUT_SECONDS,
            )

            if session.approval_response:
                return ApprovalResponse(
                    call_id=approval_req.call_id,
                    decision=session.approval_response["decision"],
                    feedback=session.approval_response.get("feedback"),
                )
        except asyncio.TimeoutError:
            logger.warning(f"Approval timeout for session {session_id}")
            # Broadcast timeout event
            timeout_event = {
                "event": "ApprovalTimeout",
                "data": json.dumps({"call_id": approval_req.call_id}),
            }
            await _broadcast_event(session, timeout_event)
        finally:
            session.pending_approval = None
            session.approval_response = None

    return ApprovalResponse(
        call_id=approval_req.call_id,
        decision="deny",
        feedback="Approval timeout or error",
    )
