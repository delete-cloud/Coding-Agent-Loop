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

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from coding_agent.approval import ApprovalPolicy
from coding_agent.ui.session_manager import SessionManager
from coding_agent.ui.schemas import (
    PromptRequest,
    CreateSessionRequest,
    ApproveRequest,
    SessionResponse,
    ApprovalResponseSchema,
    CloseSessionResponse,
    HealthResponse,
)
from coding_agent.ui.auth import verify_api_key
from coding_agent.ui.rate_limit import limiter, RateLimits
from slowapi.errors import RateLimitExceeded
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
from coding_agent.wire.protocol import ToolResultDelta

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120
SESSION_IDLE_TIMEOUT_MINUTES = 30


@dataclass
class SessionState:
    """In-memory session state (for backward compatibility with tests)."""

    id: str
    created_at: datetime
    last_activity: datetime
    turn_in_progress: bool = False
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict]] = field(default_factory=list)


# Global session manager
session_manager = SessionManager()

# In-memory session store (for backward compatibility with existing tests)
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

# Add rate limiter to app state
app.state.limiter = limiter

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Add exception handler for rate limit exceeded
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail=str(exc))


def _session_to_dict(session: SessionState) -> dict:
    """Convert session state to dictionary."""
    return {
        "id": session.id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "turn_in_progress": session.turn_in_progress,
        "pending_approval": session.pending_approval is not None,
    }


def _http_safe_tool_result_payload(msg: ToolResultDelta) -> dict[str, Any]:
    return {
        "session_id": msg.session_id,
        "call_id": msg.call_id,
        "tool_name": msg.tool_name,
        "result": None,
        "display_result": msg.display_result,
        "is_error": msg.is_error,
        "timestamp": msg.timestamp.isoformat(),
    }


def _http_safe_tool_call_end_payload(msg: ToolCallEnd) -> dict[str, Any]:
    return {
        "session_id": msg.session_id,
        "call_id": msg.call_id,
        "result": None,
        "timestamp": msg.timestamp.isoformat(),
    }


def _wire_message_to_event(msg: WireMessage) -> dict:
    """Convert wire message to SSE event."""
    match msg:
        case TurnEnd():
            return {
                "event": "TurnEnd",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
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
                        "content": msg.content,
                        "role": msg.role,
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
                "data": json.dumps(_http_safe_tool_call_end_payload(msg)),
            }
        case ApprovalRequest():
            return {
                "event": "ApprovalRequest",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
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
                    }
                ),
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
        try:
            # Cleanup in session manager
            await session_manager.cleanup_idle_sessions(SESSION_IDLE_TIMEOUT_MINUTES)

            # Cleanup legacy sessions (for backward compatibility)
            now = datetime.now()
            expired = []
            for session_id, session in sessions.items():
                idle_time = now - session.last_activity
                if idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
                    expired.append(session_id)
            for session_id in expired:
                logger.info(f"Cleaning up idle legacy session: {session_id}")
                del sessions[session_id]
        except Exception as e:
            logger.exception("Error during idle session cleanup")


async def stream_wire_messages(wire: LocalWire) -> AsyncIterator[dict]:
    """Stream wire messages as SSE events.

    Consumes messages from the wire's outgoing queue and yields SSE events.
    Stops when a TurnEnd message is received.
    """
    while True:
        try:
            msg = await wire.get_next_outgoing()
            event = _wire_message_to_event(msg)
            yield event

            # Stop streaming on TurnEnd
            if isinstance(msg, TurnEnd):
                break
        except asyncio.CancelledError:
            # Client disconnected
            raise
        except Exception as e:
            logger.exception("Error streaming wire message")
            yield {
                "event": "Error",
                "data": json.dumps({"error": str(e)}),
            }
            break


@app.get("/health", response_model=HealthResponse)
@limiter.limit(RateLimits.HEALTH)
async def health_check(request: Request):
    """Health check endpoint."""
    return HealthResponse(status="healthy", sessions=len(sessions), version="2.0.0")


