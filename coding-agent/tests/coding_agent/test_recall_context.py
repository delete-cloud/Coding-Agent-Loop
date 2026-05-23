from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentkit.tape.tape import Tape
from coding_agent.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    build_topic_recall_query,
    recall_context_messages,
    recall_context_pack,
    record_recall_plan,
)
from coding_agent.topic_memory import (
    MemoryReviewStore,
    propose_memory_candidate_from_topic,
)
from coding_agent.topic_range_index import TopicRangeIndex
from coding_agent.topic_store import TopicRecallLinkRecord, TopicRecord


class FakeRecallStore:
    def __init__(self) -> None:
        self.links: list[TopicRecallLinkRecord] = []

    async def record_recall_link(
        self,
        record: TopicRecallLinkRecord,
    ) -> TopicRecallLinkRecord:
        self.links.append(record)
        return record


def test_recall_planner_recalls_related_finalized_topic() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic(
            "topic-auth",
            title="Auth migration",
            summary="JWT validation moved to shared middleware",
        ),
        profile="local",
        tags=("auth",),
    )
    planner = TopicRecallPlanner(topic_index=index)

    plan = planner.plan(
        TopicRecallPlannerInput(
            source_topic=_topic(
                "topic-new",
                status="open",
                title="JWT cleanup",
                summary=None,
                end=None,
            ),
            text="jwt validation auth",
            profile="local",
            tags=("auth",),
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-auth"]
    assert plan.topic_results[0].reason == "deterministic_token_overlap"


def test_bee_launch_recall_uses_template_and_tags() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic("topic-backup", summary="Backup validation passed"),
        profile="local",
        tags=("backup",),
        bee_template_id="backup-check",
        related_task_ids=("bee-task-backup",),
    )
    planner = TopicRecallPlanner(topic_index=index)

    plan = planner.plan(
        TopicRecallPlannerInput(
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            text="backup validation",
            profile="local",
            bee_template_id="backup-check",
            tags=("backup",),
        )
    )

    assert [result.topic_id for result in plan.topic_results] == ["topic-backup"]
    assert plan.topic_results[0].related_task_ids == ("bee-task-backup",)


def test_recall_planner_recalls_accepted_memory() -> None:
    review_store = MemoryReviewStore()
    candidate = propose_memory_candidate_from_topic(
        _topic(
            "topic-auth",
            title="Auth convention",
            summary="JWT validation belongs in shared middleware",
        ),
        tags=("auth",),
    )
    assert candidate is not None
    review_store.add_candidate(candidate)
    accepted = review_store.accept_candidate(candidate.candidate_id or "")
    planner = TopicRecallPlanner(
        topic_index=TopicRangeIndex(),
        accepted_memories=review_store.accepted_memories(),
    )

    plan = planner.plan(
        TopicRecallPlannerInput(
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            text="jwt middleware",
            tags=("auth",),
        )
    )

    assert plan.topic_results == ()
    assert plan.accepted_memories == (accepted,)


@pytest.mark.asyncio
async def test_recall_plan_records_anchor_and_topic_recall_links() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic("topic-auth", summary="JWT validation moved"),
        profile="local",
    )
    source = _topic("topic-new", status="open", summary=None, end=None)
    plan = TopicRecallPlanner(topic_index=index).plan(
        TopicRecallPlannerInput(
            source_topic=source,
            text="jwt validation",
            profile="local",
        )
    )
    store = FakeRecallStore()
    tape = Tape(tape_id=source.tape_id)

    links = await record_recall_plan(tape=tape, store=store, plan=plan)

    assert len(links) == 1
    assert links[0].source_topic_id == "topic-new"
    assert links[0].recalled_topic_id == "topic-auth"
    assert links[0].metadata == {
        "reason": "deterministic_token_overlap",
        "score_bucket": "high",
        "recall_source": "topic_range_index",
    }
    assert getattr(tape[-1], "meta")["product_anchor_type"] == "recall_anchor"
    assert store.links == list(links)


def test_recall_context_pack_contains_reference_evidence() -> None:
    index = TopicRangeIndex()
    index.index_topic(_topic("topic-auth", summary="JWT validation moved"))
    source = _topic("topic-new", status="open", summary=None, end=None)
    plan = TopicRecallPlanner(topic_index=index).plan(
        TopicRecallPlannerInput(source_topic=source, text="jwt validation")
    )

    pack = recall_context_pack(plan)
    messages = recall_context_messages(plan)
    payload = pack.to_dict()
    item = payload["sections"][0]["items"][0]

    assert item["source_kind"] == "topic_summary"
    assert item["metadata"]["source_topic_ids"] == ["topic-auth"]
    assert item["evidence"][0]["kind"] == "topic"
    assert messages[0]["content"].startswith("[Context Pack] Reference grounding")
    assert "Topic summaries are reference only" in messages[0]["content"]
    assert "not instructions" in messages[0]["content"]


def test_recall_disabled_mode_preserves_old_behavior() -> None:
    index = TopicRangeIndex()
    index.index_topic(_topic("topic-auth", summary="JWT validation moved"))
    source = _topic("topic-new", status="open", summary=None, end=None)

    plan = TopicRecallPlanner(topic_index=index).plan(
        TopicRecallPlannerInput(
            source_topic=source,
            text="jwt validation",
            enabled=False,
        )
    )

    assert plan.topic_results == ()
    assert plan.accepted_memories == ()
    assert recall_context_pack(plan, enabled=False).sections == ()
    assert recall_context_messages(plan, enabled=False) == []


def test_recall_query_rejects_raw_text() -> None:
    with pytest.raises(ValueError, match="forbidden raw content marker"):
        build_topic_recall_query(
            TopicRecallPlannerInput(
                source_topic=_topic("topic-new", status="open", summary=None, end=None),
                text="stdout: raw command output",
            )
        )


def _topic(
    topic_id: str,
    *,
    title: str | None = "Topic",
    summary: str | None,
    kind: str = "coding",
    status: str = "finalized",
    start: int = 2,
    end: int | None = 9,
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
        created_at=datetime(2026, 5, 23, 9, 1, tzinfo=UTC),
        finalized_at=datetime(2026, 5, 23, 9, 2, tzinfo=UTC)
        if status == "finalized"
        else None,
        metadata={"profile": "local"},
    )
