from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.topic_range_index import (
    TopicRangeIndex,
    TopicRangeSearchQuery,
)
from coding_agent.topic_store import TopicRecord


def test_topic_range_index_indexes_finalized_topic_and_searches_text() -> None:
    index = TopicRangeIndex()
    document = index.index_topic(
        _topic(
            "topic-auth",
            title="Auth migration",
            summary="JWT validation moved to shared auth middleware",
        ),
        profile="local",
        tags=("auth", "jwt"),
    )

    results = index.search(TopicRangeSearchQuery(text="jwt validation"))

    assert document is not None
    assert [result.topic_id for result in results] == ["topic-auth"]
    assert results[0].summary == "JWT validation moved to shared auth middleware"
    assert results[0].reason == "deterministic_token_overlap"
    assert results[0].source_ranges[0].to_dict() == {
        "topic_id": "topic-auth",
        "start_seq": 2,
        "end_seq": 9,
    }
    assert results[0].tags == ("auth", "jwt")


def test_topic_range_index_skips_open_topic_by_default() -> None:
    index = TopicRangeIndex()

    document = index.index_topic(
        _topic(
            "topic-open",
            status="open",
            title="In-progress auth",
            summary="JWT validation still changing",
            end=None,
        )
    )

    assert document is None
    assert index.search(TopicRangeSearchQuery(text="jwt")) == []


def test_topic_range_index_can_explicitly_index_open_topic() -> None:
    index = TopicRangeIndex()

    document = index.index_topic(
        _topic(
            "topic-open",
            status="open",
            title="In-progress auth",
            summary="JWT validation still changing",
            end=None,
        ),
        allow_open=True,
    )

    assert document is not None
    results = index.search(TopicRangeSearchQuery(text="jwt", status="open"))
    assert [result.topic_id for result in results] == ["topic-open"]
    assert results[0].source_ranges[0].to_dict() == {
        "topic_id": "topic-open",
        "start_seq": 2,
    }


def test_topic_range_index_searches_kind_profile_tag_status_and_time() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic(
            "topic-auth",
            created_at=_dt(10),
            kind="coding",
            summary="Auth middleware migration",
        ),
        profile="local",
        tags=("auth", "middleware"),
    )
    index.index_topic(
        _topic(
            "topic-docs",
            created_at=_dt(20),
            kind="documentation",
            summary="Docs navigation cleanup",
        ),
        profile="ci",
        tags=("docs",),
    )

    results = index.search(
        TopicRangeSearchQuery(
            kind="coding",
            profile="local",
            tags=("auth",),
            status="finalized",
            created_after=_dt(5),
            created_before=_dt(15),
        )
    )

    assert [result.topic_id for result in results] == ["topic-auth"]
    assert results[0].reason == "filtered_match"


def test_topic_range_index_indexes_bee_task_topic_metadata() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic(
            "topic-bee",
            title="Backup check",
            summary="Validated backup readiness",
        ),
        profile="local",
        tags=("backup", "readiness"),
        bee_template_id="backup-check",
        related_task_ids=("bee-task-1",),
        report_refs=("reports/backup-check.md",),
        evidence_refs=("evidence/executor-run.md",),
        report_summary="Backup validation report accepted",
        evidence_summaries=("Executor validation passed",),
    )

    results = index.search(
        TopicRangeSearchQuery(
            text="executor validation",
            bee_template_id="backup-check",
            tags=("backup",),
        )
    )

    assert [result.topic_id for result in results] == ["topic-bee"]
    assert results[0].bee_template_id == "backup-check"
    assert results[0].related_task_ids == ("bee-task-1",)
    assert results[0].report_refs == ("reports/backup-check.md",)
    assert results[0].evidence_refs == ("evidence/executor-run.md",)


def test_topic_range_index_records_pack_template_metadata() -> None:
    index = TopicRangeIndex()
    index.index_topic(
        _topic("topic-bee", title="Backup check", summary="Validated backup readiness"),
        profile="local",
        tags=("backup",),
        bee_pack_id="pack-alpha",
        bee_template_id="backup-check",
        domain_profile="maintenance",
        template_kind="maintenance",
    )

    results = index.search(
        TopicRangeSearchQuery(
            text="backup",
            bee_pack_id="pack-alpha",
            domain_profile="maintenance",
            tags=("backup",),
        )
    )

    assert [result.topic_id for result in results] == ["topic-bee"]
    assert results[0].bee_pack_id == "pack-alpha"
    assert results[0].bee_template_id == "backup-check"
    assert results[0].domain_profile == "maintenance"
    assert results[0].template_kind == "maintenance"


def test_topic_range_index_rejects_raw_evidence_and_report_text() -> None:
    index = TopicRangeIndex()

    forbidden_summaries = (
        ("evidence_summaries", ("stdout: raw command output",), None),
        ("evidence_summaries", ("stderr=raw command output",), None),
        ("evidence_summaries", ("command: pytest -q",), None),
        ("report_summary", (), "raw_log captured output"),
        ("report_summary", (), "password: hunter2"),
        ("report_summary", (), "secret: value"),
        ("report_summary", (), "prompt: raw task prompt"),
        ("report_summary", (), "message: raw user message"),
        ("report_summary", (), "result: raw model result"),
    )
    for field, evidence_summaries, report_summary in forbidden_summaries:
        with pytest.raises(ValueError, match="forbidden raw content marker"):
            index.index_topic(
                _topic(f"topic-raw-{field}", summary="Safe topic summary"),
                evidence_summaries=evidence_summaries,
                report_summary=report_summary,
            )

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        index.index_topic(
            _topic("topic-raw-summary", summary="content: raw message"),
        )


@pytest.mark.parametrize(
    "text",
    [
        "token=secret",
        "secret: value",
        "stdout=trace",
        "stderr: trace",
        "command: pytest -q",
        "prompt: raw request",
    ],
)
def test_topic_range_index_rejects_secret_like_search_text(text: str) -> None:
    with pytest.raises(ValueError, match="forbidden raw content marker"):
        TopicRangeSearchQuery(text=text)


def _topic(
    topic_id: str,
    *,
    title: str | None = "Topic",
    summary: str | None,
    kind: str = "coding",
    status: str = "finalized",
    start: int = 2,
    end: int | None = 9,
    created_at: datetime | None = None,
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
        created_at=created_at or _dt(1),
        finalized_at=_dt(2) if status == "finalized" else None,
        metadata={"profile": "local"},
    )


def _dt(minute: int) -> datetime:
    return datetime(2026, 5, 23, 9, minute, tzinfo=UTC)
