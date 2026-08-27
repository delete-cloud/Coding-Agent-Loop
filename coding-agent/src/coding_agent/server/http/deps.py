"""HTTP auth helpers, owner conflicts, and session visibility."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse

from coding_agent.events.connected_chat import (
    ChatCommandConflictError,
    ResumeSourceUnsettledError,
    TurnInProgressError,
)
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.session_manager import Session
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _safe_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _codex_account_not_connected_exception(provider_key: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=(
            f"codex account not connected: {provider_key}. "
            "Connect via POST /oauth/codex/start first."
        ),
    )


def _key_error_detail(exc: KeyError) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def _owner_conflict_http_exception(
    exc: SessionOwnershipConflictError,
    *,
    session_id: str,
) -> HTTPException:
    if exc.reason == SessionOwnershipConflictReason.MISSING_OWNER:
        return HTTPException(
            status_code=404,
            detail=f"Session not found: {session_id}",
        )
    return HTTPException(status_code=409, detail=str(exc))


def _http_metrics_route_label(route_label: str) -> str:
    if route_label == "/console/topics/{topic_id}":
        return "/console/topics/detail"
    return route_label


def _session_to_dict(session: Session) -> dict[str, Any]:
    """Convert session state to dictionary."""
    return session.as_dict()


def _session_owner_label(session: Session) -> str | None:
    origin = session.origin
    if origin is None:
        return None
    owner_label = origin.get("owner_label")
    return owner_label if isinstance(owner_label, str) and owner_label else None


def _auth_context_can_access_session(
    auth_context: AuthContext | None,
    session: Session,
) -> bool:
    if auth_context is None:
        return True
    if auth_context.scope == "admin":
        return True
    return _session_owner_label(session) == auth_context.owner_label


def _normalize_direct_auth_context(
    auth_context: AuthContext | DependsParam | None,
) -> AuthContext | None:
    if isinstance(auth_context, DependsParam):
        return None
    return auth_context


def _require_admin_context(auth_context: AuthContext | None) -> None:
    if auth_context is None:
        return
    if auth_context.scope != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")


async def _connected_chat_auth(
    x_api_key: str | None,
    authorization: str | None,
) -> AuthContext | JSONResponse | None:
    try:
        return await auth_context_from_headers(x_api_key, authorization)
    except HTTPException as exc:
        if exc.status_code == 401:
            return _chat_error(
                401,
                "credentials_required",
                "Authentication credentials are required",
            )
        raise


def _chat_error(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    replay_required: bool | None = None,
) -> JSONResponse:
    detail: dict[str, object] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if replay_required is not None:
        detail["replay_required"] = replay_required
    return JSONResponse(status_code=status, content={"error": detail})


_CHAT_ADMISSION_ERRORS: tuple[tuple[type[Exception], str, str, bool], ...] = (
    (
        TurnInProgressError,
        "turn_in_progress",
        "A root turn is already active",
        True,
    ),
    (
        ChatCommandConflictError,
        "command_conflict",
        "Command ID was reused with different input",
        False,
    ),
    (
        ResumeSourceUnsettledError,
        "resume_source_unsettled",
        "The source run has not durably settled",
        True,
    ),
)


def _chat_admission_error(exc: Exception) -> JSONResponse:
    """Map a checked chat admission failure to its contract error envelope."""
    for error_type, code, message, retryable in _CHAT_ADMISSION_ERRORS:
        if isinstance(exc, error_type):
            return _chat_error(409, code, message, retryable=retryable)
    raise TypeError(f"unmapped chat admission error: {type(exc).__name__}")


def _chat_session_not_found_error() -> JSONResponse:
    return _chat_error(404, "session_not_found", "Session not found")


async def _get_visible_session(
    session_id: str,
    auth_context: AuthContext | None,
) -> Session:
    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")
    return session


__all__ = [
    "_safe_dict",
    "_auth_context_can_access_session",
    "_chat_admission_error",
    "_chat_error",
    "_connected_chat_auth",
    "_chat_session_not_found_error",
    "_codex_account_not_connected_exception",
    "_get_visible_session",
    "_http_metrics_route_label",
    "_key_error_detail",
    "_normalize_direct_auth_context",
    "_owner_conflict_http_exception",
    "_require_admin_context",
    "_session_owner_label",
    "_session_to_dict",
]
