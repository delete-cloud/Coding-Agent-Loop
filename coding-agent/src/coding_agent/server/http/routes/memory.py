"""Semantic memory and memory-review routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request

from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    MemoryReviewRecordResponse,
    MemoryReviewTransitionRequest,
    MemoryReviewTransitionResponse,
    SemanticDogfoodTopicRequest,
    SemanticDogfoodTopicResponse,
    SemanticMemoryRebuildRequest,
    SemanticMemoryRebuildResponse,
    SemanticMemoryStatusResponse,
)
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictError,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _get_visible_session,
    _key_error_detail,
    _normalize_direct_auth_context,
    _owner_conflict_http_exception,
    _require_admin_context,
)
from coding_agent.server.http.memory_review import (
    _memory_review_record_response,
    _memory_review_record_visible_for_session,
    _memory_review_store_from_runtime_config,
    _memory_review_transition_response,
    _semantic_dogfood_topic_response,
    _semantic_memory_rebuild_response,
    _semantic_memory_runtime_exception,
    _semantic_memory_status_response,
    _semantic_review_sync_service_from_runtime_config,
    _sync_memory_review_service,
    _transition_memory_review_store,
    _validate_memory_review_transition,
)
from coding_agent.server.http.session_target import _http_exception_detail
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get(
    "/sessions/{session_id}/memory/semantic/status",
    response_model=SemanticMemoryStatusResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_semantic_memory_status(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SemanticMemoryStatusResponse:
    del request
    auth_context = _normalize_direct_auth_context(auth_context)
    _require_admin_context(auth_context)
    _ = await _get_visible_session(session_id, auth_context)
    try:
        status = await _bindings.module().session_manager.semantic_memory_status(
            session_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except RuntimeError as exc:
        raise _semantic_memory_runtime_exception(exc) from exc
    return _semantic_memory_status_response(status)


@router.post(
    "/sessions/{session_id}/memory/semantic/rebuild",
    response_model=SemanticMemoryRebuildResponse,
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def rebuild_semantic_memory(
    request: Request,
    session_id: str,
    body: SemanticMemoryRebuildRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SemanticMemoryRebuildResponse:
    del request
    auth_context = _normalize_direct_auth_context(auth_context)
    _require_admin_context(auth_context)
    _ = await _get_visible_session(session_id, auth_context)
    try:
        report = await _bindings.module().session_manager.rebuild_semantic_memory(
            session_id,
            batch_size=body.batch_size,
            allow_rebuild=body.allow_rebuild,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _semantic_memory_runtime_exception(exc) from exc
    return _semantic_memory_rebuild_response(report)


@router.get(
    "/sessions/{session_id}/memory/reviews",
    response_model=list[MemoryReviewRecordResponse],
)
@limiter.limit(RateLimits.GET_SESSION)
async def list_memory_reviews(
    request: Request,
    session_id: str,
    status: Literal["candidate", "accepted", "rejected", "archived"] | None = Query(
        None
    ),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> list[MemoryReviewRecordResponse]:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        runtime_ctx = await _bindings.module().session_manager.ensure_session_runtime(
            session_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=_http_exception_detail(exc),
        ) from exc

    config = getattr(runtime_ctx, "config", None)
    if not isinstance(config, Mapping):
        raise HTTPException(
            status_code=500,
            detail="Session runtime context is missing config",
        )
    review_store = _memory_review_store_from_runtime_config(config)
    records = [
        record
        for record in review_store.list_memories(status=status)
        if _memory_review_record_visible_for_session(record, session_id=session_id)
    ]
    return [_memory_review_record_response(record) for record in records]


@router.post(
    "/sessions/{session_id}/memory/semantic/dogfood-topic",
    response_model=SemanticDogfoodTopicResponse,
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def seed_semantic_dogfood_topic(
    request: Request,
    session_id: str,
    body: SemanticDogfoodTopicRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SemanticDogfoodTopicResponse:
    del request
    auth_context = _normalize_direct_auth_context(auth_context)
    _require_admin_context(auth_context)
    _ = await _get_visible_session(session_id, auth_context)
    try:
        result = await _bindings.module().session_manager.seed_semantic_dogfood_topic(
            session_id,
            title=body.title,
            summary=body.summary,
            kind=body.kind,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _semantic_memory_runtime_exception(exc) from exc
    return _semantic_dogfood_topic_response(result)


@router.post(
    "/sessions/{session_id}/memory/reviews/{candidate_id}",
    response_model=MemoryReviewTransitionResponse,
)
@limiter.limit(RateLimits.APPROVE)
async def transition_memory_review(
    request: Request,
    session_id: str,
    candidate_id: str,
    body: MemoryReviewTransitionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> MemoryReviewTransitionResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        runtime_ctx = await _bindings.module().session_manager.ensure_session_runtime(
            session_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=_http_exception_detail(exc),
        ) from exc

    config = getattr(runtime_ctx, "config", None)
    if not isinstance(config, Mapping):
        raise HTTPException(
            status_code=500,
            detail="Session runtime context is missing config",
        )
    review_store = _memory_review_store_from_runtime_config(config)
    service = _semantic_review_sync_service_from_runtime_config(config)

    try:
        current_record = _validate_memory_review_transition(
            review_store,
            session_id=session_id,
            candidate_id=candidate_id,
            status=body.status,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if current_record.status == body.status:
        record = current_record
    else:
        try:
            record = _transition_memory_review_store(
                review_store,
                session_id=session_id,
                candidate_id=candidate_id,
                status=body.status,
                reason=body.reason,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=_key_error_detail(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if service is not None:
        try:
            await _sync_memory_review_service(service, record=record)
        except Exception as exc:
            logger.exception(
                "Semantic memory review sync failed for session %s candidate %s",
                session_id,
                candidate_id,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Semantic memory review sync failed: {_http_exception_detail(exc)}"
                ),
            ) from exc

    return _memory_review_transition_response(record)


__all__ = [
    "get_semantic_memory_status",
    "list_memory_reviews",
    "rebuild_semantic_memory",
    "seed_semantic_dogfood_topic",
    "transition_memory_review",
]
