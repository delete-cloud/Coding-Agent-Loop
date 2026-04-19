"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agentkit.config.loader import load_config as load_agent_toml
from agentkit.errors import ConfigError
from coding_agent.approval import ApprovalPolicy
from coding_agent.ui.execution_binding import LocalExecutionBinding
from coding_agent.ui.session_manager import Session, SessionManager
from coding_agent.ui.schemas import (
    PromptRequest,
    CreateSessionRequest,
    ApproveRequest,
    CheckpointCaptureRequest,
    SessionResponse,
    CheckpointListResponse,
    CheckpointMetadataResponse,
    CheckpointRestoreResponse,
    ApprovalResponseSchema,
    CloseSessionResponse,
    HealthResponse,
    ReadinessResponse,
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
from coding_agent.wire.protocol import ThinkingDelta, TurnStatusDelta

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120
SESSION_IDLE_TIMEOUT_MINUTES = 30


def _load_storage_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent / "agent.toml"
    try:
        return cast(
            dict[str, Any], load_agent_toml(config_path).extra.get("storage", {})
        )
    except (ConfigError, OSError) as exc:
        if isinstance(exc, ConfigError):
            detail = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
            if not detail.startswith("config file not found:"):
                raise
        logger.warning(
            "Unable to load storage config from %s; using defaults",
            config_path,
            exc_info=True,
        )
        return {}


def _build_session_manager() -> SessionManager:
    return SessionManager(storage_config=_load_storage_config())


# Global session manager
session_manager = _build_session_manager()


def _key_error_detail(exc: KeyError) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


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
    try:
        for session_id in await session_manager.list_sessions_async():
            try:
                await session_manager.shutdown_session_runtime(session_id)
            except Exception:
                logger.warning(
                    "Failed to shut down runtime for session %s during server shutdown",
                    session_id,
                    exc_info=True,
                )
    finally:
        await session_manager.close()

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
    return session.as_dict()


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
    active_queues: list[asyncio.Queue[dict[str, str]]] = []
    full_pruned_count = 0
    failed_pruned_count = 0

    for queue in session.event_queues:
        try:
            queue.put_nowait(event)
            active_queues.append(queue)
        except asyncio.QueueFull:
            full_pruned_count += 1
        except Exception:
            failed_pruned_count += 1
            logger.debug("Dropping closed event queue", exc_info=True)

    session.event_queues = active_queues

    if full_pruned_count:
        logger.info(
            "Pruned %d full event queue(s) for session %s",
            full_pruned_count,
            session.id,
        )
    if failed_pruned_count:
        logger.info(
            "Pruned %d failed event queue(s) for session %s",
            failed_pruned_count,
            session.id,
        )


async def _cleanup_event_queue_on_disconnect(
    session_id: str,
    queue: asyncio.Queue[dict[str, str]],
) -> None:
    try:
        await asyncio.shield(
            session_manager.remove_event_queue_async(session_id, queue)
        )
    except KeyError:
        logger.debug(
            "Event queue cleanup skipped for already-removed session %s",
            session_id,
            exc_info=True,
        )


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

            if isinstance(msg, TurnEnd) and not msg.agent_id:
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


@app.get("/healthz", response_model=HealthResponse)
@limiter.limit(RateLimits.HEALTH)
async def liveness_check(request: Request) -> HealthResponse:
    return HealthResponse(
        status="healthy",
        sessions=await session_manager.count_sessions_async(),
        version="2.0.0",
    )


@app.get("/readyz", response_model=ReadinessResponse)
@limiter.limit(RateLimits.HEALTH)
async def readiness_check(request: Request, response: Response) -> ReadinessResponse:
    try:
        session_store_ok = bool(await session_manager.check_health_async())
    except Exception:
        logger.exception("Session store readiness check failed")
        session_store_ok = False

    try:
        rate_limiter_ok = bool(limiter._storage.check())
    except Exception:
        logger.exception("Rate limiter readiness check failed")
        rate_limiter_ok = False

    checks = {
        "session_store": "ok" if session_store_ok else "error",
        "rate_limiter": "ok" if rate_limiter_ok else "error",
    }
    ready = session_store_ok and rate_limiter_ok
    if not ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        checks=checks,
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

    session = await session_manager.get_session_async(session_id)
    expected_workspace_root = (
        str(repo_path.resolve()) if repo_path is not None else str(Path.cwd().resolve())
    )
    if (
        not isinstance(session.execution_binding, LocalExecutionBinding)
        or session.execution_binding.workspace_root != expected_workspace_root
    ):
        session.execution_binding = LocalExecutionBinding(
            workspace_root=expected_workspace_root
        )
        session_manager.register_session(session)

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

    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = await session_manager.get_session_async(session_id)

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
            if session.task is not None:
                try:
                    await session.task
                except Exception:
                    pass
                session.task = None
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
    scope: str | None = None,  # Backward compat: query param
    api_key: str | None = Depends(verify_api_key),
) -> ApprovalResponseSchema:
    """Respond to approval request.

    Accepts parameters via JSON body (preferred) or query params (backward compat).
    """
    # Get values from body or query params (body takes precedence)
    req_id = body.request_id if body else request_id
    is_approved = body.approved if body else approved
    fb = body.feedback if body else feedback
    resolved_scope = cast(
        Literal["once", "session"],
        body.scope if body else (scope or "once"),
    )

    if resolved_scope not in {"once", "session"}:
        raise HTTPException(status_code=422, detail="scope must be 'once' or 'session'")

    if req_id is None:
        raise HTTPException(status_code=422, detail="request_id is required")
    if is_approved is None:
        raise HTTPException(status_code=422, detail="approved is required")

    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = await session_manager.get_session_async(session_id)
    if session.approval_store.get_request(req_id) is None:
        raise HTTPException(status_code=400, detail="No pending approval request")

    try:
        success = await session_manager.submit_approval(
            session_id=session_id,
            request_id=req_id,
            approved=is_approved,
            feedback=fb,
            scope=resolved_scope,
        )
        if not success:
            raise HTTPException(status_code=400, detail="No pending approval request")
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
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session = await session_manager.get_session_async(session_id)
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    await session_manager.add_event_queue_async(session_id, queue)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate events from queue."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event
                    if event.get("event") == "SessionClosed":
                        break
                except asyncio.TimeoutError:
                    if not await session_manager.has_session_async(session_id):
                        break
                    try:
                        if not await session_manager.has_event_queue_async(
                            session_id, queue
                        ):
                            break
                    except KeyError:
                        break
                    # Send keepalive
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            # Client disconnected
            raise
        finally:
            await _cleanup_event_queue_on_disconnect(session_id, queue)

    return EventSourceResponse(event_generator())


