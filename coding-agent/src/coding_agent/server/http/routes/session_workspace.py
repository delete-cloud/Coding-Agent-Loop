"""Session-scoped workspace diff, patch, and archive routes."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
    verify_api_key,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    WorkspaceArchiveManifestResponse,
    WorkspaceArchiveResponse,
    WorkspaceDiffFileSchema,
    WorkspaceDiffResponse,
    WorkspacePatchResponse,
)
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _get_visible_session,
    _key_error_detail,
    _owner_conflict_http_exception,
)
from coding_agent.server.http.local_git import (
    _local_workspace_diff,
    _local_workspace_patch,
    _session_local_workspace_root,
)
from coding_agent.server.http.workspace_retention import (
    _workspace_archive_manifest_response,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get(
    "/sessions/{session_id}/workspace/diff",
    response_model=WorkspaceDiffResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_diff(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceDiffResponse:
    del request
    session = await _get_visible_session(session_id, auth_context)
    try:
        local_workspace_root = _session_local_workspace_root(session)
        if local_workspace_root is not None:
            diff = await _bindings.module().asyncio.to_thread(
                _local_workspace_diff, local_workspace_root
            )
        else:
            diff = await _bindings.module().session_manager.export_workspace_archive(
                session_id,
                lambda binding: _bindings.module().workspace_diff_from_config(
                    _bindings.module()._load_cloud_workspace_config(),
                    binding.workspace_id,
                ),
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    return WorkspaceDiffResponse(
        session_id=session_id,
        workspace_id=diff.workspace_id,
        files=[
            WorkspaceDiffFileSchema(
                path=file.path,
                status=file.status,
                old_path=file.old_path,
                additions=file.additions,
                deletions=file.deletions,
                binary=file.binary,
            )
            for file in diff.files
        ],
        additions=diff.additions,
        deletions=diff.deletions,
    )


@router.get(
    "/sessions/{session_id}/workspace/patch",
    response_model=WorkspacePatchResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_patch(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspacePatchResponse:
    del request
    session = await _get_visible_session(session_id, auth_context)
    try:
        local_workspace_root = _session_local_workspace_root(session)
        if local_workspace_root is not None:
            patch = await _bindings.module().asyncio.to_thread(
                _local_workspace_patch, local_workspace_root
            )
        else:
            patch = await _bindings.module().session_manager.export_workspace_archive(
                session_id,
                lambda binding: _bindings.module().workspace_patch_from_config(
                    _bindings.module()._load_cloud_workspace_config(),
                    binding.workspace_id,
                ),
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    return WorkspacePatchResponse(
        session_id=session_id,
        workspace_id=patch.workspace_id,
        format=patch.format,
        patch=patch.patch,
    )


async def _export_session_workspace_archive(
    session_id: str,
) -> WorkspaceArchiveResponse:
    try:
        archive_base64 = (
            await _bindings.module().session_manager.export_workspace_archive(
                session_id,
                lambda binding: _bindings.module().export_workspace_archive_from_config(
                    _bindings.module()._load_cloud_workspace_config(),
                    binding,
                ),
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        logger.exception(
            "Cloud workspace archive download/export failed session_id=%s",
            session_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception(
            "Cloud workspace archive download/export failed session_id=%s",
            session_id,
        )
        raise

    return WorkspaceArchiveResponse(format="tar.gz", archive_base64=archive_base64)


@router.get(
    "/sessions/{session_id}/workspace/archive/manifest",
    response_model=WorkspaceArchiveManifestResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_archive_manifest(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveManifestResponse:
    del request, api_key
    try:
        manifest = await _bindings.module().session_manager.export_workspace_archive(
            session_id,
            lambda binding: _bindings.module().workspace_archive_manifest_from_config(
                _bindings.module()._load_cloud_workspace_config(),
                binding.workspace_id,
                session_id=session_id,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return _workspace_archive_manifest_response(manifest)


@router.get(
    "/sessions/{session_id}/workspace/archive",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_archive(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveResponse:
    del request, api_key
    return await _export_session_workspace_archive(session_id)


@router.get(
    "/sessions/{session_id}/workspace",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveResponse:
    del request, api_key
    return await _export_session_workspace_archive(session_id)


__all__ = [
    "_export_session_workspace_archive",
    "get_session_workspace_archive",
    "get_session_workspace_archive_manifest",
    "get_session_workspace_diff",
    "get_session_workspace_patch",
    "get_workspace_archive",
]
