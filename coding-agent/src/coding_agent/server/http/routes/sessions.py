"""Session create, runtime-config, list, get, and close routes."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request

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
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    CloseSessionResponse,
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
    _codex_account_not_connected_exception,
    _get_visible_session,
    _key_error_detail,
)
from coding_agent.server.http.events import _broadcast_event
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
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionResponse:
    """Create new session with AgentLoop integration."""
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
    """Update the session runtime provider/model/base_url/thinking/approval config.

    Field semantics are three-state: omitted = leave unchanged, explicit
    null = reset to default (base_url only; null model/provider/thinking/approval
    are rejected with 422), value = set.

    Applies changes next turn. The session's tape and history are preserved.
    Returns 409 if a turn is currently in progress.
    Returns 400 if no config fields are provided.
    """
    provided = body.model_fields_set
    if not provided:
        raise HTTPException(
            status_code=400,
            detail="At least one of model, provider, base_url, thinking, or approval must be provided",
        )

    try:
        session = await _bindings.module().session_manager.get_session_async(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if getattr(session, "turn_in_progress", False):
        raise HTTPException(status_code=409, detail="Turn already in progress")

    no_rebuild_update = not (provided & {"model", "provider", "base_url"})
    try:
        if no_rebuild_update:
            updated_session = session
        else:
            updated_session = (
                await _bindings.module().session_manager.replace_session_runtime_config(
                    session_id,
                    model_name=body.model,
                    provider_name=body.provider,
                    base_url=body.base_url if "base_url" in provided else UNSET,
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


@router.get("/sessions", response_model=SessionListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_sessions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionListResponse:
    del request
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