@app.get("/sessions/{session_id}")
@limiter.limit(RateLimits.GET_SESSION)
async def get_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> dict[str, Any]:
    """Get session state."""
    if await session_manager.has_session_async(session_id):
        return await session_manager.get_session_info_async(session_id)

    raise HTTPException(status_code=404, detail="Session not found")


@app.post(
    "/sessions/{session_id}/checkpoints",
    response_model=CheckpointMetadataResponse,
)
@limiter.limit(RateLimits.CAPTURE_CHECKPOINT)
async def capture_checkpoint(
    request: Request,
    session_id: str,
    body: CheckpointCaptureRequest | None = None,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointMetadataResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        checkpoint = await session_manager.capture_checkpoint(
            session_id,
            label=body.label if body else None,
            extra=None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CheckpointMetadataResponse(
        checkpoint_id=checkpoint.checkpoint_id,
        tape_id=checkpoint.tape_id,
        session_id=checkpoint.session_id,
        entry_count=checkpoint.entry_count,
        window_start=checkpoint.window_start,
        created_at=checkpoint.created_at,
        label=checkpoint.label,
    )


@app.get("/sessions/{session_id}/checkpoints", response_model=CheckpointListResponse)
@limiter.limit(RateLimits.LIST_CHECKPOINTS)
async def list_checkpoints(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointListResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    checkpoints = await session_manager.list_checkpoints(session_id)
    return CheckpointListResponse(
        checkpoints=[
            CheckpointMetadataResponse(
                checkpoint_id=checkpoint.checkpoint_id,
                tape_id=checkpoint.tape_id,
                session_id=checkpoint.session_id,
                entry_count=checkpoint.entry_count,
                window_start=checkpoint.window_start,
                created_at=checkpoint.created_at,
                label=checkpoint.label,
            )
            for checkpoint in checkpoints
        ]
    )


@app.post(
    "/sessions/{session_id}/checkpoints/{checkpoint_id}/restore",
    response_model=CheckpointRestoreResponse,
)
@limiter.limit(RateLimits.RESTORE_CHECKPOINT)
async def restore_checkpoint(
    request: Request,
    session_id: str,
    checkpoint_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointRestoreResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        await session_manager.restore_checkpoint(session_id, checkpoint_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CheckpointRestoreResponse(
        status="restored",
        session_id=session_id,
        checkpoint_id=checkpoint_id,
    )


@app.delete("/sessions/{session_id}", response_model=CloseSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def close_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CloseSessionResponse:
    """Close session and release resources."""
    try:
        session = await session_manager.get_session_async(session_id)
        await session_manager.close_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error while closing session %s", session_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    await _broadcast_event(
        session,
        {"event": "SessionClosed", "data": json.dumps({"session_id": session_id})},
    )

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
    if not await session_manager.has_session_async(session_id):
        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Session not found",
        )

    session = await session_manager.get_session_async(session_id)
    event = _wire_message_to_event(approval_req)
    await _broadcast_event(session, event)
    response = await session_manager.wait_for_http_approval(
        session_id=session_id,
        approval_req=approval_req,
        timeout_seconds=APPROVAL_TIMEOUT_SECONDS,
    )
    if not response.approved and response.feedback == "Approval timeout or error":
        timeout_event = {
            "event": "ApprovalTimeout",
            "data": json.dumps({"request_id": approval_req.request_id}),
        }
        await _broadcast_event(session, timeout_event)
    return response
