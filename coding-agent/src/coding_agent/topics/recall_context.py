"""Cross-topic recall planning and context-pack composition."""

from __future__ import annotations

from dataclasses import dataclass

from agentkit.tape.tape import Tape
from coding_agent.topics.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackRenderer,
    ContextPackSection,
    EvidenceRef,
)
from coding_agent.topics.memory import (
    ReviewedMemoryRecord,
    accepted_memory_context_pack,
)
from coding_agent.topics.range_index import (
    TopicRangeIndex,
    TopicRangeSearchQuery,
    TopicRangeSearchResult,
    require_recall_safe_text,
)
from coding_agent.topics.recall import (
    RecalledTopic,
    TopicRecallStore,
    record_topic_recall,
)
from coding_agent.topics.store import TopicRecallLinkRecord, TopicRecord


@dataclass(frozen=True)
class TopicRecallPlannerInput:
    source_topic: TopicRecord
    text: str | None = None
    profile: str | None = None
    bee_pack_id: str | None = None
    bee_template_id: str | None = None
    domain_profile: str | None = None
    template_kind: str | None = None
    tags: tuple[str, ...] = ()
    limit: int = 5
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.text is not None:
            require_recall_safe_text("text", self.text)
        for name, value in (
            ("bee_pack_id", self.bee_pack_id),
            ("bee_template_id", self.bee_template_id),
            ("domain_profile", self.domain_profile),
            ("template_kind", self.template_kind),
        ):
            if value is not None:
                require_recall_safe_text(name, value)
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        for index, tag in enumerate(self.tags):
            require_recall_safe_text(f"tags[{index}]", tag)


@dataclass(frozen=True)
class TopicRecallPlan:
    source_topic: TopicRecord
    topic_results: tuple[TopicRangeSearchResult, ...] = ()
    accepted_memories: tuple[ReviewedMemoryRecord, ...] = ()


class TopicRecallPlanner:
    def __init__(
        self,
        *,
        topic_index: TopicRangeIndex,
        accepted_memories: tuple[ReviewedMemoryRecord, ...] = (),
    ) -> None:
        self._topic_index = topic_index
        self._accepted_memories = tuple(accepted_memories)

    def plan(self, planner_input: TopicRecallPlannerInput) -> TopicRecallPlan:
        if not planner_input.enabled:
            return TopicRecallPlan(source_topic=planner_input.source_topic)
        query = build_topic_recall_query(planner_input)
        topic_results = tuple(
            result
            for result in self._topic_index.search(query)
            if result.topic_id != planner_input.source_topic.topic_id
        )[: planner_input.limit]
        accepted = _rank_accepted_memories(
            self._accepted_memories,
            query_text=query.text,
            tags=planner_input.tags,
            domain_profile=planner_input.domain_profile,
            bee_pack_id=planner_input.bee_pack_id,
            limit=planner_input.limit,
        )
        return TopicRecallPlan(
            source_topic=planner_input.source_topic,
            topic_results=topic_results,
            accepted_memories=accepted,
        )


def build_topic_recall_query(
    planner_input: TopicRecallPlannerInput,
) -> TopicRangeSearchQuery:
    text = planner_input.text or _topic_query_text(planner_input.source_topic)
    return TopicRangeSearchQuery(
        text=text or None,
        kind=planner_input.source_topic.kind,
        profile=planner_input.profile,
        bee_pack_id=planner_input.bee_pack_id,
        bee_template_id=planner_input.bee_template_id,
        domain_profile=planner_input.domain_profile,
        template_kind=planner_input.template_kind,
        tags=planner_input.tags,
        status="finalized",
        limit=planner_input.limit,
    )


async def record_recall_plan(
    *,
    tape: Tape,
    store: TopicRecallStore,
    plan: TopicRecallPlan,
) -> tuple[TopicRecallLinkRecord, ...]:
    links: list[TopicRecallLinkRecord] = []
    for result in plan.topic_results:
        link = await record_topic_recall(
            tape=tape,
            store=store,
            source_topic=plan.source_topic,
            recalled=RecalledTopic(
                topic=_topic_record_from_result(result),
                score=result.score,
                reason=result.reason,
            ),
            metadata={"recall_source": "topic_range_index"},
        )
        links.append(link)
    return tuple(links)


