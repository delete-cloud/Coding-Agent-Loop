from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.memory import (
    ReviewedMemoryRecord,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    recall_context_messages,
)
from coding_agent.topics.semantic_index import (
    SafeSemanticMemoryIndex,
    SemanticDocId,
)
from coding_agent.topics.semantic_recall import SemanticRecallPlanner
from coding_agent.topics.store import TopicRecord


class FakeMemoryIndex:
    def __init__(self, results: tuple[MemoryHit, ...]) -> None:
        self.results = results
        self.queries: list[tuple[str, int]] = []

    async def upsert(self, memory_id: str, text: str, metadata: dict[str, Any]) -> None:
        raise AssertionError("semantic recall must not write to the memory index")

    async def search(self, query: str, limit: int = 10) -> list[MemoryHit]:
        self.queries.append((query, limit))
        return list(self.results[:limit])

    async def delete(self, memory_id: str) -> None:
        raise AssertionError("semantic recall must not delete from the memory index")


class FakeTopicStore:
    def __init__(self, topics: tuple[TopicRecord, ...]) -> None:
        self.topics = {topic.topic_id: topic for topic in topics}
        self.loaded: list[str] = []

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        self.loaded.append(topic_id)
        return self.topics.get(topic_id)


class FakeMemoryReviewStore:
    def __init__(self, records: tuple[ReviewedMemoryRecord, ...]) -> None:
        self.records = {
            record.candidate.candidate_id: record
            for record in records
            if record.candidate.candidate_id is not None
        }
        self.loaded: list[str] = []

    def load_memory(self, candidate_id: str) -> ReviewedMemoryRecord | None:
        self.loaded.append(candidate_id)
        return self.records.get(candidate_id)


@pytest.mark.asyncio
async def test_semantic_topic_hit_is_rehydrated_from_authoritative_topic() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth convention",
        summary="Authoritative JWT middleware summary",
    )
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(topic)),
                source_refs=("topic:topic-auth",),
                text="Backend text that must not be rendered",
            ),
        ),
        topics=(topic,),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(),
            text="jwt middleware",
            profile="local",
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-auth"]
    assert plan.topic_results[0].summary == "Authoritative JWT middleware summary"
    rendered = recall_context_messages(plan)[0]["content"]
    assert "Authoritative JWT middleware summary" in rendered
    assert "Backend text that must not be rendered" not in rendered


@pytest.mark.asyncio
async def test_stale_semantic_topic_doc_id_is_dropped() -> None:
    topic = _topic("topic-auth", start=4, end=12)
    planner = _planner(
        semantic_hits=(
            _hit(
                "topic-summary:topic-auth:2-9",
                source_refs=("topic:topic-auth",),
            ),
        ),
        topics=(topic,),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="jwt middleware")
    )

    assert plan.topic_results == ()


@pytest.mark.asyncio
async def test_missing_unfinalized_no_summary_and_source_topic_hits_are_dropped() -> (
    None
):
    source = _topic("topic-source", summary="Source summary")
    open_topic = _topic("topic-open", status="open", summary="Open summary", end=None)
    no_summary = _topic("topic-empty", summary=None)
    planner = _planner(
        semantic_hits=(
            _hit(
                "topic-summary:topic-missing:2-9", source_refs=("topic:topic-missing",)
            ),
            _hit("topic-summary:topic-open:2-open", source_refs=("topic:topic-open",)),
            _hit("topic-summary:topic-empty:2-9", source_refs=("topic:topic-empty",)),
            _hit(
                str(SemanticDocId.for_topic(source)),
                source_refs=("topic:topic-source",),
            ),
        ),
        topics=(source, open_topic, no_summary),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=source, text="jwt middleware")
    )

    assert plan.topic_results == ()


@pytest.mark.asyncio
async def test_accepted_memory_hit_is_rehydrated_and_other_statuses_are_dropped() -> (
    None
):
    accepted = _record("memory-accepted", status="accepted")
    candidate = _record("memory-candidate", status="candidate")
    rejected = _record("memory-rejected", status="rejected")
    archived = _record("memory-archived", status="archived")
    planner = _planner(
        semantic_hits=(
            _memory_hit("memory-accepted"),
            _memory_hit("memory-candidate"),
            _memory_hit("memory-rejected"),
            _memory_hit("memory-archived"),
            _memory_hit("memory-missing"),
        ),
        memories=(accepted, candidate, rejected, archived),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="jwt middleware")
    )

    assert plan.topic_results == ()
    assert plan.accepted_memories == (accepted,)


