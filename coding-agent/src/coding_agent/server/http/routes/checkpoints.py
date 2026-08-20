"""Session checkpoint capture, list, and restore routes."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from coding_agent.server.auth import (
    verify_api_key,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    CheckpointCaptureRequest,
    CheckpointListResponse,
    CheckpointMetadataResponse,
    CheckpointRestoreResponse,
)
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import _key_error_detail
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.post(
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
    if not await _bindings.module().session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        checkpoint = await _bindings.module().session_manager.capture_checkpoint(
            session_id,
            label=body.label if body else None,
            extra=None,
        )
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@router.get("/sessions/{session_id}/checkpoints", response_model=CheckpointListResponse)
@limiter.limit(RateLimits.LIST_CHECKPOINTS)
async def list_checkpoints(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointListResponse:
    if not await _bindings.module().session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    checkpoints = await _bindings.module().session_manager.list_checkpoints(session_id)
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


@router.post(
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
    if not await _bindings.module().session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        await _bindings.module().session_manager.restore_checkpoint(
            session_id, checkpoint_id
        )
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


__all__ = [
    "capture_checkpoint",
    "list_checkpoints",
    "restore_checkpoint",
]
