"""Workspace inventory, retention, GC, and archive-by-id routes."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Depends, HTTPException, Request, Response

from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    WorkspaceArchiveManifestResponse,
    WorkspaceArchiveResponse,
    WorkspaceCleanupResponse,
    WorkspaceGcResponse,
    WorkspaceListResponse,
    WorkspaceRetentionRequest,
    WorkspaceRetentionResponse,
    WorkspaceSummarySchema,
    WorkspaceUnpinRequest,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import _require_admin_context
from coding_agent.server.http.lifecycle import (
    _active_cloud_workspace_ids,
    _cleanup_cloud_workspaces_from_config,
    _cloud_workspace_gc_config,
)
from coding_agent.server.http.workspace_retention import (
    _durable_workspace_retention_not_implemented,
    _local_workspace_record_for_provider_operation,
    _remote_retention_enabled,
    _update_workspace_retention,
    _workspace_archive_manifest_response,
    _workspace_record_summary_response,
    _workspace_summary_response,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get("/workspaces", response_model=WorkspaceListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_workspaces(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceListResponse:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        records = await _bindings.module().session_manager.list_workspace_records()
        return WorkspaceListResponse(
            workspaces=[
                _workspace_record_summary_response(record) for record in records
            ]
        )
    config = _bindings.module()._load_cloud_workspace_config()
    entries = await _bindings.module().asyncio.to_thread(
        _bindings.module().list_cloud_workspaces_from_config,
        config,
        active_workspace_ids=await _active_cloud_workspace_ids(),
    )
    return WorkspaceListResponse(
        workspaces=[_workspace_summary_response(entry) for entry in entries]
    )


@router.post("/workspaces/gc", response_model=WorkspaceGcResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def run_workspace_gc(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceGcResponse:
    del request
    _require_admin_context(auth_context)
    cleaned_count = await _cleanup_cloud_workspaces_from_config(
        await _cloud_workspace_gc_config()
    )
    return WorkspaceGcResponse(cleaned_count=cleaned_count)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceSummarySchema)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceSummarySchema:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        record = await _bindings.module().session_manager.load_workspace_record_by_workspace_id(
            workspace_id
        )
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Workspace not found: {workspace_id}"
            )
        return _workspace_record_summary_response(record)
    try:
        entry = await _bindings.module().asyncio.to_thread(
            _bindings.module().get_cloud_workspace_from_config,
            _bindings.module()._load_cloud_workspace_config(),
            workspace_id,
            active_workspace_ids=await _active_cloud_workspace_ids(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _workspace_summary_response(entry)


@router.post(
    "/workspaces/{workspace_id}/retain", response_model=WorkspaceRetentionResponse
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def retain_workspace(
    request: Request,
    workspace_id: str,
    body: WorkspaceRetentionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    return await _update_workspace_retention(
        workspace_id,
        retention_policy=body.retention_policy,
        ttl_seconds=body.ttl_seconds,
    )


@router.post(
    "/workspaces/{workspace_id}/pin", response_model=WorkspaceRetentionResponse
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def pin_workspace(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    return await _update_workspace_retention(
        workspace_id,
        retention_policy="pinned",
        ttl_seconds=None,
    )


@router.post(
    "/workspaces/{workspace_id}/unpin", response_model=WorkspaceRetentionResponse
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def unpin_workspace(
    request: Request,
    workspace_id: str,
    body: WorkspaceUnpinRequest | None = None,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    retention_policy: Literal["delete_on_close", "ttl"]
    ttl_seconds: int | None
    if body is not None and body.retention_policy is not None:
        retention_policy = body.retention_policy
        ttl_seconds = body.ttl_seconds
    else:
        default_policy = (
            _bindings.module()._load_remote_retention_config().get("default_policy")
        )
        if default_policy == "ttl":
            retention_policy = "ttl"
            ttl_seconds = None if body is None else body.ttl_seconds
        else:
            retention_policy = "delete_on_close"
            ttl_seconds = None
    return await _update_workspace_retention(
        workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )


@router.delete("/workspaces/{workspace_id}", response_model=WorkspaceCleanupResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def cleanup_workspace(
    request: Request,
    response: Response,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceCleanupResponse:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        record = await _local_workspace_record_for_provider_operation(workspace_id)
        await _bindings.module().session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaning",
            cleanup_error=None,
        )
        try:
            entry = await _bindings.module().asyncio.to_thread(
                _bindings.module().cleanup_cloud_workspace_from_config,
                _bindings.module()._load_cloud_workspace_config(),
                workspace_id,
                active_workspace_ids=await _active_cloud_workspace_ids(),
            )
        except KeyError as exc:
            await _bindings.module().session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="lost",
                cleanup_error=str(exc),
            )
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            await _bindings.module().session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc),
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Cloud workspace cleanup failed workspace_id=%s", workspace_id
            )
            await _bindings.module().session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc) or "workspace cleanup failed",
            )
            response.status_code = 500
            return WorkspaceCleanupResponse(
                workspace_id=workspace_id,
                status="cleanup_failed",
                error=str(exc),
            )
        await _bindings.module().session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaned",
            cleanup_error=None,
        )
        return WorkspaceCleanupResponse(
            workspace_id=entry.workspace_id,
            status="cleaned",
        )
    try:
        entry = await _bindings.module().asyncio.to_thread(
            _bindings.module().cleanup_cloud_workspace_from_config,
            _bindings.module()._load_cloud_workspace_config(),
            workspace_id,
            active_workspace_ids=await _active_cloud_workspace_ids(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Cloud workspace cleanup failed workspace_id=%s", workspace_id)
        response.status_code = 500
        return WorkspaceCleanupResponse(
            workspace_id=workspace_id,
            status="cleanup_failed",
            error=str(exc),
        )
    return WorkspaceCleanupResponse(workspace_id=entry.workspace_id, status="cleaned")


@router.get(
    "/workspaces/{workspace_id}/archive/manifest",
    response_model=WorkspaceArchiveManifestResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive_manifest_by_id(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceArchiveManifestResponse:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        _ = await _local_workspace_record_for_provider_operation(workspace_id)
    try:
        manifest = await _bindings.module().asyncio.to_thread(
            _bindings.module().workspace_archive_manifest_from_config,
            _bindings.module()._load_cloud_workspace_config(),
            workspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _workspace_archive_manifest_response(manifest)


@router.get(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive_by_id(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceArchiveResponse:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        _ = await _local_workspace_record_for_provider_operation(workspace_id)
    try:
        archive_base64 = await _bindings.module().asyncio.to_thread(
            _bindings.module().export_workspace_archive_by_id_from_config,
            _bindings.module()._load_cloud_workspace_config(),
            workspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceArchiveResponse(format="tar.gz", archive_base64=archive_base64)


__all__ = [
    "cleanup_workspace",
    "get_workspace",
    "get_workspace_archive_by_id",
    "get_workspace_archive_manifest_by_id",
    "list_workspaces",
    "pin_workspace",
    "retain_workspace",
    "run_workspace_gc",
    "unpin_workspace",
]