@pytest.mark.asyncio
async def test_semantic_recall_applies_topic_and_memory_filters() -> None:
    matching_topic = _topic(
        "topic-match",
        metadata={
            "profile": "local",
            "domain_profile": "maintenance",
            "bee_pack_id": "pack-alpha",
        },
    )
    wrong_profile = _topic("topic-remote", metadata={"profile": "remote"})
    wrong_kind = _topic("topic-research", kind="research")
    matching_memory = _record(
        "memory-match",
        status="accepted",
        provenance={
            "domain_profile": "maintenance",
            "pack_id": "pack-alpha",
        },
    )
    wrong_memory = _record(
        "memory-docs",
        status="accepted",
        provenance={
            "domain_profile": "documentation",
            "pack_id": "pack-docs",
        },
    )
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(matching_topic)),
                source_refs=("topic:topic-match",),
            ),
            _hit(
                str(SemanticDocId.for_topic(wrong_profile)),
                source_refs=("topic:topic-remote",),
            ),
            _hit(
                str(SemanticDocId.for_topic(wrong_kind)),
                source_refs=("topic:topic-research",),
            ),
            _memory_hit("memory-match"),
            _memory_hit("memory-docs"),
        ),
        topics=(matching_topic, wrong_profile, wrong_kind),
        memories=(matching_memory, wrong_memory),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(kind="coding"),
            text="jwt middleware",
            profile="local",
            domain_profile="maintenance",
            bee_pack_id="pack-alpha",
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-match"]
    assert plan.accepted_memories == (matching_memory,)


@pytest.mark.asyncio
async def test_deterministic_topics_stay_first_and_semantic_hits_refill_after() -> None:
    deterministic_topic = _topic(
        "topic-deterministic",
        title="JWT deterministic",
        summary="JWT validation deterministic match",
    )
    semantic_topic = _topic(
        "topic-semantic",
        title="High score semantic",
        summary="Semantic only authoritative summary",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(deterministic_topic, profile="local")
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(semantic_topic)),
                source_refs=("topic:topic-semantic",),
                score=0.99,
            ),
        ),
        topics=(semantic_topic,),
        topic_index=topic_index,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(),
            text="jwt deterministic",
            profile="local",
            limit=2,
        )
    )

    assert [result.topic_id for result in plan.topic_results] == [
        "topic-deterministic",
        "topic-semantic",
    ]


@pytest.mark.asyncio
async def test_semantic_refill_does_not_expand_beyond_configured_limit() -> None:
    deterministic_topic = _topic(
        "topic-deterministic",
        title="JWT deterministic",
        summary="JWT validation deterministic match",
    )
    semantic_topic = _topic(
        "topic-semantic",
        title="High score semantic",
        summary="Semantic only authoritative summary",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(deterministic_topic, profile="local")
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(semantic_topic)),
                source_refs=("topic:topic-semantic",),
                score=0.99,
            ),
        ),
        topics=(semantic_topic,),
        topic_index=topic_index,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(),
            text="jwt deterministic",
            profile="local",
            limit=1,
        )
    )

    assert [result.topic_id for result in plan.topic_results] == [
        "topic-deterministic"
    ]


def _planner(
    *,
    semantic_hits: tuple[MemoryHit, ...],
    topics: tuple[TopicRecord, ...] = (),
    memories: tuple[ReviewedMemoryRecord, ...] = (),
    topic_index: TopicRangeIndex | None = None,
) -> SemanticRecallPlanner:
    return SemanticRecallPlanner(
        topic_planner=TopicRecallPlanner(topic_index=topic_index or TopicRangeIndex()),
        semantic_index=SafeSemanticMemoryIndex(FakeMemoryIndex(semantic_hits)),
        topic_store=FakeTopicStore(topics),
        memory_review_store=FakeMemoryReviewStore(memories),
    )


def _hit(
    memory_id: str,
    *,
    source_refs: tuple[str, ...],
    text: str = "Safe backend semantic text",
    score: float = 0.8,
) -> MemoryHit:
    return MemoryHit(
        memory_id=memory_id,
        text=text,
        score=score,
        metadata={"kind": "semantic_test"},
        source_refs=source_refs,
    )


def _memory_hit(candidate_id: str) -> MemoryHit:
    return _hit(
        f"accepted-memory:{candidate_id}:accepted",
        source_refs=(f"memory:{candidate_id}",),
    )


def _topic(
    topic_id: str,
    *,
    title: str | None = "Topic",
    summary: str | None = "JWT middleware convention",
    kind: str = "coding",
    status: str = "finalized",
    start: int = 2,
    end: int | None = 9,
    metadata: dict[str, Any] | None = None,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind=kind,
        status=status,
        title=title,
        summary=summary,
        owner="local",
        topic_initial_seq=start,
        topic_finalized_seq=end,
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 6, 24, 9, 5, tzinfo=UTC)
        if status == "finalized"
        else None,
        metadata={"profile": "local"} if metadata is None else metadata,
    )


def _source_topic(*, kind: str = "coding") -> TopicRecord:
    return _topic(
        "topic-source",
        title="Source topic",
        summary=None,
        kind=kind,
        status="open",
        end=None,
    )


def _record(
    candidate_id: str,
    *,
    status: str,
    provenance: dict[str, Any] | None = None,
) -> ReviewedMemoryRecord:
    base_provenance = {
        "topic_id": "topic-auth",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
        ],
    }
    if provenance is not None:
        base_provenance.update(provenance)
    candidate = TopicDerivedMemoryCandidate(
        kind="fact",
        title=f"Memory {candidate_id}",
        summary=f"Authoritative memory summary {candidate_id}",
        scope="topic:topic-auth",
        tags=("auth", "jwt"),
        confidence=0.8,
        provenance=base_provenance,
        candidate_id=candidate_id,
    )
    return ReviewedMemoryRecord(candidate=candidate, status=status)
