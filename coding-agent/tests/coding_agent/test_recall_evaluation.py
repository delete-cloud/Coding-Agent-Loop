from __future__ import annotations

import json
from datetime import UTC, datetime

from coding_agent.topics.recall_evaluation import (
    RecallEvalCase,
    RecallEvalVariant,
    evaluate_recall_variants,
)
from coding_agent.topics.memory import (
    MemoryReviewStore,
    propose_memory_candidate_from_topic,
)
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.store import TopicRecord


def test_recall_eval_report_compares_all_recall_variants() -> None:
    index = TopicRangeIndex()
    topic = _topic(
        "topic-auth",
        title="Auth migration",
        summary="JWT validation moved to shared middleware",
    )
    index.index_topic(topic, profile="local", tags=("auth",))
    review_store = MemoryReviewStore()
    candidate = propose_memory_candidate_from_topic(topic, tags=("auth",))
    assert candidate is not None
    review_store.add_candidate(candidate)
    accepted = review_store.accept_candidate(candidate.candidate_id or "")

    report = evaluate_recall_variants(
        RecallEvalCase(
            case_id="recall-auth",
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            query_text="jwt middleware auth",
            profile="local",
            tags=("auth",),
            expected_topic_ids=("topic-auth",),
            expected_memory_ids=(accepted.candidate.candidate_id or "",),
        ),
        topic_index=index,
        accepted_memories=review_store.accepted_memories(),
    )

    assert [result.variant for result in report.results] == [
        RecallEvalVariant.NO_RECALL,
        RecallEvalVariant.ACCEPTED_MEMORY,
        RecallEvalVariant.TOPIC_RANGE,
        RecallEvalVariant.TOPIC_AND_MEMORY,
    ]
    assert report.result_for(RecallEvalVariant.NO_RECALL).candidate_count == 0
    accepted_result = report.result_for(RecallEvalVariant.ACCEPTED_MEMORY)
    assert accepted_result.memory_ids == (accepted.candidate.candidate_id,)
    assert accepted_result.matched_expected == 1
    topic_result = report.result_for(RecallEvalVariant.TOPIC_RANGE)
    assert topic_result.topic_ids == ("topic-auth",)
    assert topic_result.matched_expected == 1
    both_result = report.result_for(RecallEvalVariant.TOPIC_AND_MEMORY)
    assert both_result.candidate_count == 2
    assert both_result.matched_expected == 2


def test_recall_eval_report_serialization_omits_raw_summaries() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic(
            "topic-auth",
            title="Auth migration",
            summary="JWT validation moved to shared middleware",
        ),
        profile="local",
    )

    report = evaluate_recall_variants(
        RecallEvalCase(
            case_id="recall-auth",
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            query_text="jwt validation",
            profile="local",
        ),
        topic_index=index,
    )

    payload = json.dumps(report.to_dict(), sort_keys=True)

    assert "topic-auth" in payload
    assert "JWT validation moved to shared middleware" not in payload
    assert "query_text" not in payload


def test_recall_eval_case_rejects_raw_query_text() -> None:
    try:
        RecallEvalCase(
            case_id="bad",
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            query_text="stdout: raw command output",
        )
    except ValueError as exc:
        assert "forbidden raw content marker" in str(exc)
    else:
        raise AssertionError("raw recall query text should be rejected")


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
