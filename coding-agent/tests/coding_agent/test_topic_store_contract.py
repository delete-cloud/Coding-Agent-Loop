from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.topics.store import (
    PGTopicStore,
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from tests.coding_agent.test_topic_store import FakeTopicPool


@pytest.fixture(params=("pg", "sqlite"))
def topic_store(request: pytest.FixtureRequest, tmp_path) -> Any:
    if request.param == "sqlite":
        return SQLiteTopicStore(tmp_path / "local.sqlite3")

    fake_pool = FakeTopicPool()

    async def fake_pool_factory(**_: object) -> AsyncPGPool:
        return cast(AsyncPGPool, fake_pool)

    return PGTopicStore(
        pool=PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
    )


@pytest.mark.asyncio
async def test_topic_store_contract_create_is_idempotent_by_topic_id(
    topic_store: Any,
) -> None:
    created = await topic_store.create_topic(_topic("topic-1", title="Original"))
    loaded = await topic_store.create_topic(_topic("topic-1", title="Changed"))

    assert loaded.topic_id == created.topic_id
    assert loaded.title == "Original"


@pytest.mark.asyncio
async def test_topic_store_contract_rejects_duplicate_open_topic_for_session_tape(
    topic_store: Any,
) -> None:
    await topic_store.create_topic(_topic("topic-1"))

    with pytest.raises(Exception):
        await topic_store.create_topic(_topic("topic-2"))


@pytest.mark.asyncio
async def test_topic_store_contract_rejects_orphan_anchor_recall_and_cost_records(
    topic_store: Any,
) -> None:
    with pytest.raises(Exception):
        await topic_store.record_topic_anchor(
            TopicAnchorRecord(
                topic_id="missing",
                tape_id="tape-1",
                seq=1,
                anchor_type="topic_initial",
                entry_id=None,
            )
        )
    with pytest.raises(Exception):
        await topic_store.record_recall_link(
            TopicRecallLinkRecord(
                source_topic_id="missing-source",
                recalled_topic_id="missing-recalled",
                relation="summary_recall",
            )
        )
    with pytest.raises(Exception):
        await topic_store.update_topic_cost(TopicCostRecord(topic_id="missing"))


@pytest.mark.asyncio
async def test_topic_store_contract_rejects_anchor_tape_mismatch(
    topic_store: Any,
) -> None:
    await topic_store.create_topic(_topic("topic-1", tape_id="tape-1"))

    with pytest.raises(ValueError):
        await topic_store.record_topic_anchor(
            TopicAnchorRecord(
                topic_id="topic-1",
                tape_id="other-tape",
                seq=1,
                anchor_type="topic_initial",
                entry_id=None,
            )
        )


@pytest.mark.asyncio
async def test_topic_store_contract_cursor_requires_created_at_and_topic_id_together(
    topic_store: Any,
) -> None:
    with pytest.raises(ValueError):
        await topic_store.list_topics(after_created_at=_dt(9), limit=10)
    with pytest.raises(ValueError):
        await topic_store.list_topics(after_topic_id="topic-1", limit=10)


@pytest.mark.asyncio
async def test_topic_store_contract_cursor_paginates_by_created_at_topic_id_with_equal_timestamp_tiebreak(
    topic_store: Any,
) -> None:
    await topic_store.create_topic(_topic("topic-b", tape_id="tape-b"))
    await topic_store.create_topic(_topic("topic-a", tape_id="tape-a"))
    await topic_store.create_topic(_topic("topic-c", tape_id="tape-c"))

    first_page = await topic_store.list_topics(limit=2)
    second_page = await topic_store.list_topics(
        after_created_at=first_page[-1].created_at,
        after_topic_id=first_page[-1].topic_id,
        limit=2,
    )

    assert [topic.topic_id for topic in first_page] == ["topic-a", "topic-b"]
    assert [topic.topic_id for topic in second_page] == ["topic-c"]


@pytest.mark.asyncio
async def test_topic_store_contract_records_anchors_recall_links_and_cost_increments(
    topic_store: Any,
) -> None:
    await topic_store.create_topic(_topic("topic-1", tape_id="tape-1"))
    await topic_store.create_topic(_topic("topic-2", tape_id="tape-2"))

    await topic_store.record_topic_anchor(
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=1,
            anchor_type="topic_initial",
            entry_id="entry-1",
        )
    )
    await topic_store.record_recall_link(
        TopicRecallLinkRecord(
            source_topic_id="topic-1",
            recalled_topic_id="topic-2",
            relation="summary_recall",
        )
    )
    await topic_store.update_topic_cost(
        TopicCostRecord(topic_id="topic-1", prompt_tokens=1, total_tokens=1)
    )
    cost = await topic_store.update_topic_cost(
        TopicCostRecord(topic_id="topic-1", prompt_tokens=2, total_tokens=2)
    )

    assert [anchor.seq for anchor in await topic_store.list_topic_anchors("topic-1")]
    assert [
        link.recalled_topic_id
        for link in await topic_store.list_recall_links("topic-1")
    ] == ["topic-2"]
    assert cost.prompt_tokens == 3
    assert cost.total_tokens == 3


def _topic(
    topic_id: str,
    *,
    title: str = "Topic",
    tape_id: str = "tape-1",
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id=tape_id,
        session_id="session-1",
        kind="coding",
        status="open",
        title=title,
        summary=None,
        owner="local",
        topic_initial_seq=1,
        topic_finalized_seq=None,
        created_at=_dt(9),
        finalized_at=None,
        metadata={},
    )


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 26, hour, tzinfo=UTC)
