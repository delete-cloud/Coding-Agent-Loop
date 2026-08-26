"""Session prompt, resume, approval, and SSE event routes."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from coding_agent.events.connected_chat import (
    ChatCommandAdmission,
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    TurnInProgressError,
)
from coding_agent.runs.lifecycle import RuntimeTurnSessionState
from coding_agent.runs.resume import resolve_resume_user_prompt
from coding_agent.server.auth import verify_api_key
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    ApprovalResponseSchema,
    ApproveRequest,
    ConnectedChatErrorResponse,
    PromptRequest,
    ResumeSessionRequest,
)
from coding_agent.server.session_manager import Session
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
    _chat_admission_error,
    _chat_error,
    _chat_session_not_found_error,
    _connected_chat_auth,
    _owner_conflict_http_exception,
)
from coding_agent.server.http.events import (
    _broadcast_event,
    _chat_stream_openapi_response,
    _chat_stream_sse_frames,
    _display_event_stream_transform,
    _wire_message_to_event,
)
from coding_agent.server.http.routes.workers import (
    _remote_loop_gone,
    _session_uses_retired_remote_loop,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()

_CHAT_STREAM_RESPONSES: dict[int, dict[str, Any]] = {
    200: _chat_stream_openapi_response("Connected chat command event stream"),
    401: {"model": ConnectedChatErrorResponse},
    404: {"model": ConnectedChatErrorResponse},
    409: {"model": ConnectedChatErrorResponse},
}


def _admitted_chat_stream_response(
    session_manager: Any,
    session_id: str,
    admission: ChatCommandAdmission,
) -> EventSourceResponse | None:
    """Stream canonical chat events for an admitted command when supported."""
    stream_method = getattr(session_manager, "stream_chat_command", None)
    if not callable(stream_method):
        return None
    return _bindings.module().EventSourceResponse(
        _chat_stream_sse_frames(stream_method(session_id, admission=admission)),
        media_type="text/event-stream",
    )


async def _settle_stream_disconnect(
    session_manager: object,
    *,
    session_id: str,
    run_id: str,
    owns_run: bool,
) -> None:
    if not owns_run:
        return
    capability = getattr(session_manager, "can_settle_root_run_authoritatively", None)
    if callable(capability) and not capability():
        return
    settle = getattr(session_manager, "settle_root_run", None)
    if not callable(settle):
        raise TypeError("session manager must provide settle_root_run")
    settlement = asyncio.create_task(
        settle(session_id, run_id=run_id, outcome="interrupted")
    )
    try:
        await asyncio.shield(settlement)
    except asyncio.CancelledError:
        await settlement
        raise


@router.post(
    "/sessions/{session_id}/prompt",
    response_model=None,
    responses=_CHAT_STREAM_RESPONSES,
)
@limiter.limit(RateLimits.SEND_PROMPT)
async def send_prompt(
    request: Request,
    session_id: str,
    body: PromptRequest | None = None,
    prompt: str | None = None,  # Backward compat: query param
    event_format: Literal["wire", "display"] = Query("wire"),
    api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> EventSourceResponse | JSONResponse:
    """Send message, returns SSE stream.

    Returns 409 if a turn is already in progress.
    Accepts prompt via JSON body (preferred) or query param (backward compat).
    Admission rejections on the connected-chat display path return a
    JSONResponse error envelope instead of the SSE stream.
    """
    auth_context = await _connected_chat_auth(api_key, authorization)
    if isinstance(auth_context, JSONResponse):
        return auth_context

    # Get prompt from body or query param (body takes precedence)
    prompt_text = body.prompt if body else prompt
    if not prompt_text:
        if body is not None and getattr(body, "command_id", None) is not None:
            return _chat_error(422, "prompt_required", "Prompt is required")
        raise HTTPException(status_code=422, detail="Prompt is required")
    session_manager = _bindings.module().session_manager
    try:
        visible_session = await session_manager.get_session_async(session_id)
    except KeyError:
        return _chat_session_not_found_error()
    if not _auth_context_can_access_session(auth_context, visible_session):
        return _chat_session_not_found_error()

    # A command_id identifies the connected-chat contract. The optional
    # event_format query remains only for backward-compatible legacy requests.
    connected_chat_request = body is not None and body.command_id is not None
    effective_event_format: Literal["wire", "display"] = (
        "display" if connected_chat_request else event_format
    )
    admission: ChatCommandAdmission | None = None
    if connected_chat_request:
        try:
            admission = await session_manager.admit_chat_command(
                session_id,
                prompt=prompt_text,
                command_id=body.command_id,
            )
        except (
            TurnInProgressError,
            ChatCommandConflictError,
            ResumeSourceUnsettledError,
        ) as exc:
            return _chat_admission_error(exc)
        except KeyError:
            return _chat_session_not_found_error()
        except SessionOwnershipConflictError as exc:
            raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    if admission is not None:
        canonical_stream = _admitted_chat_stream_response(
            session_manager, session_id, admission
        )
        if canonical_stream is not None:
            return canonical_stream
        # Admission already performed the checked turn gate; load the session
        # directly instead of re-running the legacy admission check.
        session = visible_session
        if _session_uses_retired_remote_loop(session):
            raise _remote_loop_gone()
    else:
        try:
            session = await session_manager.prepare_session_turn(session_id)
            if _session_uses_retired_remote_loop(session):
                raise _remote_loop_gone()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except SessionOwnershipConflictError as exc:
            raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
        except RuntimeError as exc:
            if str(exc) == "turn already in progress":
                raise HTTPException(
                    status_code=409, detail="Turn already in progress"
                ) from exc
            raise

    session.turn_in_progress = True
    session.last_activity = datetime.now(UTC)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate SSE events for the turn."""
        try:
            run_kwargs: dict[str, str] = {}
            if admission is not None:
                run_kwargs["run_id_override"] = admission.run_id
            session.task = asyncio.create_task(
                _bindings.module().session_manager.run_agent(
                    session_id, prompt_text, **run_kwargs
                )
            )

            async for event in _bindings.module().stream_wire_messages(
                session.wire, session.task
            ):
                await _broadcast_event(session, event)
                response_event = _prompt_stream_event_response(
                    session,
                    event,
                    event_format=effective_event_format,
                )
                if response_event is not None:
                    yield response_event

        except Exception as e:
            logger.exception("Error during turn")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(e),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ),
            }
            await _broadcast_event(session, error_data)
            yield error_data
        finally:
            task = session.task
            stream_task = asyncio.current_task()
            stream_cancelling = stream_task is not None and stream_task.cancelling() > 0
            task_cancelled_error: asyncio.CancelledError | None = None
            task_base_error: BaseException | None = None
            session_manager = _bindings.module().session_manager
            owns_unsettled_run = task is not None and not task.done()
            if owns_unsettled_run and session.current_turn_id is not None:
                await _settle_stream_disconnect(
                    session_manager,
                    session_id=session_id,
                    run_id=session.current_turn_id,
                    owns_run=True,
                )
            if task is not None:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError as exc:
                    task_cancelled_error = exc
                except Exception:
                    pass
                except BaseException as exc:
                    task_base_error = exc
            has_admission_state = (
                session.turn_in_progress or session.turn_status == "running"
            )
            finalize_needed = (task is not None and session.task is task) or (
                session.task is None and has_admission_state
            )
            if finalize_needed:
                session_manager = _bindings.module().session_manager
                turn_session_state = RuntimeTurnSessionState(
                    persist_session=session_manager._persist_session_async,
                    persist_finalize=session_manager._persist_turn_settled,
                )
                await asyncio.shield(
                    turn_session_state.finalize(
                        session,
                        current_task=task,
                        turn_finished=False,
                    )
                )
            if task_base_error is not None:
                raise task_base_error
            if stream_cancelling and task_cancelled_error is not None:
                raise task_cancelled_error

    # Return SSE stream from wire
    return _bindings.module().EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.post(
    "/sessions/{session_id}/resume",
    response_model=None,
    responses=_CHAT_STREAM_RESPONSES,
)
@limiter.limit(RateLimits.SEND_PROMPT)
async def resume_session(
    request: Request,
    session_id: str,
    body: ResumeSessionRequest | None = None,
    event_format: Literal["wire", "display"] = Query("wire"),
    api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> EventSourceResponse | JSONResponse:
    del request
    auth_context = await _connected_chat_auth(api_key, authorization)
    if isinstance(auth_context, JSONResponse):
        return auth_context

    session_manager = _bindings.module().session_manager
    prompt_text = None if body is None else body.prompt
    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError:
        return _chat_session_not_found_error()
    if not _auth_context_can_access_session(auth_context, session):
        return _chat_session_not_found_error()

    # A command_id identifies the connected-chat contract. The optional
    # event_format query remains only for backward-compatible legacy requests.
    connected_chat_request = body is not None and body.command_id is not None
    if connected_chat_request and not body.parent_run_id:
        return _chat_error(
            422,
            "parent_run_id_required",
            "Connected Resume requires parent_run_id",
        )
    effective_event_format: Literal["wire", "display"] = (
        "display" if connected_chat_request else event_format
    )
    admission: ChatCommandAdmission | None = None
    admit_method = getattr(session_manager, "admit_chat_command", None)
    if connected_chat_request and callable(admit_method):
        try:
            admission = await admit_method(
                session_id,
                prompt=resolve_resume_user_prompt(prompt_text),
                command_id=body.command_id,
                parent_run_id=body.parent_run_id,
            )
        except (
            TurnInProgressError,
            ChatCommandConflictError,
            ResumeSourceUnsettledError,
        ) as exc:
            return _chat_admission_error(exc)
        except KeyError:
            return _chat_session_not_found_error()
        except SessionOwnershipConflictError as exc:
            raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
        canonical_stream = _admitted_chat_stream_response(
            session_manager, session_id, admission
        )
        if canonical_stream is not None:
            return canonical_stream

    if _session_uses_retired_remote_loop(session):
        raise _remote_loop_gone()

    resume_reason = "user_resume" if body is None else body.resume_reason

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            session.task = asyncio.create_task(
                _bindings.module().session_manager.resume_session(
                    session_id,
                    prompt=prompt_text,
                    resume_reason=resume_reason,
                )
            )
            async for event in _bindings.module().stream_wire_messages(
                session.wire, session.task
            ):
                await _broadcast_event(session, event)
                response_event = _prompt_stream_event_response(
                    session,
                    event,
                    event_format=effective_event_format,
                )
                if response_event is not None:
                    yield response_event
        except Exception as exc:
            logger.exception("Error during session resume")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(exc),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ),
            }
            await _broadcast_event(session, error_data)
            yield error_data
        finally:
            task = session.task
            session_manager = _bindings.module().session_manager
            if (
                task is not None
                and not task.done()
                and session.current_turn_id is not None
            ):
                await _settle_stream_disconnect(
                    session_manager,
                    session_id=session_id,
                    run_id=session.current_turn_id,
                    owns_run=True,
                )
                task.cancel()
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                if session.task is task:
                    session.task = None
            session.turn_in_progress = False
            session.last_activity = datetime.now(UTC)

    return _bindings.module().EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


