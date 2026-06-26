from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coding_agent.topics.store import (
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 26, hour, minute, tzinfo=UTC)


def _open_topic(
    topic_id: str,
    *,
    tape_id: str = "tape-1",
    created_at: datetime | None = None,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id=tape_id,
        session_id="session-1",
        kind="coding",
        status="open",
        title="Topic",
        summary=None,
        owner="local",
        topic_initial_seq=3,
        topic_finalized_seq=None,
        created_at=created_at or _dt(9),
        finalized_at=None,
        metadata={"profile": "local"},
    )


@pytest.fixture
def sqlite_store(tmp_path: Path) -> SQLiteTopicStore:
    return SQLiteTopicStore(tmp_path / "local.sqlite3")


@pytest.mark.asyncio
async def test_sqlite_topic_store_create_finalize_abort_and_list(
    sqlite_store: SQLiteTopicStore,
) -> None:
    await sqlite_store.create_topic(_open_topic("topic-1", created_at=_dt(9)))
    await sqlite_store.create_topic(
        TopicRecord(
            topic_id="topic-2",
            tape_id="tape-2",
            session_id="session-1",
            kind="coding",
            status="open",
            title="Docs",
            summary=None,
            owner=None,
            topic_initial_seq=1,
            topic_finalized_seq=None,
            created_at=_dt(9, 5),
            finalized_at=None,
            metadata={},
        )
    )

    finalized = await sqlite_store.finalize_topic(
        "topic-1",
        summary="Sanitized topic summary",
        topic_finalized_seq=9,
        finalized_at=_dt(10),
        metadata={"status_reason": "done"},
    )
    aborted = await sqlite_store.abort_topic(
        "topic-2",
        summary="Stopped before completion",
        topic_finalized_seq=4,
        finalized_at=_dt(10, 5),
        metadata={"status_reason": "aborted"},
    )

    assert finalized.status == "finalized"
    assert finalized.summary == "Sanitized topic summary"
    assert aborted.status == "aborted"
    assert await sqlite_store.load_topic("topic-1") == finalized
    assert [
        topic.topic_id
        for topic in await sqlite_store.list_topics(
            session_id="session-1",
            status="finalized",
        )
    ] == ["topic-1"]


@pytest.mark.asyncio
async def test_sqlite_topic_store_cursor_paginates_by_created_at_topic_id(
    sqlite_store: SQLiteTopicStore,
) -> None:
    await sqlite_store.create_topic(
        _open_topic("topic-b", tape_id="tape-b", created_at=_dt(9))
    )
    await sqlite_store.create_topic(
        _open_topic("topic-a", tape_id="tape-a", created_at=_dt(9))
    )
    await sqlite_store.create_topic(
        _open_topic("topic-c", tape_id="tape-c", created_at=_dt(9, 1))
    )

    first_page = await sqlite_store.list_topics(session_id="session-1", limit=1)
    second_page = await sqlite_store.list_topics(
        session_id="session-1",
        after_created_at=first_page[-1].created_at,
        after_topic_id=first_page[-1].topic_id,
        limit=10,
    )

    assert [topic.topic_id for topic in first_page] == ["topic-a"]
    assert [topic.topic_id for topic in second_page] == ["topic-b", "topic-c"]
    with pytest.raises(ValueError, match="provided together"):
        await sqlite_store.list_topics(after_created_at=_dt(9))


@pytest.mark.asyncio
async def test_sqlite_topic_store_enforces_open_topic_and_parent_constraints(
    sqlite_store: SQLiteTopicStore,
) -> None:
    await sqlite_store.create_topic(_open_topic("topic-1"))
    duplicate_open = TopicRecord(
        topic_id="topic-2",
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status="open",
        title="Duplicate",
        summary=None,
        owner=None,
        topic_initial_seq=4,
        topic_finalized_seq=None,
        created_at=_dt(9, 5),
        finalized_at=None,
        metadata={},
    )

    with pytest.raises(sqlite3.IntegrityError):
        await sqlite_store.create_topic(duplicate_open)
    with pytest.raises(sqlite3.IntegrityError):
        await sqlite_store.record_topic_anchor(
            TopicAnchorRecord(
                topic_id="missing",
                tape_id="tape-1",
                seq=3,
                anchor_type="topic_initial",
                entry_id="entry-1",
            )
        )
    with pytest.raises(sqlite3.IntegrityError):
        await sqlite_store.update_topic_cost(TopicCostRecord(topic_id="missing"))


@pytest.mark.asyncio
async def test_sqlite_topic_store_records_children_and_costs(
    sqlite_store: SQLiteTopicStore,
) -> None:
    await sqlite_store.create_topic(_open_topic("topic-1"))
    await sqlite_store.create_topic(
        TopicRecord(
            topic_id="topic-0",
            tape_id="tape-0",
            session_id="session-1",
            kind="coding",
            status="finalized",
            title="Previous",
            summary="Previous summary",
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=2,
            created_at=_dt(8),
            finalized_at=_dt(8, 30),
            metadata={},
        )
    )

    anchor = await sqlite_store.record_topic_anchor(
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=3,
            anchor_type="topic_initial",
            entry_id="entry-1",
            metadata={"label": "safe"},
        )
    )
    recall = await sqlite_store.record_recall_link(
        TopicRecallLinkRecord(
            source_topic_id="topic-1",
            recalled_topic_id="topic-0",
            relation="summary_recall",
            anchor_seq=3,
            metadata={"reason": "deterministic"},
        )
    )
    first_cost = await sqlite_store.update_topic_cost(
        TopicCostRecord(topic_id="topic-1", total_tokens=10, run_count=1)
    )
    second_cost = await sqlite_store.update_topic_cost(
        TopicCostRecord(topic_id="topic-1", total_tokens=7, run_count=1)
    )

    assert await sqlite_store.list_topic_anchors("topic-1") == [anchor]
    assert await sqlite_store.list_recall_links("topic-1") == [recall]
    assert first_cost.total_tokens == 10
    assert second_cost.total_tokens == 17
    assert second_cost.run_count == 2


@pytest.mark.asyncio
async def test_sqlite_topic_store_serializes_json_and_utc_datetimes_stably(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.sqlite3"
    store = SQLiteTopicStore(path)
    created = await store.create_topic(_open_topic("topic-1"))

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT created_at, metadata FROM topics WHERE topic_id = ?",
            ("topic-1",),
        ).fetchone()

    assert row == ("2026-06-26T09:00:00.000000Z", '{"profile":"local"}')
    assert created.created_at == _dt(9)
