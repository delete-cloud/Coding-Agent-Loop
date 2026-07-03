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
async def test_recall_min_score_filters_semantic_results() -> None:
    below_floor = _topic(
        "topic-low-score",
        title="Low score semantic",
        summary="Low similarity semantic topic.",
    )
    above_floor = _topic(
        "topic-high-score",
        title="High score semantic",
        summary="High similarity semantic topic.",
    )
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(below_floor)),
                source_refs=("topic:topic-low-score",),
                score=0.49,
            ),
            _hit(
                str(SemanticDocId.for_topic(above_floor)),
                source_refs=("topic:topic-high-score",),
                score=0.80,
            ),
        ),
        topics=(below_floor, above_floor),
        recall_min_score=0.50,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="jwt middleware")
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-high-score"]


@pytest.mark.asyncio
async def test_recall_min_score_filters_semantic_accepted_memory_hits() -> None:
    below_floor = _record("memory-low-score", status="accepted")
    above_floor = _record("memory-high-score", status="accepted")
    planner = _planner(
        semantic_hits=(
            _memory_hit("memory-low-score", score=0.49),
            _memory_hit("memory-high-score", score=0.80),
        ),
        memories=(below_floor, above_floor),
        recall_min_score=0.50,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="jwt middleware")
    )

    assert plan.topic_results == ()
    assert plan.accepted_memories == (above_floor,)


@pytest.mark.asyncio
async def test_recall_min_score_exempts_scoreless_accepted_memory_listing() -> None:
    listing_record = _record("memory-listing", status="accepted")
    below_floor = _record("memory-low-score", status="accepted")
    planner = _planner(
        semantic_hits=(_memory_hit("memory-low-score", score=0.49),),
        memories=(below_floor,),
        accepted_memories=(listing_record,),
        recall_min_score=0.50,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="memory listing")
    )

    assert len(plan.topic_results) == 0
    assert len(plan.accepted_memories) == 1
    assert plan.accepted_memories == (listing_record,)


@pytest.mark.asyncio
async def test_recall_min_overlap_filters_deterministic_results() -> None:
    low_overlap = _topic(
        "topic-low-overlap",
        title="JWT auth",
        summary="JWT token rotation.",
    )
    high_overlap = _topic(
        "topic-high-overlap",
        title="JWT auth validation",
        summary="JWT auth validation middleware.",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(low_overlap, profile="local")
    topic_index.index_topic(high_overlap, profile="local")
    planner = _planner(
        semantic_hits=(),
        topic_index=topic_index,
        recall_min_overlap=0.75,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(),
            text="jwt auth validation middleware",
            profile="local",
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-high-overlap"]


@pytest.mark.asyncio
async def test_floors_default_off_preserve_existing_plans() -> None:
    deterministic_topic = _topic(
        "topic-deterministic",
        title="JWT deterministic",
        summary="JWT validation deterministic match",
    )
    semantic_topic = _topic(
        "topic-semantic",
        title="Semantic auth",
        summary="Semantic auth retry summary.",
    )
    accepted_memory = _record("memory-accepted", status="accepted")
    topic_index = TopicRangeIndex()
    topic_index.index_topic(deterministic_topic, profile="local")
    semantic_hits = (
        _hit(
            str(SemanticDocId.for_topic(semantic_topic)),
            source_refs=("topic:topic-semantic",),
            score=0.01,
        ),
        _memory_hit("memory-accepted"),
    )
    legacy_signature_planner = SemanticRecallPlanner(
        topic_planner=TopicRecallPlanner(topic_index=topic_index),
        semantic_index=SafeSemanticMemoryIndex(FakeMemoryIndex(semantic_hits)),
        topic_store=FakeTopicStore((semantic_topic,)),
        memory_review_store=FakeMemoryReviewStore((accepted_memory,)),
    )
    explicit_default_planner = _planner(
        semantic_hits=semantic_hits,
        topics=(semantic_topic,),
        memories=(accepted_memory,),
        topic_index=topic_index,
        recall_min_score=None,
        recall_min_overlap=None,
    )
    planner_input = TopicRecallPlannerInput(
        source_topic=_source_topic(),
        text="jwt validation",
        profile="local",
        limit=3,
    )

    default_plan = await legacy_signature_planner.plan(planner_input)
    explicit_default_plan = await explicit_default_planner.plan(planner_input)

    assert default_plan == explicit_default_plan
    assert default_plan.topic_results == explicit_default_plan.topic_results
    assert default_plan.accepted_memories == explicit_default_plan.accepted_memories
    assert [result.topic_id for result in default_plan.topic_results] == [
        "topic-deterministic",
        "topic-semantic",
    ]
    assert default_plan.accepted_memories == (accepted_memory,)


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
async def test_scoped_memory_hit_does_not_fallback_to_other_session_record() -> None:
    other_session_memory = _record(
        "memory-other-session",
        status="accepted",
        provenance={
            "session_id": "other-session",
            "tape_id": "other-tape",
            "profile": "local",
        },
    )
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_reviewed_memory(other_session_memory)),
                source_refs=("memory:memory-other-session",),
            ),
        ),
        memories=(other_session_memory,),
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(source_topic=_source_topic(), text="jwt middleware")
    )

    assert plan.accepted_memories == ()


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

    assert [result.topic_id for result in plan.topic_results] == ["topic-deterministic"]


