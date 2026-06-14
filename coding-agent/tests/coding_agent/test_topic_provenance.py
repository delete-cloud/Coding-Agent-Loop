from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.topics.provenance import (
    TopicEntryRange,
    topic_cost_delta,
    topic_entry_range,
    topic_eval_provenance,
    topic_memory_provenance,
    topic_metric_attributes,
    update_topic_cost,
)
from coding_agent.topics.store import TopicCostRecord, TopicRecord


class FakeTopicCostStore:
    def __init__(self) -> None:
        self.updated: list[TopicCostRecord] = []

    async def update_topic_cost(self, delta: TopicCostRecord) -> TopicCostRecord:
        self.updated.append(delta)
        return delta


def _topic() -> TopicRecord:
    return TopicRecord(
        topic_id="topic-auth",
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status="finalized",
        title="Auth cleanup",
        summary="JWT validation moved to auth service",
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=8,
        created_at=datetime(2026, 5, 21, 9, tzinfo=UTC),
        finalized_at=datetime(2026, 5, 21, 10, tzinfo=UTC),
        metadata={"profile": "local"},
    )


def _topic_with_kind(kind: str) -> TopicRecord:
    topic = _topic()
    return TopicRecord(
        topic_id=topic.topic_id,
        tape_id=topic.tape_id,
        session_id=topic.session_id,
        kind=kind,
        status=topic.status,
        title=topic.title,
        summary=topic.summary,
        owner=topic.owner,
        topic_initial_seq=topic.topic_initial_seq,
        topic_finalized_seq=topic.topic_finalized_seq,
        created_at=topic.created_at,
        finalized_at=topic.finalized_at,
        metadata=topic.metadata,
    )


def test_topic_cost_delta_aggregates_usage_action_validation_and_run_counts() -> None:
    delta = topic_cost_delta(
        topic_id="topic-auth",
        prompt_tokens=10,
        completion_tokens=7,
        run_count=1,
        action_count=2,
        validation_count=3,
        tool_call_count=4,
        metadata={"source": "unit"},
    )

    assert delta == TopicCostRecord(
        topic_id="topic-auth",
        prompt_tokens=10,
        completion_tokens=7,
        total_tokens=17,
        run_count=1,
        action_count=2,
        validation_count=3,
        tool_call_count=4,
        metadata={"source": "unit"},
    )


@pytest.mark.asyncio
async def test_update_topic_cost_persists_delta_through_store() -> None:
    store = FakeTopicCostStore()

    updated = await update_topic_cost(
        store=store,
        topic_id="topic-auth",
        prompt_tokens=5,
        completion_tokens=4,
        total_tokens=12,
        run_count=1,
    )

    assert updated.total_tokens == 12
    assert store.updated == [updated]


def test_eval_result_topic_provenance_includes_topic_and_source_range() -> None:
    provenance = topic_eval_provenance(topic=_topic())

    assert provenance == {
        "provenance_kind": "eval_result",
        "topic_id": "topic-auth",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 8}
        ],
    }


def test_memory_evidence_topic_provenance_uses_explicit_range() -> None:
    provenance = topic_memory_provenance(
        topic=_topic(),
        source_ranges=(TopicEntryRange("topic-auth", start_seq=3, end_seq=5),),
    )

    assert provenance["provenance_kind"] == "memory_evidence"
    assert provenance["topic_id"] == "topic-auth"
    assert provenance["source_entry_ranges"] == [
        {"topic_id": "topic-auth", "start_seq": 3, "end_seq": 5}
    ]


def test_topic_metric_attributes_are_low_cardinality_and_exclude_topic_id() -> None:
    labels = topic_metric_attributes(topic=_topic(), profile="local")

    assert labels == {
        "topic_kind": "coding",
        "topic_status": "finalized",
        "topic_profile": "local",
    }
    assert "topic_id" not in labels


def test_topic_metric_attributes_normalize_unlisted_kind_to_unknown() -> None:
    labels = topic_metric_attributes(
        topic=_topic_with_kind("customer_auth_cleanup_123"),
        profile="local",
    )

    assert labels["topic_kind"] == "unknown"


def test_topic_entry_range_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="end_seq"):
        TopicEntryRange("topic-auth", start_seq=5, end_seq=3)

    assert topic_entry_range(_topic()).to_dict() == {
        "topic_id": "topic-auth",
        "start_seq": 2,
        "end_seq": 8,
    }
