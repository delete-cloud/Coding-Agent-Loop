"""Session SSE event streams and turn-cancel routes."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
    verify_api_key,
)
from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME
from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
    _get_visible_session,
    _key_error_detail,
    _normalize_direct_auth_context,
    _owner_conflict_http_exception,
)
from coding_agent.server.http.events import (
    _broadcast_event,
    _display_event_stream_transform,
    _legacy_event_stream_transform,
    _owned_session_event_generator,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import CancelSessionResponse
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get("/sessions/{session_id}/events")
@limiter.limit(RateLimits.EVENTS)
async def get_events(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> EventSourceResponse:
    """Persistent SSE event stream (fan-out supported)."""
    del request, api_key
    auth_context = _normalize_direct_auth_context(auth_context)
    _ = await _get_visible_session(session_id, auth_context)

    try:
        await _bindings.module().session_manager.authorize_event_stream(session_id)
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    try:
        await _bindings.module().session_manager.register_owned_event_queue_async(
            session_id, queue
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    return _bindings.module().EventSourceResponse(
        _owned_session_event_generator(
            session_id,
            queue,
            _legacy_event_stream_transform,
        )
    )


@router.get("/sessions/{session_id}/display-events")
@limiter.limit(RateLimits.EVENTS)
async def get_session_display_events(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> EventSourceResponse:
    """Persistent SSE stream of projected user-facing display events."""
    del request, api_key
    auth_context = _normalize_direct_auth_context(auth_context)
    session = await _get_visible_session(session_id, auth_context)

    try:
        await _bindings.module().session_manager.authorize_event_stream(session_id)
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    try:
        await _bindings.module().session_manager.register_owned_event_queue_async(
            session_id, queue
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    return _bindings.module().EventSourceResponse(
        _owned_session_event_generator(
            session_id,
            queue,
            lambda event: _display_event_stream_transform(session, event),
        )
    )


@router.post("/sessions/{session_id}/cancel", response_model=CancelSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def cancel_session_turn(
    request: Request,
    response: Response,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> CancelSessionResponse:
    del request
    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = await _bindings.module().session_manager.cancel_session_turn(
            session_id
        )
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result.status == "cancelling":
        response.status_code = 202
        await _broadcast_event(
            session,
            {
                "event": "TurnCancelling",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "turn_id": result.turn_id,
                    }
                ),
            },
        )

    return CancelSessionResponse(
        session_id=result.session_id,
        turn_id=result.turn_id,
        status=result.status,
    )


__all__ = [
    "cancel_session_turn",
    "get_events",
    "get_session_display_events",
]
