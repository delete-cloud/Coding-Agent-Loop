from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.memory import (
    ReviewedMemoryRecord,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.semantic_index import (
    SemanticMemoryDocument,
    SemanticSourceRef,
    SafeSemanticMemoryIndex,
    merge_hybrid_recall_hits,
    semantic_document_from_reviewed_memory,
    semantic_document_from_topic,
)
from coding_agent.topics.range_index import TopicRangeIndex, TopicRangeSearchQuery
from coding_agent.topics.store import TopicRecord


class FakeMemoryIndex:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, dict[str, Any]]] = []
        self.queries: list[tuple[str, int]] = []
        self.deletes: list[str] = []
        self.results: list[MemoryHit] = []

    async def upsert(self, memory_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.upserts.append((memory_id, text, metadata))

    async def search(self, query: str, limit: int = 10) -> list[MemoryHit]:
        self.queries.append((query, limit))
        return self.results[:limit]

    async def delete(self, memory_id: str) -> None:
        self.deletes.append(memory_id)


@pytest.mark.asyncio
async def test_semantic_index_rejects_forbidden_text_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="topic-summary:topic-auth:2-9",
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
                memory_id="topic-summary:topic-auth:2-9",
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
            memory_id="topic-summary:topic-auth:2-9",
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


@pytest.mark.asyncio
async def test_semantic_index_rejects_invalid_document_id_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="semantic document id"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="memory-auth",
                text="Safe memory text",
                metadata={"kind": "fact"},
                source_refs=("topic:topic-auth",),
            )
        )

    assert fake.upserts == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_invalid_source_ref_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="semantic source ref"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="topic-summary:topic-auth:2-9",
                text="Safe memory text",
                metadata={"kind": "fact"},
                source_refs=("run:topic-auth",),
            )
        )

    assert fake.upserts == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_missing_primary_source_ref_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="primary source ref"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="topic-summary:topic-auth:2-9",
                text="Safe memory text",
                metadata={"kind": "fact"},
                source_refs=(),
            )
        )

    assert fake.upserts == []


@pytest.mark.asyncio
async def test_semantic_index_rejects_mismatched_primary_source_ref_before_indexing() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="primary source ref"):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id="topic-summary:topic-auth:2-9",
                text="Safe memory text",
                metadata={"kind": "fact"},
                source_refs=("topic:topic-cache",),
            )
        )

    assert fake.upserts == []


def test_topic_semantic_document_uses_closed_identity_and_safe_metadata() -> None:
    topic = _topic("topic-auth", title="Auth convention", summary="JWT middleware")

    document = semantic_document_from_topic(topic)

    assert document.memory_id == "topic-summary:topic-auth:2-9"
    assert document.source_refs == ("topic:topic-auth",)
    assert document.text == "Auth convention\n\nJWT middleware"
    assert document.metadata == {
        "kind": "topic_summary",
        "topic_id": "topic-auth",
        "tape_id": "tape-1",
        "session_id": "session-1",
        "topic_kind": "coding",
        "topic_status": "finalized",
        "source_start_seq": 2,
        "source_end_seq": 9,
    }


def test_reviewed_memory_semantic_document_accepts_only_accepted_records() -> None:
    accepted = ReviewedMemoryRecord(
        candidate=_candidate("memory-candidate-auth"),
        status="accepted",
        review_reason="Useful cross-topic reference",
    )

    document = semantic_document_from_reviewed_memory(accepted)

    assert document.memory_id == "accepted-memory:memory-candidate-auth:accepted"
    assert document.source_refs == ("memory:memory-candidate-auth",)
    assert document.text == "Auth convention\n\nJWT middleware convention"
    assert document.metadata == {
        "kind": "accepted_reviewed_memory",
        "memory_kind": "fact",
        "candidate_id": "memory-candidate-auth",
        "memory_status": "accepted",
        "scope": "topic:topic-auth",
        "tags": ["auth", "jwt"],
    }

    for status in ("candidate", "rejected", "archived"):
        record = ReviewedMemoryRecord(
            candidate=_candidate(f"memory-candidate-{status}"),
            status=status,
        )
        with pytest.raises(ValueError, match="accepted"):
            semantic_document_from_reviewed_memory(record)
        with pytest.raises(ValueError, match="accepted"):
            SemanticSourceRef.for_reviewed_memory(record)


@pytest.mark.asyncio
async def test_semantic_index_delete_validates_document_id_and_delegates() -> None:
    fake = FakeMemoryIndex()
    index = SafeSemanticMemoryIndex(fake)

    await index.delete("topic-summary:topic-auth:2-9")

    assert fake.deletes == ["topic-summary:topic-auth:2-9"]

    with pytest.raises(ValueError, match="semantic document id"):
        await index.delete("memory-auth")

    assert fake.deletes == ["topic-summary:topic-auth:2-9"]


@pytest.mark.asyncio
async def test_semantic_index_rejects_backend_hit_with_invalid_identity() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="memory-auth",
            text="Safe backend text",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-auth",),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="semantic document id"):
        await index.search("safe query")


@pytest.mark.asyncio
async def test_semantic_index_rejects_backend_hit_with_invalid_source_ref() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="topic-summary:topic-auth:2-9",
            text="Safe backend text",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=("run:topic-auth",),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="semantic source ref"):
        await index.search("safe query")


@pytest.mark.asyncio
async def test_semantic_index_rejects_backend_hit_missing_primary_source_ref() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="topic-summary:topic-auth:2-9",
            text="Safe backend text",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=(),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="primary source ref"):
        await index.search("safe query")


@pytest.mark.asyncio
async def test_semantic_index_rejects_backend_hit_mismatched_primary_source_ref() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="topic-summary:topic-auth:2-9",
            text="Safe backend text",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-cache",),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    with pytest.raises(ValueError, match="primary source ref"):
        await index.search("safe query")


@pytest.mark.asyncio
async def test_semantic_only_hit_uses_primary_source_identity() -> None:
    fake = FakeMemoryIndex()
    fake.results = [
        MemoryHit(
            memory_id="topic-summary:topic-auth:2-9",
            text="Safe backend text",
            score=0.1,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-auth",),
        )
    ]
    index = SafeSemanticMemoryIndex(fake)

    hits = await index.search("safe query")
    merged = merge_hybrid_recall_hits((), hits)

    assert [hit.identity for hit in merged] == ["topic:topic-auth"]


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
            memory_id="topic-summary:topic-cache:2-9",
            text="Semantic cache memory must dedupe against topic-cache.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-cache",),
        ),
        MemoryHit(
            memory_id="topic-summary:topic-auth:2-9",
            text="Semantic auth memory must dedupe against topic-auth.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-auth",),
        ),
        MemoryHit(
            memory_id="topic-summary:topic-extra:2-9",
            text="Semantic-only memory keeps stable identity ordering.",
            score=0.9,
            metadata={"kind": "fact"},
            source_refs=("topic:topic-extra",),
        ),
        MemoryHit(
            memory_id="topic-summary:topic-extra:2-9",
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
    assert first[2].semantic_hit.memory_id == "topic-summary:topic-extra:2-9"


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


def _candidate(candidate_id: str) -> TopicDerivedMemoryCandidate:
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title="Auth convention",
        summary="JWT middleware convention",
        scope="topic:topic-auth",
        tags=("jwt", "auth"),
        confidence=0.8,
        provenance={
            "topic_id": "topic-auth",
            "topic_status": "finalized",
            "topic_kind": "coding",
            "source_entry_ranges": [
                {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
            ],
        },
        candidate_id=candidate_id,
    )
