"""Semantic memory sync and rebuild service for tape-native memory."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Protocol

from coding_agent.topics.memory import MemoryReviewStore, ReviewedMemoryRecord
from coding_agent.topics.semantic_backends import (
    SemanticBackendScope,
    SemanticIndexSchema,
    SemanticMemoryBackend,
)
from coding_agent.topics.semantic_index import (
    SafeSemanticMemoryIndex,
    SemanticMemoryDocument,
    SemanticSourceRef,
    semantic_document_from_reviewed_memory,
    semantic_document_from_topic,
)
from coding_agent.topics.store import TopicRecord

_SAFE_SCOPE_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class SemanticSyncReport:
    topic_count: int = 0
    reviewed_memory_count: int = 0
    indexed_count: int = 0
    skipped_count: int = 0
    deleted_count: int = 0
    indexed_ids: tuple[str, ...] = ()
    deleted_ids: tuple[str, ...] = ()


class ReviewedMemorySemanticSyncer(Protocol):
    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class SemanticMemoryReviewSyncService:
    """Coordinate review-store transitions with semantic memory sync."""

    review_store: MemoryReviewStore
    syncer: ReviewedMemorySemanticSyncer

    async def accept_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        record = self.review_store.accept_candidate(candidate_id, reason=reason)
        await self.syncer.sync_reviewed_memory(record)
        return record

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        record = self.review_store.reject_candidate(candidate_id, reason=reason)
        await self.syncer.sync_reviewed_memory(record)
        return record

    async def archive_candidate(
        self,
        candidate_id: str,
        *,
        reason: str | None = None,
    ) -> ReviewedMemoryRecord:
        record = self.review_store.archive_candidate(candidate_id, reason=reason)
        await self.syncer.sync_reviewed_memory(record)
        return record

    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> None:
        await self.syncer.sync_reviewed_memory(record)


class SemanticMemorySyncer:
    """Sync derived semantic documents from authoritative topic/memory records."""

    def __init__(
        self,
        *,
        index: SafeSemanticMemoryIndex,
        backend: SemanticMemoryBackend,
        schema: SemanticIndexSchema,
    ) -> None:
        self._index = index
        self._backend = backend
        self._schema = schema

    async def ensure_schema(self, *, allow_rebuild: bool = False) -> None:
        await self._backend.ensure_schema(self._schema, allow_rebuild=allow_rebuild)

    async def rebuild(
        self,
        topics: Iterable[TopicRecord],
        reviewed_memories: Iterable[ReviewedMemoryRecord],
        *,
        allow_rebuild: bool = True,
    ) -> SemanticSyncReport:
        topic_documents, topic_skipped, topic_count = self._topic_documents(topics)
        memory_documents, memory_skipped, memory_count = (
            self._reviewed_memory_documents(reviewed_memories)
        )
        documents = (*topic_documents, *memory_documents)
        self._validate_documents(documents)
        await self.ensure_schema(allow_rebuild=allow_rebuild)
        deleted_ids = await self._clear_existing_documents()
        indexed_ids = await self._upsert_documents(documents)
        return SemanticSyncReport(
            topic_count=topic_count,
            reviewed_memory_count=memory_count,
            indexed_count=len(indexed_ids),
            skipped_count=topic_skipped + memory_skipped,
            deleted_count=len(deleted_ids),
            indexed_ids=indexed_ids,
            deleted_ids=deleted_ids,
        )

    async def reconcile_startup(
        self,
        topics: Iterable[TopicRecord],
        reviewed_memories: Iterable[ReviewedMemoryRecord],
        *,
        allow_rebuild: bool = True,
    ) -> SemanticSyncReport:
        return await self.rebuild(
            topics,
            reviewed_memories,
            allow_rebuild=allow_rebuild,
        )

    async def sync_topic(self, topic: TopicRecord) -> SemanticSyncReport:
        await self.ensure_schema()
        document = self._document_from_topic(topic)
        if document is None:
            deleted_ids = await self._delete_scope(self._topic_scope(topic))
            return SemanticSyncReport(
                topic_count=1,
                skipped_count=1,
                deleted_count=len(deleted_ids),
                deleted_ids=deleted_ids,
            )
        indexed_ids = await self._upsert_documents((document,))
        return SemanticSyncReport(
            topic_count=1,
            indexed_count=len(indexed_ids),
            indexed_ids=indexed_ids,
        )

    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> SemanticSyncReport:
        await self.ensure_schema()
        document = self._document_from_reviewed_memory(record)
        if document is None:
            deleted_ids = await self._delete_scope(self._reviewed_memory_scope(record))
            return SemanticSyncReport(
                reviewed_memory_count=1,
                skipped_count=1,
                deleted_count=len(deleted_ids),
                deleted_ids=deleted_ids,
            )
        indexed_ids = await self._upsert_documents((document,))
        return SemanticSyncReport(
            reviewed_memory_count=1,
            indexed_count=len(indexed_ids),
            indexed_ids=indexed_ids,
        )

    def _topic_documents(
        self,
        topics: Iterable[TopicRecord],
    ) -> tuple[tuple[SemanticMemoryDocument, ...], int, int]:
        documents: list[SemanticMemoryDocument] = []
        skipped = 0
        count = 0
        for topic in topics:
            count += 1
            document = self._document_from_topic(topic)
            if document is None:
                skipped += 1
                continue
            documents.append(document)
        return tuple(documents), skipped, count

    def _reviewed_memory_documents(
        self,
        records: Iterable[ReviewedMemoryRecord],
    ) -> tuple[tuple[SemanticMemoryDocument, ...], int, int]:
        documents: list[SemanticMemoryDocument] = []
        skipped = 0
        count = 0
        for record in records:
            count += 1
            document = self._document_from_reviewed_memory(record)
            if document is None:
                skipped += 1
                continue
            documents.append(document)
        return tuple(documents), skipped, count

    def _document_from_topic(
        self,
        topic: TopicRecord,
    ) -> SemanticMemoryDocument | None:
        if topic.status != "finalized" or topic.summary is None:
            return None
        document = semantic_document_from_topic(topic)
        profile = _profile_from_topic(topic)
        if profile is None:
            return document
        metadata = dict(document.metadata)
        metadata["profile"] = profile
        return SemanticMemoryDocument(
            memory_id=document.memory_id,
            text=document.text,
            metadata=metadata,
            source_refs=document.source_refs,
        )

    def _document_from_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> SemanticMemoryDocument | None:
        if record.status != "accepted":
            return None
        return semantic_document_from_reviewed_memory(record)

    async def _upsert_documents(
        self,
        documents: Iterable[SemanticMemoryDocument],
    ) -> tuple[str, ...]:
        indexed_ids: list[str] = []
        for document in sorted(documents, key=lambda item: str(item.memory_id)):
            await self._index.upsert(document)
            indexed_ids.append(str(document.memory_id))
        return tuple(indexed_ids)

    def _validate_documents(
        self,
        documents: Iterable[SemanticMemoryDocument],
    ) -> None:
        for document in documents:
            self._index.require_safe_document(document)

    async def _clear_existing_documents(self) -> tuple[str, ...]:
        existing_ids = tuple(await self._backend.list_ids())
        for memory_id in existing_ids:
            await self._index.delete(memory_id)
        return tuple(sorted(existing_ids))

    async def _delete_scope(self, scope: SemanticBackendScope) -> tuple[str, ...]:
        existing_ids = tuple(await self._backend.list_ids(scope=scope))
        if not existing_ids:
            return ()
        await self._backend.delete_scope(scope)
        return tuple(sorted(existing_ids))

    def _topic_scope(self, topic: TopicRecord) -> SemanticBackendScope:
        return SemanticBackendScope.for_source_ref(
            SemanticSourceRef.for_topic(topic),
            session_id=topic.session_id,
            profile=_profile_from_topic(topic),
        )

    def _reviewed_memory_scope(
        self,
        record: ReviewedMemoryRecord,
    ) -> SemanticBackendScope:
        candidate_id = record.candidate.candidate_id
        if candidate_id is None:
            raise ValueError("reviewed memory candidate is missing candidate_id")
        return SemanticBackendScope(source_kind="memory", source_id=candidate_id)


def _profile_from_topic(topic: TopicRecord) -> str | None:
    profile = topic.metadata.get("profile", topic.metadata.get("profile_id"))
    if not isinstance(profile, str):
        return None
    if _SAFE_SCOPE_PART_RE.fullmatch(profile) is None:
        return None
    return profile
