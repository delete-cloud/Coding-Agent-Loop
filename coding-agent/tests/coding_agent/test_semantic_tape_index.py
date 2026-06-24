from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.semantic_index import (
    SemanticMemoryDocument,
    SafeSemanticMemoryIndex,
    merge_hybrid_recall_hits,
)
from coding_agent.topics.range_index import TopicRangeIndex, TopicRangeSearchQuery
from coding_agent.topics.store import TopicRecord


class FakeMemoryIndex:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, dict[str, Any]]] = []
        self.queries: list[tuple[str, int]] = []
        self.results: list[MemoryHit] = []

    async def upsert(self, memory_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.upserts.append((memory_id, text, metadata))

    async def search(self, query: str, limit: int = 10) -> list[MemoryHit]:
        self.queries.append((query, limit))
        return self.results[:limit]

    async def delete(self, memory_id: str) -> None:
        del memory_id


@pytest.mark.asyncio
async def test_semantic_index_rejects_forbidden_text_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="memory-secret",
                text="secret: value",
                metadata={"kind": "fact"},
                source_refs=("topic:topic-auth",),
            )
        )

    assert fake.upserts == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_forbidden_text_before_query_embedding() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await index.search("stdout: raw command output")

    assert fake.queries == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_forbidden_metadata_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="memory-auth",
                text="Safe memory text",
                metadata={"raw": "stdout: secret"},
                source_refs=("topic:topic-auth",),
            )
        )

    assert fake.upserts == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_unsafe_backend_hits_before_returning() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="memory-auth",
            text="stdout: raw command output",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-auth",),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await index.search("safe query")

    assert fake.queries == [("safe query", 10)]


def test_hybrid_merge_is_deterministic_and_dedups() -> None:
    topic_index = TopicRangeIndex()
    topic_index.index_topic(
        _topic("topic-auth", title="Auth convention", summary="JWT auth middleware")
    )
    topic_index.index_topic(
        _topic("topic-cache", title="Cache convention", summary="Redis cache TTL")
    )
    topic_results = tuple(
        topic_index.search(TopicRangeSearchQuery(text="auth cache", limit=2))
    )
    semantic_hits = (
        MemoryHit(
            memory_id="memory-cache-semantic",
            text="Semantic cache memory must dedupe against topic-cache.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-cache",),
        ),
        MemoryHit(
            memory_id="memory-auth-semantic",
            text="Semantic auth memory must dedupe against topic-auth.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-auth",),
        ),
        MemoryHit(
            memory_id="memory-extra",
            text="Semantic-only memory keeps stable identity ordering.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-extra",),
        ),
        MemoryHit(
            memory_id="memory-extra-duplicate",
            text="Duplicate semantic-only memory is dropped by source ref.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-extra",),
        ),
    )

    first = merge_hybrid_recall_hits(topic_results, semantic_hits)
    second = merge_hybrid_recall_hits(reversed(topic_results), reversed(semantic_hits))

    assert [hit.identity for hit in first] == [
        "topic:topic-auth",
        "topic:topic-cache",
        "topic:topic-extra",
    ]
    assert [hit.identity for hit in second] == [hit.identity for hit in first]
    assert first[0].topic_result is topic_results[0]
    assert first[1].topic_result is topic_results[1]
    assert first[0].semantic_hit is not None
    assert first[1].semantic_hit is not None
    assert first[2].topic_result is None
    assert first[2].semantic_hit is not None
    assert first[2].semantic_hit.memory_id == "memory-extra"


def _topic(
    topic_id: str,
    *,
    title: str,
    summary: str,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status="finalized",
        title=title,
        summary=summary,
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=9,
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 6, 24, 9, 5, tzinfo=UTC),
        metadata={"profile": "local"},
    )