@pytest.mark.asyncio
async def test_current_query_prefers_recent_semantic_topic_over_stale_deterministic_topic() -> (
    None
):
    stale_deterministic_topic = _topic(
        "topic-old-deploy",
        title="o6n coding-agent deploy 685d8ba",
        summary=(
            "The latest deployed immutable o6n Coding Agent image tag is "
            "685d8ba56e0155a11ba3f10611e179bc0c64d561."
        ),
        finalized_at=datetime(2026, 6, 30, 5, 0, tzinfo=UTC),
    )
    recent_semantic_topic = _topic(
        "topic-new-deploy",
        title="o6n coding-agent deploy 0aea889",
        summary=(
            "The latest deployed immutable o6n Coding Agent chart revision and "
            "image tag is 0aea88921b16759d9556881f646a69203770f374."
        ),
        finalized_at=datetime(2026, 6, 30, 6, 25, tzinfo=UTC),
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(stale_deterministic_topic, profile="local")
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(recent_semantic_topic)),
                source_refs=("topic:topic-new-deploy",),
                score=0.64,
            ),
        ),
        topics=(recent_semantic_topic,),
        topic_index=topic_index,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=_source_topic(),
            text="o6n 当前部署的 coding-agent immutable image tag 是什么",
            profile="local",
            limit=2,
        )
    )

    assert [result.topic_id for result in plan.topic_results] == [
        "topic-new-deploy",
        "topic-old-deploy",
    ]


@pytest.mark.asyncio
async def test_derived_current_query_prefers_recent_semantic_topic() -> None:
    source_topic = _topic(
        "topic-source",
        title="o6n 当前部署的 coding-agent immutable image tag 是什么",
        summary=None,
        status="open",
        end=None,
    )
    stale_deterministic_topic = _topic(
        "topic-old-deploy",
        title="o6n coding-agent deploy 685d8ba",
        summary=(
            "The latest deployed immutable o6n Coding Agent image tag is "
            "685d8ba56e0155a11ba3f10611e179bc0c64d561."
        ),
        finalized_at=datetime(2026, 6, 30, 5, 0, tzinfo=UTC),
    )
    recent_semantic_topic = _topic(
        "topic-new-deploy",
        title="o6n coding-agent deploy 0aea889",
        summary=(
            "The latest deployed immutable o6n Coding Agent chart revision and "
            "image tag is 0aea88921b16759d9556881f646a69203770f374."
        ),
        finalized_at=datetime(2026, 6, 30, 6, 25, tzinfo=UTC),
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(stale_deterministic_topic, profile="local")
    planner = _planner(
        semantic_hits=(
            _hit(
                str(SemanticDocId.for_topic(recent_semantic_topic)),
                source_refs=("topic:topic-new-deploy",),
                score=0.64,
            ),
        ),
        topics=(recent_semantic_topic,),
        topic_index=topic_index,
    )

    plan = await planner.plan(
        TopicRecallPlannerInput(
            source_topic=source_topic,
            profile="local",
            limit=2,
        )
    )

    assert [result.topic_id for result in plan.topic_results] == [
        "topic-new-deploy",
        "topic-old-deploy",
    ]


def _planner(
    *,
    semantic_hits: tuple[MemoryHit, ...],
    topics: tuple[TopicRecord, ...] = (),
    memories: tuple[ReviewedMemoryRecord, ...] = (),
    accepted_memories: tuple[ReviewedMemoryRecord, ...] = (),
    topic_index: TopicRangeIndex | None = None,
    recall_min_score: float | None = None,
    recall_min_overlap: float | None = None,
) -> SemanticRecallPlanner:
    return SemanticRecallPlanner(
        topic_planner=TopicRecallPlanner(
            topic_index=topic_index or TopicRangeIndex(),
            accepted_memories=accepted_memories,
        ),
        semantic_index=SafeSemanticMemoryIndex(FakeMemoryIndex(semantic_hits)),
        topic_store=FakeTopicStore(topics),
        memory_review_store=FakeMemoryReviewStore(memories),
        recall_min_score=recall_min_score,
        recall_min_overlap=recall_min_overlap,
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


def _memory_hit(candidate_id: str, *, score: float = 0.8) -> MemoryHit:
    return _hit(
        f"accepted-memory:{candidate_id}:accepted",
        source_refs=(f"memory:{candidate_id}",),
        score=score,
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
    finalized_at: datetime | None = None,
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
        finalized_at=(
            finalized_at
            if finalized_at is not None
            else datetime(2026, 6, 24, 9, 5, tzinfo=UTC)
            if status == "finalized"
            else None
        ),
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
