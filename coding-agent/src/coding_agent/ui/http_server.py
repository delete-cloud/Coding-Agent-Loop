"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from coding_agent.approval import ApprovalPolicy
from coding_agent.ui.session_manager import Session, SessionManager
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

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120
SESSION_IDLE_TIMEOUT_MINUTES = 30
SessionState = Session


# Global session manager
session_manager = SessionManager()


class _SessionStoreView:
    def __contains__(self, session_id: str) -> bool:
        return session_manager.has_session(session_id)

    def __getitem__(self, session_id: str):
        return session_manager.get_session(session_id)

    def __setitem__(self, session_id: str, session: Session) -> None:
        if session.id != session_id:
            raise ValueError("session key must match session.id")
        session_manager.register_session(session)

    def __delitem__(self, session_id: str) -> None:
        session_manager.remove_session(session_id)

    def clear(self) -> None:
        session_manager.clear_sessions()

    def keys(self):
        return session_manager.list_sessions()

    def items(self):
        return [
            (session_id, session_manager.get_session(session_id))
            for session_id in session_manager.list_sessions()
        ]

    def values(self):
        return [
            session_manager.get_session(session_id)
            for session_id in session_manager.list_sessions()
        ]

    def get(self, session_id: str, default: Session | None = None) -> Session | None:
        if not session_manager.has_session(session_id):
            return default
        return session_manager.get_session(session_id)

    def __iter__(self):
        return iter(session_manager.list_sessions())

    def __len__(self) -> int:
        return len(session_manager.list_sessions())


sessions = _SessionStoreView()


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
    for session_id in list(session_manager.list_sessions()):
        await session_manager.close_session(session_id)

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


def _session_to_dict(session: Session) -> dict[str, Any]:
    """Convert session state to dictionary."""
    return {
        "id": session.id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "turn_in_progress": session.turn_in_progress,
        "pending_approval": session.pending_approval is not None,
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
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
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


async def _broadcast_event(session: Session, event: dict[str, str]) -> None:
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
            await session_manager.cleanup_idle_sessions(SESSION_IDLE_TIMEOUT_MINUTES)
        except Exception:
            logger.exception("Error during idle session cleanup")


async def stream_wire_messages(wire: LocalWire) -> AsyncIterator[dict[str, str]]:
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
    return HealthResponse(
        status="healthy", sessions=len(session_manager.list_sessions()), version="2.0.0"
    )


@app.post("/sessions", response_model=SessionResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def create_session(
    request: Request,
    body: CreateSessionRequest | None = None,
    api_key: str | None = Depends(verify_api_key),
) -> SessionResponse:
    """Create new session with AgentLoop integration."""
    # Use defaults if no body provided
    repo_path = None if body is None or body.repo_path is None else Path(body.repo_path)
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

    if not session_manager.has_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = session_manager.get_session(session_id)

    if session.turn_in_progress or (session.task and not session.task.done()):
        raise HTTPException(status_code=409, detail="Turn already in progress")

    session.turn_in_progress = True
    session.last_activity = datetime.now()

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate SSE events for the turn."""
        try:
            session.task = asyncio.create_task(
                session_manager.run_agent(session_id, prompt_text)
            )

            async for event in stream_wire_messages(session.wire):
                await _broadcast_event(session, event)
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
            await _broadcast_event(session, error_data)
            yield error_data
        finally:
            session.turn_in_progress = False
            session.last_activity = datetime.now()

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

    if not session_manager.has_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = session_manager.get_session(session_id)
    if session.pending_approval is None:
        raise HTTPException(status_code=400, detail="No pending approval request")
    if session.pending_approval.get("request_id") != req_id:
        raise HTTPException(status_code=400, detail="Request ID mismatch")

    try:
        await session_manager.submit_approval(
            session_id=session_id,
            request_id=req_id,
            approved=is_approved,
            feedback=fb,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
    if not session_manager.has_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = session_manager.get_session(session_id)
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    session.event_queues.append(queue)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
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
) -> dict[str, Any]:
    """Get session state."""
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
    if not session_manager.has_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = session_manager.get_session(session_id)
    await _broadcast_event(
        session,
        {"event": "SessionClosed", "data": json.dumps({"session_id": session_id})},
    )

    try:
        await session_manager.close_session(session_id)
    except Exception as e:
        logger.exception(f"Error closing session in manager: {e}")

    logger.info(f"Closed session: {session_id}")
    return CloseSessionResponse(status="closed", session_id=session_id)


async def wait_for_approval(
    session_id: str,
    approval_req: ApprovalRequest,
) -> ApprovalResponse:
    """Wait for approval response from HTTP clients.

    This function is called by the agent loop when it needs approval.
    It will block until the user responds via the /approve endpoint
    or the timeout expires.
    """
    if not session_manager.has_session(session_id):
        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Session not found",
        )

    session = session_manager.get_session(session_id)

    if session.turn_in_progress:
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

        event = _wire_message_to_event(approval_req)
        await _broadcast_event(session, event)

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
