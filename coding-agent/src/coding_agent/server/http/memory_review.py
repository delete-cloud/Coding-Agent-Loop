"""Memory-review store adapters and response mappers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from fastapi import HTTPException

from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    memory_candidate_belongs_to_session,
    memory_candidate_session_id,
    memory_candidate_tape_id,
)
from coding_agent.topics.range_index import require_recall_safe_text
from coding_agent.topics.semantic_maintenance import SemanticMemoryStatus
from coding_agent.topics.semantic_sync import (
    SemanticMemoryReviewSyncService,
    SemanticSyncReport,
)
from coding_agent.server.http.session_target import _http_exception_detail
from coding_agent.server.schemas import (
    MemoryReviewRecordResponse,
    MemoryReviewTransitionResponse,
    SemanticDogfoodTopicResponse,
    SemanticMemoryRebuildResponse,
    SemanticMemoryStatusResponse,
)


def _memory_review_store_from_runtime_config(
    config: Mapping[str, object],
) -> MemoryReviewStore:
    review_store = config.get("memory_review_store")
    if not isinstance(review_store, MemoryReviewStore):
        raise HTTPException(
            status_code=500,
            detail="Memory review store is not configured",
        )
    return review_store


def _semantic_review_sync_service_from_runtime_config(
    config: Mapping[str, object],
) -> SemanticMemoryReviewSyncService | None:
    service = config.get("semantic_memory_review_sync_service")
    if service is None:
        return None
    if not isinstance(service, SemanticMemoryReviewSyncService):
        raise HTTPException(
            status_code=500,
            detail="Semantic memory review sync service is not configured correctly",
        )
    return service


def _validate_memory_review_transition(
    review_store: MemoryReviewStore,
    *,
    session_id: str,
    candidate_id: str,
    status: Literal["accepted", "rejected", "archived"],
    reason: str | None,
) -> ReviewedMemoryRecord:
    if reason is not None:
        require_recall_safe_text("review_reason", reason)
    record = review_store.load_memory_for_session(session_id, candidate_id)
    if record is None:
        raise KeyError(f"memory candidate not found: {candidate_id}")
    record_session_id = memory_candidate_session_id(record.candidate)
    if record_session_id is not None and not memory_candidate_belongs_to_session(
        record.candidate,
        session_id=session_id,
    ):
        raise KeyError(f"memory candidate not found: {candidate_id}")
    if record.status != "candidate" and record.status != status:
        raise ValueError(f"memory candidate {candidate_id} is already {record.status}")
    return record


def _transition_memory_review_store(
    review_store: MemoryReviewStore,
    *,
    session_id: str,
    candidate_id: str,
    status: Literal["accepted", "rejected", "archived"],
    reason: str | None,
) -> ReviewedMemoryRecord:
    if status == "accepted":
        return review_store.accept_candidate_for_session(
            session_id,
            candidate_id,
            reason=reason,
        )
    if status == "rejected":
        return review_store.reject_candidate_for_session(
            session_id,
            candidate_id,
            reason=reason,
        )
    return review_store.archive_candidate_for_session(
        session_id,
        candidate_id,
        reason=reason,
    )


async def _sync_memory_review_service(
    service: SemanticMemoryReviewSyncService,
    *,
    record: ReviewedMemoryRecord,
) -> None:
    await service.sync_reviewed_memory(record)


def _memory_review_transition_response(
    record: ReviewedMemoryRecord,
) -> MemoryReviewTransitionResponse:
    candidate = record.candidate
    candidate_id = candidate.candidate_id
    if candidate_id is None:
        raise RuntimeError("reviewed memory candidate is missing candidate_id")
    if record.status not in {"accepted", "rejected", "archived"}:
        raise RuntimeError(f"unexpected reviewed memory status: {record.status}")
    return MemoryReviewTransitionResponse(
        candidate_id=candidate_id,
        status=cast(Literal["accepted", "rejected", "archived"], record.status),
        review_reason=record.review_reason,
        kind=candidate.kind,
        title=candidate.title,
        scope=candidate.scope,
        tags=list(candidate.tags),
        confidence=candidate.confidence,
    )


def _memory_review_record_response(
    record: ReviewedMemoryRecord,
) -> MemoryReviewRecordResponse:
    candidate = record.candidate
    candidate_id = candidate.candidate_id
    if candidate_id is None:
        raise RuntimeError("reviewed memory candidate is missing candidate_id")
    if record.status not in {"candidate", "accepted", "rejected", "archived"}:
        raise RuntimeError(f"unexpected reviewed memory status: {record.status}")
    topic_id = candidate.provenance.get("topic_id")
    return MemoryReviewRecordResponse(
        candidate_id=candidate_id,
        status=cast(
            Literal["candidate", "accepted", "rejected", "archived"],
            record.status,
        ),
        review_reason=record.review_reason,
        kind=candidate.kind,
        title=candidate.title,
        summary=candidate.summary,
        scope=candidate.scope,
        tags=list(candidate.tags),
        confidence=candidate.confidence,
        topic_id=topic_id if isinstance(topic_id, str) else None,
        session_id=memory_candidate_session_id(candidate),
        tape_id=memory_candidate_tape_id(candidate),
    )


def _memory_review_record_visible_for_session(
    record: ReviewedMemoryRecord,
    *,
    session_id: str,
) -> bool:
    record_session_id = memory_candidate_session_id(record.candidate)
    if record_session_id is None:
        return False
    return memory_candidate_belongs_to_session(
        record.candidate,
        session_id=session_id,
    )


def _semantic_memory_status_response(
    status: SemanticMemoryStatus,
) -> SemanticMemoryStatusResponse:
    return SemanticMemoryStatusResponse(
        document_count=status.document_count,
        reviewed_memory_count=status.reviewed_memory_count,
        accepted_reviewed_memory_count=status.accepted_reviewed_memory_count,
        topic_store_available=status.topic_store_available,
    )


def _semantic_memory_rebuild_response(
    report: SemanticSyncReport,
) -> SemanticMemoryRebuildResponse:
    return SemanticMemoryRebuildResponse(
        topic_count=report.topic_count,
        reviewed_memory_count=report.reviewed_memory_count,
        indexed_count=report.indexed_count,
        skipped_count=report.skipped_count,
        deleted_count=report.deleted_count,
        indexed_ids=list(report.indexed_ids),
        deleted_ids=list(report.deleted_ids),
    )


def _semantic_dogfood_topic_response(
    result: object,
) -> SemanticDogfoodTopicResponse:
    topic_id = getattr(result, "topic_id", None)
    candidate_id = getattr(result, "candidate_id", None)
    warnings = getattr(result, "warnings", ())
    if not isinstance(topic_id, str):
        raise RuntimeError("dogfood topic result is missing topic_id")
    if candidate_id is not None and not isinstance(candidate_id, str):
        raise RuntimeError("dogfood topic result has invalid candidate_id")
    if not isinstance(warnings, tuple | list) or not all(
        isinstance(warning, str) for warning in warnings
    ):
        raise RuntimeError("dogfood topic result has invalid warnings")
    return SemanticDogfoodTopicResponse(
        topic_id=topic_id,
        candidate_id=candidate_id,
        warnings=list(warnings),
    )


def _semantic_memory_runtime_exception(exc: RuntimeError) -> HTTPException:
    detail = _http_exception_detail(exc)
    if detail in {
        "semantic memory is disabled",
        "topic_store is required for semantic memory rebuild",
        "topic_store is required for semantic dogfood topic seed",
    }:
        return HTTPException(status_code=409, detail=detail)
    if detail == "turn already in progress":
        return HTTPException(status_code=409, detail="Turn already in progress")
    return HTTPException(status_code=500, detail=detail)


__all__ = [
    "_memory_review_record_response",
    "_memory_review_record_visible_for_session",
    "_memory_review_store_from_runtime_config",
    "_memory_review_transition_response",
    "_semantic_dogfood_topic_response",
    "_semantic_memory_rebuild_response",
    "_semantic_memory_runtime_exception",
    "_semantic_memory_status_response",
    "_semantic_review_sync_service_from_runtime_config",
    "_sync_memory_review_service",
    "_transition_memory_review_store",
    "_validate_memory_review_transition",
]