def _prompt_stream_event_response(
    session: Session,
    event: dict[str, str],
    *,
    event_format: Literal["wire", "display"],
) -> dict[str, str] | None:
    if event_format == "wire":
        return event
    if event.get("event") == "Error":
        return event
    return _display_event_stream_transform(session, event)


@router.post("/sessions/{session_id}/approve", response_model=ApprovalResponseSchema)
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

    if not await _bindings.module().session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        approval_response = (
            await _bindings.module().session_manager.submit_approval_response(
                session_id=session_id,
                request_id=req_id,
                approved=is_approved,
                feedback=fb,
                scope=resolved_scope,
            )
        )
        if approval_response is None:
            raise HTTPException(status_code=400, detail="No pending approval request")
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approval for session %s",
            session_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return ApprovalResponseSchema(
        status="ok",
        request_id=req_id,
        decision="approved" if approval_response.approved else "denied",
    )


async def wait_for_approval(
    session_id: str,
    approval_req: ApprovalRequest,
) -> ApprovalResponse:
    """Wait for approval response from HTTP clients.

    This function is called by the agent loop when it needs approval.
    It will block until the user responds via the /approve endpoint
    or the timeout expires.
    """
    if not await _bindings.module().session_manager.has_session_async(session_id):
        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Session not found",
        )

    session = await _bindings.module().session_manager.get_session_async(session_id)
    event = _wire_message_to_event(approval_req)
    await _broadcast_event(session, event)
    response = await _bindings.module().session_manager.wait_for_http_approval(
        session_id=session_id,
        approval_req=approval_req,
        timeout_seconds=_bindings.module().APPROVAL_TIMEOUT_SECONDS,
    )
    if not response.approved and response.feedback == "Approval timeout or error":
        timeout_event = {
            "event": "ApprovalTimeout",
            "data": json.dumps({"request_id": approval_req.request_id}),
        }
        await _broadcast_event(session, timeout_event)
    return response


__all__ = [
    "_prompt_stream_event_response",
    "approve_request",
    "resume_session",
    "send_prompt",
    "wait_for_approval",
]
