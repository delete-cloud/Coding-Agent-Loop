"""Topic-level provenance and cost helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from coding_agent.topic_store import JSONObject, TopicCostRecord, TopicRecord

_SAFE_KIND_VALUES = frozenset({"coding", "unknown"})
_SAFE_PROFILE_VALUES = frozenset({"ci", "demo", "local", "unknown"})


class TopicCostStore(Protocol):
    async def update_topic_cost(self, delta: TopicCostRecord) -> TopicCostRecord: ...


@dataclass(frozen=True)
class TopicEntryRange:
    topic_id: str
    start_seq: int
    end_seq: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty("topic_id", self.topic_id)
        _require_non_negative_int("start_seq", self.start_seq)
        if self.end_seq is not None:
            _require_non_negative_int("end_seq", self.end_seq)
            if self.end_seq < self.start_seq:
                raise ValueError("end_seq must be greater than or equal to start_seq")

    def to_dict(self) -> JSONObject:
        payload: JSONObject = {
            "topic_id": self.topic_id,
            "start_seq": self.start_seq,
        }
        if self.end_seq is not None:
            payload["end_seq"] = self.end_seq
        return payload


def topic_entry_range(topic: TopicRecord) -> TopicEntryRange:
    return TopicEntryRange(
        topic_id=topic.topic_id,
        start_seq=topic.topic_initial_seq,
        end_seq=topic.topic_finalized_seq,
    )


def topic_cost_delta(
    *,
    topic_id: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    run_count: int = 0,
    action_count: int = 0,
    validation_count: int = 0,
    tool_call_count: int = 0,
    metadata: JSONObject | None = None,
) -> TopicCostRecord:
    inferred_total = prompt_tokens + completion_tokens
    return TopicCostRecord(
        topic_id=topic_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=inferred_total if total_tokens is None else total_tokens,
        run_count=run_count,
        action_count=action_count,
        validation_count=validation_count,
        tool_call_count=tool_call_count,
        metadata=dict(metadata or {}),
    )


async def update_topic_cost(
    *,
    store: TopicCostStore,
    topic_id: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    run_count: int = 0,
    action_count: int = 0,
    validation_count: int = 0,
    tool_call_count: int = 0,
    metadata: JSONObject | None = None,
) -> TopicCostRecord:
    return await store.update_topic_cost(
        topic_cost_delta(
            topic_id=topic_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            run_count=run_count,
            action_count=action_count,
            validation_count=validation_count,
            tool_call_count=tool_call_count,
            metadata=metadata,
        )
    )


def topic_eval_provenance(
    *,
    topic: TopicRecord,
    source_ranges: tuple[TopicEntryRange, ...] | None = None,
) -> JSONObject:
    return _provenance_payload(
        topic=topic,
        source_ranges=source_ranges,
        provenance_kind="eval_result",
    )


def topic_memory_provenance(
    *,
    topic: TopicRecord,
    source_ranges: tuple[TopicEntryRange, ...] | None = None,
) -> JSONObject:
    return _provenance_payload(
        topic=topic,
        source_ranges=source_ranges,
        provenance_kind="memory_evidence",
    )


def topic_metric_attributes(
    *,
    topic: TopicRecord,
    profile: str = "unknown",
) -> dict[str, str]:
    return {
        "topic_kind": _safe_topic_kind(topic.kind),
        "topic_status": _safe_label_value(topic.status),
        "topic_profile": _safe_topic_profile(profile),
    }


def _provenance_payload(
    *,
    topic: TopicRecord,
    source_ranges: tuple[TopicEntryRange, ...] | None,
    provenance_kind: str,
) -> JSONObject:
    ranges = source_ranges if source_ranges is not None else (topic_entry_range(topic),)
    return {
        "provenance_kind": provenance_kind,
        "topic_id": topic.topic_id,
        "topic_status": topic.status,
        "topic_kind": topic.kind,
        "source_entry_ranges": [entry_range.to_dict() for entry_range in ranges],
    }


def _safe_topic_kind(value: str) -> str:
    normalized = _safe_label_value(value)
    if normalized not in _SAFE_KIND_VALUES:
        return "unknown"
    return normalized


def _safe_topic_profile(value: str) -> str:
    normalized = _safe_label_value(value)
    if normalized not in _SAFE_PROFILE_VALUES:
        return "unknown"
    return normalized


def _safe_label_value(value: str) -> str:
    _require_non_empty("label", value)
    normalized = "".join(char if char.isalnum() else "_" for char in value.lower())
    normalized = normalized.strip("_")[:64]
    return normalized or "unknown"


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
