"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    # Startup
    cleanup_task = asyncio.create_task(_cleanup_idle_sessions())
    logger.info("HTTP server starting up")

    yield  # Server runs here

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Close all sessions
    for session_id in list(sessions.keys()):
        await close_session(session_id)

    logger.info("HTTP server shut down")


app = FastAPI(title="Coding Agent HTTP API", lifespan=lifespan)


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
        case TurnEnd():
            return {
                "event": "TurnEnd",
                "data": json.dumps({
                    "session_id": msg.session_id,
                    "turn_id": msg.turn_id,
                    "completion_status": msg.completion_status,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case StreamDelta():
            return {
                "event": "StreamDelta",
                "data": json.dumps({
                    "session_id": msg.session_id,
                    "content": msg.content,
                    "role": msg.role,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ToolCallDelta():
            return {
                "event": "ToolCallDelta",
                "data": json.dumps({
                    "session_id": msg.session_id,
                    "tool_name": msg.tool_name,
                    "arguments": msg.arguments,
                    "call_id": msg.call_id,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ApprovalRequest():
            return {
                "event": "ApprovalRequest",
                "data": json.dumps({
                    "session_id": msg.session_id,
                    "request_id": msg.request_id,
                    "tool_call": {
                        "tool_name": msg.tool_call.tool_name,
                        "arguments": msg.tool_call.arguments,
                        "call_id": msg.tool_call.call_id,
                    },
                    "timeout_seconds": msg.timeout_seconds,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case ApprovalResponse():
            return {
                "event": "ApprovalResponse",
                "data": json.dumps({
                    "session_id": msg.session_id,
                    "request_id": msg.request_id,
                    "approved": msg.approved,
                    "feedback": msg.feedback,
                    "timestamp": msg.timestamp.isoformat(),
                }),
            }
        case _:
            return {
                "event": "Unknown",
                "data": json.dumps({
                    "type": type(msg).__name__,
                    "session_id": getattr(msg, "session_id", None),
                }),
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "sessions": len(sessions),
        "version": "2.0.0"
    }


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
            # Simulate streaming response (placeholder for actual agent loop)
            # This will be replaced with actual integration in Task 4
            chunks = [
                "I'll help you with that request.",
                " Let me analyze the task...",
                " Done!",
            ]
            for chunk in chunks:
                delta = StreamDelta(
                    session_id=session_id,
                    content=chunk,
                    role="assistant",
                )
                event = _wire_message_to_event(delta)
                await _broadcast_event(session, event)
                yield event
                await asyncio.sleep(0.1)  # Simulate streaming delay

            # Emit TurnEnd
            end_msg = TurnEnd(
                session_id=session_id,
                turn_id=str(uuid.uuid4()),
                completion_status="completed",
            )
            event = _wire_message_to_event(end_msg)
            await _broadcast_event(session, event)
            yield event

        except Exception as e:
            logger.exception("Error during turn")
            # Create error-like response
            error_data = {
                "event": "Error",
                "data": json.dumps({
                    "session_id": session_id,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                }),
            }
            await _broadcast_event(session, error_data)
            yield error_data
        finally:
            session.turn_in_progress = False
            session.last_activity = datetime.now()

    return EventSourceResponse(event_generator())


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

    if session.pending_approval.get("request_id") != request_id:
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
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Session not found",
        )

    session = sessions[session_id]

    if session.turn_in_progress:
        # Set pending approval and notify clients
        session.pending_approval = {
            "request_id": approval_req.request_id,
            "tool_name": approval_req.tool_call.tool_name,
            "arguments": approval_req.tool_call.arguments,
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
                    session_id=session_id,
                    request_id=approval_req.request_id,
                    approved=session.approval_response["decision"] == "approve",
                    feedback=session.approval_response.get("feedback"),
                )
        except asyncio.TimeoutError:
            logger.warning(f"Approval timeout for session {session_id}")
            # Broadcast timeout event
            timeout_event = {
                "event": "ApprovalTimeout",
                "data": json.dumps({"request_id": approval_req.request_id}),
            }
            await _broadcast_event(session, timeout_event)
        finally:
            session.pending_approval = None
            session.approval_response = None

    return ApprovalResponse(
        session_id=session_id,
        request_id=approval_req.request_id,
        approved=False,
        feedback="Approval timeout or error",
    )
