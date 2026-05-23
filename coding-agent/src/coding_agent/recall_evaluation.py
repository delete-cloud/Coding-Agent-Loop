"""Deterministic evaluation helpers for cross-topic recall variants."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from coding_agent.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
)
from coding_agent.topic_memory import ReviewedMemoryRecord
from coding_agent.topic_range_index import TopicRangeIndex, require_recall_safe_text
from coding_agent.topic_store import JSONObject, TopicRecord


class RecallEvalVariant(StrEnum):
    NO_RECALL = "no_recall"
    ACCEPTED_MEMORY = "accepted_memory"
    TOPIC_RANGE = "topic_range"
    TOPIC_AND_MEMORY = "topic_and_memory"


@dataclass(frozen=True)
class RecallEvalCase:
    case_id: str
    source_topic: TopicRecord
    query_text: str
    profile: str | None = None
    bee_template_id: str | None = None
    tags: tuple[str, ...] = ()
    expected_topic_ids: tuple[str, ...] = ()
    expected_memory_ids: tuple[str, ...] = ()
    limit: int = 5

    def __post_init__(self) -> None:
        _require_safe_identifier("case_id", self.case_id)
        require_recall_safe_text("query_text", self.query_text)
        if self.profile is not None:
            _require_safe_identifier("profile", self.profile)
        if self.bee_template_id is not None:
            _require_safe_identifier("bee_template_id", self.bee_template_id)
        for index, tag in enumerate(self.tags):
            _require_safe_identifier(f"tags[{index}]", tag)
        for index, topic_id in enumerate(self.expected_topic_ids):
            _require_safe_identifier(f"expected_topic_ids[{index}]", topic_id)
        for index, memory_id in enumerate(self.expected_memory_ids):
            _require_safe_identifier(f"expected_memory_ids[{index}]", memory_id)
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))


@dataclass(frozen=True)
class RecallEvalVariantResult:
    variant: RecallEvalVariant
    status: str
    topic_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    candidate_count: int = 0
    matched_expected: int = 0

    def to_dict(self) -> JSONObject:
        return {
            "variant": self.variant.value,
            "status": self.status,
            "topic_ids": list(self.topic_ids),
            "memory_ids": list(self.memory_ids),
            "candidate_count": self.candidate_count,
            "matched_expected": self.matched_expected,
        }


@dataclass(frozen=True)
class RecallEvalReport:
    case_id: str
    results: tuple[RecallEvalVariantResult, ...]

    def result_for(self, variant: RecallEvalVariant) -> RecallEvalVariantResult:
        for result in self.results:
            if result.variant == variant:
                return result
        raise KeyError(f"recall eval variant not found: {variant.value}")

    def to_dict(self) -> JSONObject:
        return {
            "case_id": self.case_id,
            "results": [result.to_dict() for result in self.results],
        }


def evaluate_recall_variants(
    case: RecallEvalCase,
    *,
    topic_index: TopicRangeIndex,
    accepted_memories: tuple[ReviewedMemoryRecord, ...] = (),
) -> RecallEvalReport:
    """Compare recall-disabled, memory-only, topic-only, and combined recall."""

    results = (
        _evaluate_variant(
            case,
            variant=RecallEvalVariant.NO_RECALL,
            topic_index=TopicRangeIndex(),
            accepted_memories=(),
            enabled=False,
        ),
        _evaluate_variant(
            case,
            variant=RecallEvalVariant.ACCEPTED_MEMORY,
            topic_index=TopicRangeIndex(),
            accepted_memories=accepted_memories,
            enabled=True,
        ),
        _evaluate_variant(
            case,
            variant=RecallEvalVariant.TOPIC_RANGE,
            topic_index=topic_index,
            accepted_memories=(),
            enabled=True,
        ),
        _evaluate_variant(
            case,
            variant=RecallEvalVariant.TOPIC_AND_MEMORY,
            topic_index=topic_index,
            accepted_memories=accepted_memories,
            enabled=True,
        ),
    )
    return RecallEvalReport(case_id=case.case_id, results=results)


def _evaluate_variant(
    case: RecallEvalCase,
    *,
    variant: RecallEvalVariant,
    topic_index: TopicRangeIndex,
    accepted_memories: tuple[ReviewedMemoryRecord, ...],
    enabled: bool,
) -> RecallEvalVariantResult:
    plan = TopicRecallPlanner(
        topic_index=topic_index,
        accepted_memories=accepted_memories,
    ).plan(
        TopicRecallPlannerInput(
            source_topic=case.source_topic,
            text=case.query_text,
            profile=case.profile,
            bee_template_id=case.bee_template_id,
            tags=case.tags,
            limit=case.limit,
            enabled=enabled,
        )
    )
    topic_ids = tuple(result.topic_id for result in plan.topic_results)
    memory_ids = tuple(
        memory.candidate.candidate_id or "" for memory in plan.accepted_memories
    )
    candidate_count = len(topic_ids) + len(memory_ids)
    matched_expected = len(set(topic_ids) & set(case.expected_topic_ids)) + len(
        set(memory_ids) & set(case.expected_memory_ids)
    )
    return RecallEvalVariantResult(
        variant=variant,
        status="matched" if candidate_count else "empty",
        topic_ids=topic_ids,
        memory_ids=memory_ids,
        candidate_count=candidate_count,
        matched_expected=matched_expected,
    )


def _require_safe_identifier(field_name: str, value: str) -> None:
    require_recall_safe_text(field_name, value)
    if any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must not contain whitespace")
