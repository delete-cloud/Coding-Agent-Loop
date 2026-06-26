"""Operational maintenance helpers for semantic topic memory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from coding_agent.topics.memory import ReviewedMemoryRecord
from coding_agent.topics.semantic_backends import SemanticMemoryBackend
from coding_agent.topics.semantic_sync import SemanticMemorySyncer, SemanticSyncReport
from coding_agent.topics.store import TopicRecord, TopicStatus


class TopicListingStore(Protocol):
    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: TopicStatus | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[TopicRecord]: ...


class ReviewedMemoryListingStore(Protocol):
    def list_memories(
        self,
        *,
        status: str | None = None,
    ) -> Sequence[ReviewedMemoryRecord]: ...


@dataclass(frozen=True, slots=True)
class SemanticMemoryStatus:
    document_count: int
    reviewed_memory_count: int
    accepted_reviewed_memory_count: int
    topic_store_available: bool


@dataclass(frozen=True, slots=True)
class SemanticMemoryMaintainer:
    """Run explicit semantic memory maintenance operations.

    Full rebuilds are destructive: the syncer clears the semantic backend before
    re-indexing authoritative topic and reviewed-memory records. Callers must
    provide authoritative stores backed by quiescent or snapshot-consistent
    reads.
    """

    syncer: SemanticMemorySyncer
    backend: SemanticMemoryBackend
    review_store: ReviewedMemoryListingStore
    topic_store: TopicListingStore | None = None

    async def rebuild(
        self,
        *,
        batch_size: int = 100,
        allow_rebuild: bool = True,
    ) -> SemanticSyncReport:
        _require_positive_int("batch_size", batch_size)
        if self.topic_store is None:
            raise RuntimeError("topic_store is required for semantic memory rebuild")
        topics = await self._list_finalized_topics(batch_size=batch_size)
        reviewed_memories = tuple(self.review_store.list_memories())
        return await self.syncer.rebuild(
            topics,
            reviewed_memories,
            allow_rebuild=allow_rebuild,
        )

    async def status(self) -> SemanticMemoryStatus:
        document_ids = await self.backend.list_ids()
        reviewed_memories = tuple(self.review_store.list_memories())
        accepted_memories = tuple(self.review_store.list_memories(status="accepted"))
        return SemanticMemoryStatus(
            document_count=len(document_ids),
            reviewed_memory_count=len(reviewed_memories),
            accepted_reviewed_memory_count=len(accepted_memories),
            topic_store_available=self.topic_store is not None,
        )

    async def _list_finalized_topics(
        self,
        *,
        batch_size: int,
    ) -> tuple[TopicRecord, ...]:
        if self.topic_store is None:
            raise RuntimeError("topic_store is required for semantic memory rebuild")
        topics: list[TopicRecord] = []
        seen_topic_ids: set[str] = set()
        after_created_at: datetime | None = None
        after_topic_id: str | None = None
        while True:
            page = tuple(
                await self.topic_store.list_topics(
                    status="finalized",
                    after_created_at=after_created_at,
                    after_topic_id=after_topic_id,
                    limit=batch_size,
                )
            )
            if not page:
                return tuple(topics)
            for topic in page:
                if topic.topic_id in seen_topic_ids:
                    raise RuntimeError(
                        "topic scan changed during rebuild; retry with a "
                        "quiescent or snapshot-consistent topic store"
                    )
                seen_topic_ids.add(topic.topic_id)
            topics.extend(page)
            if len(page) < batch_size:
                return tuple(topics)
            last_topic = page[-1]
            after_created_at = last_topic.created_at
            after_topic_id = last_topic.topic_id


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be positive")
