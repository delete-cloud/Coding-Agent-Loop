"""Authoritative semantic recall rehydration for Coding Agent topics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.memory import ReviewedMemoryRecord
from coding_agent.topics.provenance import topic_entry_range
from coding_agent.topics.range_index import (
    TopicRangeSearchQuery,
    TopicRangeSearchResult,
)
from coding_agent.topics.recall_context import (
    TopicRecallPlan,
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    build_topic_recall_query,
)
from coding_agent.topics.semantic_index import (
    HybridRecallHit,
    SafeSemanticMemoryIndex,
    SemanticDocId,
    SemanticDocKind,
    merge_hybrid_recall_hits,
)
from coding_agent.topics.store import TopicRecord


class SemanticTopicStore(Protocol):
    async def load_topic(self, topic_id: str) -> TopicRecord | None: ...


class SemanticMemoryReviewStore(Protocol):
    def load_memory(self, candidate_id: str) -> ReviewedMemoryRecord | None: ...


@dataclass(frozen=True)
class _RehydratedTopicHit:
    hit: MemoryHit
    result: TopicRangeSearchResult


class SemanticRecallPlanner:
    """Compose deterministic recall with rehydrated semantic recall hits."""

    def __init__(
        self,
        *,
        topic_planner: TopicRecallPlanner,
        semantic_index: SafeSemanticMemoryIndex,
        topic_store: SemanticTopicStore,
        memory_review_store: SemanticMemoryReviewStore,
    ) -> None:
        _require_dependency("topic_planner", topic_planner)
        _require_dependency("semantic_index", semantic_index)
        _require_dependency("topic_store", topic_store)
        _require_dependency("memory_review_store", memory_review_store)
        self._topic_planner = topic_planner
        self._semantic_index = semantic_index
        self._topic_store = topic_store
        self._memory_review_store = memory_review_store

    async def plan(self, planner_input: TopicRecallPlannerInput) -> TopicRecallPlan:
        base_plan = self._topic_planner.plan(planner_input)
        if not planner_input.enabled:
            return base_plan

        query = build_topic_recall_query(planner_input)
        if query.text is None:
            return base_plan

        semantic_hits = await self._semantic_index.search(
            query.text,
            limit=planner_input.limit * 4,
        )
        topic_hits: list[MemoryHit] = []
        memory_hits: list[MemoryHit] = []
        for hit in semantic_hits:
            document_id = SemanticDocId.parse(hit.memory_id)
            if document_id.kind is SemanticDocKind.TOPIC_SUMMARY:
                topic_hits.append(hit)
            elif document_id.kind is SemanticDocKind.ACCEPTED_REVIEWED_MEMORY:
                memory_hits.append(hit)

        rehydrated_topics = await self._rehydrate_topic_hits(
            tuple(topic_hits),
            planner_input=planner_input,
        )
        topic_results = self._merge_topic_results(
            deterministic_results=base_plan.topic_results,
            semantic_topics=rehydrated_topics,
            limit=planner_input.limit,
        )
        accepted_memories = self._merge_accepted_memories(
            deterministic_memories=base_plan.accepted_memories,
            semantic_memories=self._rehydrate_memory_hits(
                tuple(memory_hits),
                planner_input=planner_input,
            ),
            limit=planner_input.limit,
        )
        return TopicRecallPlan(
            source_topic=base_plan.source_topic,
            topic_results=topic_results,
            accepted_memories=accepted_memories,
        )

    async def _rehydrate_topic_hits(
        self,
        hits: tuple[MemoryHit, ...],
        *,
        planner_input: TopicRecallPlannerInput,
    ) -> tuple[_RehydratedTopicHit, ...]:
        query = build_topic_recall_query(planner_input)
        rehydrated: list[_RehydratedTopicHit] = []
        for hit in hits:
            document_id = SemanticDocId.parse(hit.memory_id)
            topic = await self._topic_store.load_topic(document_id.source_id)
            if topic is None:
                continue
            if not _topic_hit_is_current(topic, document_id, planner_input):
                continue
            if not _topic_satisfies_query_filters(topic, query):
                continue
            rehydrated.append(
                _RehydratedTopicHit(
                    hit=hit,
                    result=_topic_result_from_record(topic, score=hit.score),
                )
            )
        return tuple(rehydrated)

    def _rehydrate_memory_hits(
        self,
        hits: tuple[MemoryHit, ...],
        *,
        planner_input: TopicRecallPlannerInput,
    ) -> tuple[ReviewedMemoryRecord, ...]:
        rehydrated: list[ReviewedMemoryRecord] = []
        for hit in hits:
            document_id = SemanticDocId.parse(hit.memory_id)
            record = self._memory_review_store.load_memory(document_id.source_id)
            if record is None:
                continue
            if not _memory_hit_is_current(record, document_id):
                continue
            if not _memory_satisfies_filters(record, planner_input):
                continue
            rehydrated.append(record)
        return tuple(rehydrated)

    def _merge_topic_results(
        self,
        *,
        deterministic_results: tuple[TopicRangeSearchResult, ...],
        semantic_topics: tuple[_RehydratedTopicHit, ...],
        limit: int,
    ) -> tuple[TopicRangeSearchResult, ...]:
        result_by_identity = {
            f"topic:{topic.result.topic_id}": topic.result for topic in semantic_topics
        }
        merged = merge_hybrid_recall_hits(
            deterministic_results,
            (topic.hit for topic in semantic_topics),
            limit=limit,
        )
        results: list[TopicRangeSearchResult] = []
        for hybrid_hit in merged:
            result = _topic_result_from_hybrid_hit(hybrid_hit, result_by_identity)
            if result is not None:
                results.append(result)
        return tuple(results)

    def _merge_accepted_memories(
        self,
        *,
        deterministic_memories: tuple[ReviewedMemoryRecord, ...],
        semantic_memories: tuple[ReviewedMemoryRecord, ...],
        limit: int,
    ) -> tuple[ReviewedMemoryRecord, ...]:
        records: list[ReviewedMemoryRecord] = []
        seen: set[str] = set()
        for record in (*deterministic_memories, *semantic_memories):
            candidate_id = record.candidate.candidate_id
            if candidate_id is None:
                raise ValueError("reviewed memory candidate is missing candidate_id")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            records.append(record)
            if len(records) >= limit:
                break
        return tuple(records)


def _topic_hit_is_current(
    topic: TopicRecord,
    document_id: SemanticDocId,
    planner_input: TopicRecallPlannerInput,
) -> bool:
    if topic.topic_id == planner_input.source_topic.topic_id:
        return False
    if topic.status != "finalized":
        return False
    if topic.summary is None:
        return False
    return str(SemanticDocId.for_topic(topic)) == str(document_id)


def _memory_hit_is_current(
    record: ReviewedMemoryRecord,
    document_id: SemanticDocId,
) -> bool:
    if record.status != "accepted":
        return False
    return str(SemanticDocId.for_reviewed_memory(record)) == str(document_id)


def _topic_satisfies_query_filters(
    topic: TopicRecord,
    query: TopicRangeSearchQuery,
) -> bool:
    if query.kind is not None and topic.kind != query.kind:
        return False
    if query.status is not None and topic.status != query.status:
        return False
    if (
        query.profile is not None
        and _metadata_str(topic, "profile", "profile_id") != query.profile
    ):
        return False
    if (
        query.bee_pack_id is not None
        and _metadata_str(topic, "bee_pack_id", "pack_id") != query.bee_pack_id
    ):
        return False
    if (
        query.bee_template_id is not None
        and _metadata_str(topic, "bee_template_id", "template_id")
        != query.bee_template_id
    ):
        return False
    if (
        query.domain_profile is not None
        and _metadata_str(topic, "domain_profile") != query.domain_profile
    ):
        return False
    if (
        query.template_kind is not None
        and _metadata_str(topic, "template_kind") != query.template_kind
    ):
        return False
    if query.tags and not set(query.tags).issubset(_metadata_tags(topic)):
        return False
    if query.created_after is not None and topic.created_at < query.created_after:
        return False
    return not (
        query.created_before is not None and topic.created_at > query.created_before
    )


def _memory_satisfies_filters(
    record: ReviewedMemoryRecord,
    planner_input: TopicRecallPlannerInput,
) -> bool:
    provenance = record.candidate.provenance
    if (
        planner_input.domain_profile is not None
        and provenance.get("domain_profile") != planner_input.domain_profile
    ):
        return False
    if (
        planner_input.bee_pack_id is not None
        and provenance.get("pack_id") != planner_input.bee_pack_id
    ):
        return False
    if (
        planner_input.bee_template_id is not None
        and provenance.get("template_id") != planner_input.bee_template_id
    ):
        return False
    if (
        planner_input.template_kind is not None
        and provenance.get("template_kind") != planner_input.template_kind
    ):
        return False
    return True


def _topic_result_from_record(
    topic: TopicRecord,
    *,
    score: float,
) -> TopicRangeSearchResult:
    if topic.summary is None:
        raise ValueError("topic result requires summary")
    return TopicRangeSearchResult(
        topic_id=topic.topic_id,
        tape_id=topic.tape_id,
        session_id=topic.session_id,
        title=topic.title,
        summary=topic.summary,
        score=score,
        reason="semantic_rehydrated",
        source_ranges=(topic_entry_range(topic),),
        kind=topic.kind,
        status=topic.status,
        created_at=topic.created_at,
        finalized_at=topic.finalized_at,
        profile=_metadata_str(topic, "profile", "profile_id"),
        bee_pack_id=_metadata_str(topic, "bee_pack_id", "pack_id"),
        bee_template_id=_metadata_str(topic, "bee_template_id", "template_id"),
        domain_profile=_metadata_str(topic, "domain_profile"),
        template_kind=_metadata_str(topic, "template_kind"),
        related_task_ids=_metadata_tuple(topic, "related_task_ids"),
        report_refs=_metadata_tuple(topic, "report_refs"),
        evidence_refs=_metadata_tuple(topic, "evidence_refs"),
        tags=_metadata_tags(topic),
    )


def _topic_result_from_hybrid_hit(
    hybrid_hit: HybridRecallHit,
    semantic_results: dict[str, TopicRangeSearchResult],
) -> TopicRangeSearchResult | None:
    if hybrid_hit.topic_result is not None:
        return hybrid_hit.topic_result
    return semantic_results.get(hybrid_hit.identity)


def _metadata_str(topic: TopicRecord, *keys: str) -> str | None:
    for key in keys:
        value = topic.metadata.get(key)
        if isinstance(value, str):
            return value
    return None


def _metadata_tuple(topic: TopicRecord, key: str) -> tuple[str, ...]:
    value = topic.metadata.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _metadata_tags(topic: TopicRecord) -> tuple[str, ...]:
    return _metadata_tuple(topic, "tags")


def _require_dependency(name: str, value: object) -> None:
    if value is None:
        raise TypeError(f"{name} is required")