@app.post("/sessions", response_model=SessionResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def create_session(
    request: Request,
    body: CreateSessionRequest | None = None,
    api_key: str | None = Depends(verify_api_key),
) -> SessionResponse:
    """Create new session with AgentLoop integration."""
    # Use defaults if no body provided
    repo_path = body.repo_path if body else None
    approval_policy_str = body.approval_policy if body else "auto"

    # Map string to ApprovalPolicy enum
    approval_policy_map = {
        "yolo": ApprovalPolicy.YOLO,
        "interactive": ApprovalPolicy.INTERACTIVE,
        "auto": ApprovalPolicy.AUTO,
    }
    approval_policy = approval_policy_map.get(approval_policy_str, ApprovalPolicy.AUTO)

    # Create session using SessionManager
    session_id = await session_manager.create_session(
        repo_path=repo_path,
        approval_policy=approval_policy,
        provider=None,  # Will use mock/test provider
    )

    # Also create legacy session state for backward compatibility with tests
    now = datetime.now()
    sessions[session_id] = SessionState(
        id=session_id,
        created_at=now,
        last_activity=now,
    )

    logger.info(f"Created session: {session_id}")
    return SessionResponse(session_id=session_id)


@app.post("/sessions/{session_id}/prompt")
@limiter.limit(RateLimits.SEND_PROMPT)
async def send_prompt(
    request: Request,
    session_id: str,
    body: PromptRequest | None = None,
    prompt: str | None = None,  # Backward compat: query param
    api_key: str | None = Depends(verify_api_key),
) -> EventSourceResponse:
    """Send message, returns SSE stream.

    Returns 409 if a turn is already in progress.
    Accepts prompt via JSON body (preferred) or query param (backward compat).
    """
    # Get prompt from body or query param (body takes precedence)
    prompt_text = body.prompt if body else prompt
    if not prompt_text:
        raise HTTPException(status_code=422, detail="Prompt is required")

    # Check in session manager (primary) or legacy sessions (backward compat)
    has_session_manager = session_manager.has_session(session_id)
    has_legacy = session_id in sessions

    if not has_session_manager and not has_legacy:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get session from session_manager if available
    session = None
    if has_session_manager:
        session = session_manager.get_session(session_id)

    # Check if turn is already in progress (check both session_manager and legacy state)
    if session and session.task and not session.task.done():
        raise HTTPException(status_code=409, detail="Turn already in progress")

    # Also check legacy session state for backward compatibility
    if session_id in sessions and sessions[session_id].turn_in_progress:
        raise HTTPException(status_code=409, detail="Turn already in progress")

    # Update legacy session state if exists
    if session_id in sessions:
        sessions[session_id].turn_in_progress = True
        sessions[session_id].last_activity = datetime.now()

    async def event_generator() -> AsyncIterator[dict]:
        """Generate SSE events for the turn."""
        # If no session_manager session, just yield TurnEnd for legacy compatibility
        if not session:
            # Legacy mode: just yield a simple TurnEnd
            yield {
                "event": "TurnEnd",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "turn_id": "legacy-turn",
                        "completion_status": "completed",
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
            }
            return

        try:
            # Start agent run in background
            session.task = asyncio.create_task(
                session_manager.run_agent(session_id, prompt_text)
            )

            # Stream wire messages
            async for event in stream_wire_messages(session.wire):
                # Also broadcast to legacy event queues
                if session_id in sessions:
                    await _broadcast_event(sessions[session_id], event)
                yield event

        except Exception as e:
            logger.exception("Error during turn")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
            }
            if session_id in sessions:
                await _broadcast_event(sessions[session_id], error_data)
            yield error_data
        finally:
            if session_id in sessions:
                sessions[session_id].turn_in_progress = False
                sessions[session_id].last_activity = datetime.now()

    # Return SSE stream from wire
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.post("/sessions/{session_id}/approve", response_model=ApprovalResponseSchema)
@limiter.limit(RateLimits.APPROVE)
async def approve_request(
    request: Request,
    session_id: str,
    body: ApproveRequest | None = None,
    request_id: str | None = None,  # Backward compat: query param
    approved: bool | None = None,  # Backward compat: query param
    feedback: str | None = None,  # Backward compat: query param
    api_key: str | None = Depends(verify_api_key),
) -> ApprovalResponseSchema:
    """Respond to approval request.

    Accepts parameters via JSON body (preferred) or query params (backward compat).
    """
    # Get values from body or query params (body takes precedence)
    req_id = body.request_id if body else request_id
    is_approved = body.approved if body else approved
    fb = body.feedback if body else feedback

    if req_id is None:
        raise HTTPException(status_code=422, detail="request_id is required")
    if is_approved is None:
        raise HTTPException(status_code=422, detail="approved is required")

    # Check in session_manager or legacy sessions
    has_session_manager = session_manager.has_session(session_id)
    has_legacy = session_id in sessions

    if not has_session_manager and not has_legacy:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check legacy session state for pending approval (for backward compatibility)
    if has_legacy:
        session = sessions[session_id]
        if session.pending_approval is None:
            raise HTTPException(status_code=400, detail="No pending approval request")
        if session.pending_approval.get("request_id") != req_id:
            raise HTTPException(status_code=400, detail="Request ID mismatch")

    # Try to submit approval via session_manager if session exists there
    if has_session_manager:
        try:
            await session_manager.submit_approval(
                session_id=session_id,
                request_id=req_id,
                approved=is_approved,
                feedback=fb,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Update legacy session state if exists
    if session_id in sessions:
        sessions[session_id].pending_approval = None
        sessions[session_id].approval_response = {
            "decision": "approve" if is_approved else "deny",
            "feedback": fb,
        }
        sessions[session_id].approval_event.set()
        sessions[session_id].last_activity = datetime.now()

    return ApprovalResponseSchema(
        status="ok",
        request_id=req_id,
        decision="approved" if is_approved else "denied",
    )


@app.get("/sessions/{session_id}/events")
@limiter.limit(RateLimits.EVENTS)
async def get_events(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> EventSourceResponse:
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
@limiter.limit(RateLimits.GET_SESSION)
async def get_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> dict:
    """Get session state."""
    # Check in legacy sessions first (for backward compatibility)
    if session_id in sessions:
        return _session_to_dict(sessions[session_id])

    # Check in session manager
    if session_manager.has_session(session_id):
        return session_manager.get_session_info(session_id)

    raise HTTPException(status_code=404, detail="Session not found")


@app.delete("/sessions/{session_id}", response_model=CloseSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def close_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CloseSessionResponse:
    """Close session and release resources."""
    # Check if session exists in either store
    if not session_manager.has_session(session_id) and session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    # Close in session manager
    if session_manager.has_session(session_id):
        try:
            await session_manager.close_session(session_id)
        except Exception as e:
            logger.exception(f"Error closing session in manager: {e}")

    # Close in legacy sessions if exists
    if session_id in sessions:
        session = sessions[session_id]

        # Notify all connected clients
        await _broadcast_event(
            session,
            {"event": "SessionClosed", "data": json.dumps({"session_id": session_id})},
        )

        del sessions[session_id]

    logger.info(f"Closed session: {session_id}")
    return CloseSessionResponse(status="closed", session_id=session_id)


# Global approval handler for integration with agent loop (legacy)
async def wait_for_approval(
    session_id: str,
    approval_req: ApprovalRequest,
) -> ApprovalResponse:
    """Wait for approval response from HTTP clients.

    This function is called by the agent loop when it needs approval.
    It will block until the user responds via the /approve endpoint
    or the timeout expires.
    """
    # Use session manager if available
    if session_manager.has_session(session_id):
        # Submit through session manager
        # This is handled by the client calling /approve
        pass

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
            "tool_name": approval_req.tool_call.tool_name
            if approval_req.tool_call
            else "",
            "arguments": approval_req.tool_call.arguments
            if approval_req.tool_call
            else {},
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