def recall_context_pack(plan: TopicRecallPlan, *, enabled: bool = True) -> ContextPack:
    if not enabled:
        return ContextPack(sections=())
    sections: list[ContextPackSection] = []
    if plan.topic_results:
        sections.append(
            ContextPackSection(
                title="Cross-topic recall references",
                items=tuple(
                    _topic_result_item(result) for result in plan.topic_results
                ),
            )
        )
    sections.extend(accepted_memory_context_pack(plan.accepted_memories).sections)
    return ContextPack(sections=tuple(sections))


def recall_context_messages(
    plan: TopicRecallPlan,
    *,
    enabled: bool = True,
) -> list[dict[str, object]]:
    return ContextPackRenderer().render_messages(
        recall_context_pack(plan, enabled=enabled)
    )


def _topic_result_item(result: TopicRangeSearchResult) -> ContextPackItem:
    return ContextPackItem(
        source_kind="topic_summary",
        source_id=f"topic:{result.topic_id}",
        label=result.title or result.topic_id,
        body=result.summary,
        score=result.score,
        evidence=(
            EvidenceRef(
                kind="topic",
                source_id=result.topic_id,
                label="topic range summary",
                session_id=result.session_id,
            ),
        ),
        metadata={
            "source_topic_ids": [result.topic_id],
            "source_entry_ranges": [
                source_range.to_dict() for source_range in result.source_ranges
            ],
            "reason": result.reason,
        },
    )


def _rank_accepted_memories(
    records: tuple[ReviewedMemoryRecord, ...],
    *,
    query_text: str | None,
    tags: tuple[str, ...],
    domain_profile: str | None,
    bee_pack_id: str | None,
    limit: int,
) -> tuple[ReviewedMemoryRecord, ...]:
    query_tokens = _tokens(query_text or "")
    tag_set = set(tags)
    ranked: list[tuple[float, str, ReviewedMemoryRecord]] = []
    for record in records:
        if record.status != "accepted":
            continue
        provenance = record.candidate.provenance
        if (
            domain_profile is not None
            and provenance.get("domain_profile") != domain_profile
        ):
            continue
        if bee_pack_id is not None and provenance.get("pack_id") != bee_pack_id:
            continue
        memory_tokens = _tokens(f"{record.candidate.title} {record.candidate.summary}")
        token_score = (
            len(query_tokens & memory_tokens) / len(query_tokens)
            if query_tokens
            else 1.0
        )
        tag_score = (
            0.25 if tag_set and tag_set.intersection(record.candidate.tags) else 0
        )
        domain_score = (
            0.15
            if domain_profile is not None
            and provenance.get("domain_profile") == domain_profile
            else 0
        )
        pack_score = (
            0.15
            if bee_pack_id is not None and provenance.get("pack_id") == bee_pack_id
            else 0
        )
        score = round(token_score + tag_score + domain_score + pack_score, 4)
        if query_tokens and score == 0:
            continue
        candidate_id = record.candidate.candidate_id or ""
        ranked.append((score, candidate_id, record))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return tuple(item[2] for item in ranked[:limit])


def _topic_record_from_result(result: TopicRangeSearchResult) -> TopicRecord:
    source_range = result.source_ranges[0]
    return TopicRecord(
        topic_id=result.topic_id,
        tape_id=result.tape_id,
        session_id=result.session_id,
        kind=result.kind,
        status=result.status,
        title=result.title,
        summary=result.summary,
        owner=None,
        topic_initial_seq=source_range.start_seq,
        topic_finalized_seq=source_range.end_seq,
        created_at=result.created_at,
        finalized_at=result.finalized_at,
        metadata={"profile": result.profile} if result.profile is not None else {},
    )


def _topic_query_text(topic: TopicRecord) -> str:
    return " ".join(part for part in (topic.title or "", topic.summary or "") if part)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in (
            "".join(char.lower() if char.isalnum() else " " for char in value)
        ).split()
        if len(token) >= 3
    }
