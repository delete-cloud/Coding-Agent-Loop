"""Session create, runtime-config, list, get, and close routes."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from agentkit.errors import PluginError
from coding_agent.approval import ApprovalPolicy
from coding_agent.plugins.approval import ApprovalPlugin
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
    verify_api_key,
)
from coding_agent.runs import (
    UNSET,
    CloudWorkspaceRef,
    UnsetType,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.events.connected_chat import ChatCursorError, decode_chat_cursor
from coding_agent.server.schemas import (
    CloseSessionResponse,
    ConnectedChatErrorResponse,
    ConnectedChatSnapshotSchema,
    CreateSessionRequest,
    RuntimeConfigUpdateRequest,
    RuntimeConfigUpdateResponse,
    RuntimeRunListResponse,
    SessionListResponse,
    SessionResponse,
    SessionSummaryResponse,
)
from coding_agent.server.session_manager import Session
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.config import (
    _config_int_or_default,
    _optional_config_string,
)
from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
    _chat_error,
    _chat_session_not_found_error,
    _connected_chat_auth,
    _codex_account_not_connected_exception,
    _get_visible_session,
    _key_error_detail,
)
from coding_agent.server.http.events import (
    _broadcast_event,
    _chat_stream_openapi_response,
    _chat_stream_sse_frames,
)
from coding_agent.server.http.routes.runtime import _runtime_run_response
from coding_agent.server.http.session_target import (
    _http_exception_detail,
    _provisioned_run_target_from_request,
    _session_origin_from_request,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.post("/sessions", response_model=SessionResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def create_session(
    request: Request,
    body: CreateSessionRequest | None = None,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> SessionResponse | JSONResponse:
    """Create new session with AgentLoop integration."""
    auth_context = await _connected_chat_auth(x_api_key, authorization)
    if isinstance(auth_context, JSONResponse):
        return auth_context

    # Use defaults if no body provided
    repo_path = None if body is None or body.repo_path is None else Path(body.repo_path)
    approval_policy_str = body.approval_policy if body else "auto"
    agent_defaults = _bindings.module()._load_agent_runtime_defaults()

    # Map string to ApprovalPolicy enum
    approval_policy_map = {
        "yolo": ApprovalPolicy.YOLO,
        "interactive": ApprovalPolicy.INTERACTIVE,
        "auto": ApprovalPolicy.AUTO,
    }
    approval_policy = approval_policy_map.get(approval_policy_str, ApprovalPolicy.AUTO)
    provisioned_workspace: CloudWorkspaceRef | None = None

    # Multi-account codex keys must reference a connected account up front;
    # fail at creation instead of mid-turn (plain `codex` keeps lazy behavior).
    if (
        body is not None
        and body.provider is not None
        and body.provider.startswith("codex:")
        and not await _bindings.module().asyncio.to_thread(
            _bindings.module().codex_oauth_flow_manager.has_account, body.provider
        )
    ):
        raise _codex_account_not_connected_exception(body.provider)

    try:
        default_run_target = _provisioned_run_target_from_request(
            body,
            auth_context=auth_context,
        )
        if (
            body is not None
            and body.workspace_source is not None
            and default_run_target is not None
            and isinstance(default_run_target.workspace, CloudWorkspaceRef)
        ):
            provisioned_workspace = default_run_target.workspace
        session_id = await _bindings.module().session_manager.create_session(
            repo_path=repo_path,
            origin=_session_origin_from_request(
                body,
                default_run_target,
                auth_context,
            ),
            default_run_target=default_run_target,
            approval_policy=approval_policy,
            provider_name=(
                body.provider
                if body and body.provider is not None
                else _optional_config_string(agent_defaults.get("provider"))
            ),
            model_name=(
                body.model
                if body and body.model is not None
                else _optional_config_string(agent_defaults.get("model"))
            ),
            base_url=body.base_url if body else None,
            api_key=(
                body.api_key.get_secret_value()
                if body is not None and body.api_key is not None
                else None
            ),
            max_steps=(
                body.max_steps
                if body and body.max_steps is not None
                else _config_int_or_default(agent_defaults.get("max_steps"), 30)
            ),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        if provisioned_workspace is not None:
            try:
                await _bindings.module().asyncio.to_thread(
                    _bindings.module()._cleanup_provisioned_cloud_binding,
                    provisioned_workspace,
                )
            except Exception:
                logger.exception("Failed to roll back provisioned cloud workspace")
        raise
    except Exception as exc:
        if provisioned_workspace is not None:
            try:
                await _bindings.module().asyncio.to_thread(
                    _bindings.module()._cleanup_provisioned_cloud_binding,
                    provisioned_workspace,
                )
            except Exception:
                logger.exception("Failed to roll back provisioned cloud workspace")
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, RuntimeError):
            raise HTTPException(
                status_code=500, detail=_http_exception_detail(exc)
            ) from exc
        if isinstance(exc, (ValueError, TypeError)):
            raise HTTPException(
                status_code=400, detail=_http_exception_detail(exc)
            ) from exc
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc

    logger.info(f"Created session: {session_id}")
    return SessionResponse(session_id=session_id)


def _live_approval_plugin(session: Any) -> ApprovalPlugin | None:
    pipeline = getattr(session, "runtime_pipeline", None)
    if pipeline is None:
        return None

    registry = getattr(pipeline, "_registry", None)
    if registry is None:
        raise RuntimeError("session runtime pipeline has no plugin registry")

    try:
        plugin = registry.get("approval")
    except PluginError:
        plugin = None

    if isinstance(plugin, ApprovalPlugin):
        return plugin

    plugins = getattr(registry, "_plugins", None)
    if isinstance(plugins, Mapping):
        for candidate in plugins.values():
            if isinstance(candidate, ApprovalPlugin):
                return candidate
        for candidate in plugins.values():
            if getattr(candidate, "state_key", None) == "approval":
                if isinstance(candidate, ApprovalPlugin):
                    return candidate
                raise RuntimeError("approval plugin is not an ApprovalPlugin")

    raise RuntimeError("session runtime pipeline is missing approval plugin")


@router.post(
    "/sessions/{session_id}/runtime-config", response_model=RuntimeConfigUpdateResponse
)
@router.patch(
    "/sessions/{session_id}/runtime-config", response_model=RuntimeConfigUpdateResponse
)
@limiter.limit(RateLimits.CREATE_SESSION)
async def update_runtime_config(
    request: Request,
    session_id: str,
    body: RuntimeConfigUpdateRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeConfigUpdateResponse:
    """Update the session runtime provider/model/base_url/key/thinking/approval config.

    Field semantics are three-state: omitted = leave unchanged, explicit
    null = reset to default (base_url and api_key), value = set.
    model/provider/thinking/approval have no meaningful reset target, so
    explicit null values for those fields are rejected with 422.

    Applies changes next turn. The session's tape and history are preserved.
    Returns 409 if a turn is currently in progress.
    Returns 400 if no config fields are provided.
    """
    provided = body.model_fields_set
    if not provided:
        raise HTTPException(
            status_code=400,
            detail="At least one of model, provider, base_url, api_key, thinking, or approval must be provided",
        )

    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if getattr(session, "turn_in_progress", False):
        raise HTTPException(status_code=409, detail="Turn already in progress")

    resolved_provider_name = (
        body.provider if "provider" in provided else session.provider_name
    )
    api_key_is_ignored = resolved_provider_name == "codex" or (
        resolved_provider_name is not None
        and resolved_provider_name.startswith("codex:")
    )
    no_rebuild_update = not (
        provided & {"model", "provider", "base_url"}
        or ("api_key" in provided and not api_key_is_ignored)
    )
    request_api_key: str | None | UnsetType
    if api_key_is_ignored:
        request_api_key = None
    elif "api_key" in provided:
        request_api_key = (
            body.api_key.get_secret_value() if body.api_key is not None else None
        )
        request_api_key = request_api_key or None
    else:
        request_api_key = UNSET
    try:
        if no_rebuild_update:
            updated_session = session
        else:
            runtime_config_changes: dict[str, Any] = {
                "model_name": body.model,
                "provider_name": body.provider,
                "base_url": body.base_url if "base_url" in provided else UNSET,
            }
            if not isinstance(request_api_key, UnsetType):
                runtime_config_changes["api_key"] = request_api_key
            updated_session = (
                await _bindings.module().session_manager.replace_session_runtime_config(
                    session_id,
                    **runtime_config_changes,
                )
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(
                status_code=409, detail="Turn already in progress"
            ) from exc
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc

    # Apply thinking and approval after the fallible rebuild. For rebuilds this
    # targets the freshly attached runtime; for approval/thinking-only updates it
    # keeps the existing no-rebuild fast path.
    if body.thinking is not None:
        updated_session.thinking_config["enabled"] = body.thinking.enabled
        updated_session.thinking_config["effort"] = body.thinking.effort
    if body.approval is not None:
        approval_policy = ApprovalPolicy(body.approval)
        plugin = _live_approval_plugin(updated_session)
        if plugin is not None:
            plugin.set_policy(approval_policy)
        updated_session.approval_policy = approval_policy
        await _bindings.module().session_manager._persist_session_async(updated_session)

    return RuntimeConfigUpdateResponse(
        session_id=session_id,
        provider_name=getattr(updated_session, "provider_name", None),
        model_name=getattr(updated_session, "model_name", None),
        base_url=getattr(updated_session, "base_url", None),
    )


_CHAT_ERROR_RESPONSES = {
    400: {"model": ConnectedChatErrorResponse},
    401: {"model": ConnectedChatErrorResponse},
    404: {"model": ConnectedChatErrorResponse},
    409: {"model": ConnectedChatErrorResponse},
    410: {"model": ConnectedChatErrorResponse},
}


@router.get(
    "/sessions/{session_id}/chat-events",
    response_model=ConnectedChatSnapshotSchema,
    responses=_CHAT_ERROR_RESPONSES,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_chat_events(
    request: Request,
    session_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> ConnectedChatSnapshotSchema | JSONResponse:
    del request
    auth = await _connected_chat_auth(x_api_key, authorization)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        manager = _bindings.module().session_manager
        try:
            session = await manager.get_session_async(session_id)
        except KeyError:
            return _chat_session_not_found_error()
        if not _auth_context_can_access_session(auth, session):
            return _chat_session_not_found_error()
        snapshot_method = getattr(manager, "snapshot_chat_events", None)
        if callable(snapshot_method):
            snapshot = await snapshot_method(session_id, cursor=cursor, limit=limit)
        else:
            store = manager._authoritative_store()
            if store is None:
                raise RuntimeError("durable authoritative store is not configured")
            decoded_cursor = None
            if cursor is not None:
                fact = await store.load_session_fact_source(session_id)
                decoded_cursor = decode_chat_cursor(
                    cursor,
                    expected_session_id=session_id,
                    fact_state=fact.state,
                )
            snapshot = await store.snapshot_chat_events(
                session_id, decoded_cursor, limit
            )
    except KeyError:
        return _chat_error(404, "session_not_found", "Session not found")
    except ChatCursorError as exc:
        return _chat_error(
            exc.status,
            exc.code,
            exc.code,
            replay_required=exc.replay_required,
        )
    return ConnectedChatSnapshotSchema.model_validate(snapshot, from_attributes=True)


@router.get(
    "/sessions/{session_id}/chat-events/follow",
    response_model=None,
    responses={
        200: _chat_stream_openapi_response(
            "Passive connected chat event follow stream"
        ),
        **_CHAT_ERROR_RESPONSES,
    },
)
@limiter.limit(RateLimits.GET_SESSION)
async def follow_chat_events(
    request: Request,
    session_id: str,
    cursor: str | None = Query(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> EventSourceResponse | JSONResponse:
    """Passively follow canonical chat events after an optional cursor.

    The stream encodes ChatEvent items as chat_event frames and StreamControl
    items as stream_control frames; it never settles a run.
    """
    del request
    auth = await _connected_chat_auth(x_api_key, authorization)
    if isinstance(auth, JSONResponse):
        return auth
    manager = _bindings.module().session_manager
    try:
        session = await manager.get_session_async(session_id)
    except KeyError:
        return _chat_session_not_found_error()
    if not _auth_context_can_access_session(auth, session):
        return _chat_session_not_found_error()
    follow_method = getattr(manager, "follow_chat_events", None)
    if not callable(follow_method):
        raise RuntimeError("session manager must provide follow_chat_events")
    try:
        stream = follow_method(session_id, cursor=cursor)
        if inspect.isawaitable(stream):
            stream = await stream
    except KeyError:
        return _chat_session_not_found_error()
    except ChatCursorError as exc:
        return _chat_error(
            exc.status,
            exc.code,
            exc.code,
            replay_required=exc.replay_required,
        )
    return EventSourceResponse(
        _chat_stream_sse_frames(stream),
        media_type="text/event-stream",
    )


@router.get("/sessions", response_model=SessionListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_sessions(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
) -> SessionListResponse | JSONResponse:
    del request
    auth_context = await _connected_chat_auth(x_api_key, authorization)
    if isinstance(auth_context, JSONResponse):
        return auth_context
    summaries: list[SessionSummaryResponse] = []
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        summaries.append(await _session_summary_response(session))
    return SessionListResponse(sessions=summaries)


@router.get("/sessions/{session_id}", response_model=SessionSummaryResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionSummaryResponse:
    """Get session state."""
    del request
    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")

    return await _session_summary_response(session)


async def _session_summary_response(session: Session) -> SessionSummaryResponse:
    payload = session.as_dict()
    payload.update(
        await _bindings.module().session_manager.session_resume_metadata(session.id)
    )
    payload["title"] = None
    return SessionSummaryResponse(**payload)


@router.get("/sessions/{session_id}/runs", response_model=RuntimeRunListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_session_runtime_runs(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeRunListResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        records = await _bindings.module().session_manager.list_active_runtime_runs(
            session_id
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime store not configured",
        ) from exc
    return RuntimeRunListResponse(
        session_id=session_id,
        runs=[_runtime_run_response(record) for record in records],
    )


@router.delete("/sessions/{session_id}", response_model=CloseSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def close_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CloseSessionResponse:
    """Close session and release resources."""
    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
        await _bindings.module().session_manager.close_session(session_id)
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


__all__ = [
    "_live_approval_plugin",
    "_session_summary_response",
    "close_session",
    "create_session",
    "get_session",
    "list_session_runtime_runs",
    "list_sessions",
    "update_runtime_config",
]
