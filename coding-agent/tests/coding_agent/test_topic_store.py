from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.topics.store import (
    PGTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)


class FakeTopicPool:
    def __init__(self) -> None:
        self.topics: dict[str, dict[str, object]] = {}
        self.anchors: dict[tuple[str, int, str], dict[str, object]] = {}
        self.recall_links: dict[tuple[str, str, str], dict[str, object]] = {}
        self.costs: dict[str, dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS topics" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO topics" in query:
            existing = self.topics.get(cast(str, args[0]))
            if existing is not None:
                return existing
            row = _topic_row(*args)
            self.topics[cast(str, row["topic_id"])] = row
            return row
        if "UPDATE topics" in query and "status = 'finalized'" in query:
            return self._close_topic("finalized", args)
        if "UPDATE topics" in query and "status = 'aborted'" in query:
            return self._close_topic("aborted", args)
        if "SELECT * FROM topics WHERE topic_id = $1" in query:
            return self.topics.get(cast(str, args[0]))
        if "SELECT * FROM topics" in query and "status = 'open'" in query:
            session_id, tape_id = args
            rows = [
                row
                for row in self.topics.values()
                if row["session_id"] == session_id
                and row["tape_id"] == tape_id
                and row["status"] == "open"
            ]
            rows.sort(key=lambda row: cast(datetime, row["created_at"]), reverse=True)
            return rows[0] if rows else None
        if "INSERT INTO topic_anchors" in query:
            topic_id, tape_id, seq, anchor_type, entry_id, metadata = args
            row = {
                "topic_id": topic_id,
                "tape_id": tape_id,
                "seq": seq,
                "anchor_type": anchor_type,
                "entry_id": entry_id,
                "metadata": metadata,
                "created_at": _dt(9, 10),
            }
            self.anchors[
                (cast(str, topic_id), cast(int, seq), cast(str, anchor_type))
            ] = row
            return row
        if "INSERT INTO topic_recall_links" in query:
            (
                source_topic_id,
                recalled_topic_id,
                relation,
                anchor_seq,
                source_entry_start_seq,
                source_entry_end_seq,
                metadata,
            ) = args
            row = {
                "source_topic_id": source_topic_id,
                "recalled_topic_id": recalled_topic_id,
                "relation": relation,
                "anchor_seq": anchor_seq,
                "source_entry_start_seq": source_entry_start_seq,
                "source_entry_end_seq": source_entry_end_seq,
                "metadata": metadata,
                "created_at": _dt(9, 20),
            }
            key = (
                cast(str, source_topic_id),
                cast(str, recalled_topic_id),
                cast(str, relation),
            )
            self.recall_links[key] = row
            return row
        if "INSERT INTO topic_costs" in query:
            row = self._upsert_cost(args)
            self.costs[cast(str, row["topic_id"])] = row
            return row
        if "SELECT * FROM topic_costs WHERE topic_id = $1" in query:
            return self.costs.get(cast(str, args[0]))
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM topics" in query:
            session_id, tape_id, status, limit = args
            rows = [
                row
                for row in self.topics.values()
                if (session_id is None or row["session_id"] == session_id)
                and (tape_id is None or row["tape_id"] == tape_id)
                and (status is None or row["status"] == status)
            ]
            rows.sort(key=lambda row: cast(datetime, row["created_at"]))
            return rows[: cast(int, limit)]
        if "SELECT * FROM topic_anchors" in query:
            topic_id = cast(str, args[0])
            rows = [row for key, row in self.anchors.items() if key[0] == topic_id]
            rows.sort(
                key=lambda row: (cast(int, row["seq"]), cast(str, row["anchor_type"]))
            )
            return rows
        if "SELECT * FROM topic_recall_links" in query:
            source_topic_id = cast(str, args[0])
            rows = [
                row
                for key, row in self.recall_links.items()
                if key[0] == source_topic_id
            ]
            rows.sort(key=lambda row: cast(str, row["recalled_topic_id"]))
            return rows
        raise AssertionError(f"unexpected fetch query: {query}")

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeTopicPool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _close_topic(
        self,
        status: str,
        args: tuple[object, ...],
    ) -> dict[str, object] | None:
        topic_id, summary, topic_finalized_seq, finalized_at, metadata = args
        row = self.topics.get(cast(str, topic_id))
        if row is None or row["status"] != "open":
            return None
        if topic_finalized_seq is not None and cast(int, topic_finalized_seq) < cast(
            int, row["topic_initial_seq"]
        ):
            return None
        row.update(
            {
                "status": status,
                "summary": summary,
                "topic_finalized_seq": topic_finalized_seq,
                "finalized_at": finalized_at,
                "metadata": metadata,
            }
        )
        return row

    def _upsert_cost(self, args: tuple[object, ...]) -> dict[str, object]:
        (
            topic_id,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            run_count,
            action_count,
            validation_count,
            tool_call_count,
            metadata,
        ) = args
        existing = self.costs.get(cast(str, topic_id))
        if existing is None:
            return {
                "topic_id": topic_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "run_count": run_count,
                "action_count": action_count,
                "validation_count": validation_count,
                "tool_call_count": tool_call_count,
                "metadata": metadata,
                "updated_at": _dt(9, 30),
            }
        for key, delta in (
            ("prompt_tokens", prompt_tokens),
            ("completion_tokens", completion_tokens),
            ("total_tokens", total_tokens),
            ("run_count", run_count),
            ("action_count", action_count),
            ("validation_count", validation_count),
            ("tool_call_count", tool_call_count),
        ):
            existing[key] = cast(int, existing[key]) + cast(int, delta)
        existing["metadata"] = metadata
        existing["updated_at"] = _dt(9, 31)
        return existing


@pytest.fixture
def fake_pool() -> FakeTopicPool:
    return FakeTopicPool()


@pytest.fixture
def store(fake_pool: FakeTopicPool) -> PGTopicStore:
    async def fake_pool_factory(**_: object) -> AsyncPGPool:
        return cast(AsyncPGPool, fake_pool)

    return PGTopicStore(
        pool=PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
    )


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 21, hour, minute, tzinfo=UTC)


def _topic_row(*args: object) -> dict[str, object]:
    (
        topic_id,
        tape_id,
        session_id,
        kind,
        status,
        title,
        summary,
        owner,
        topic_initial_seq,
        topic_finalized_seq,
        created_at,
        finalized_at,
        metadata,
    ) = args
    return {
        "topic_id": topic_id,
        "tape_id": tape_id,
        "session_id": session_id,
        "kind": kind,
        "status": status,
        "title": title,
        "summary": summary,
        "owner": owner,
        "topic_initial_seq": topic_initial_seq,
        "topic_finalized_seq": topic_finalized_seq,
        "created_at": created_at,
        "finalized_at": finalized_at,
        "metadata": metadata,
    }


def _open_topic(topic_id: str, seq: int = 3) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status="open",
        title="Auth cleanup",
        summary=None,
        owner="local",
        topic_initial_seq=seq,
        topic_finalized_seq=None,
        created_at=_dt(9),
        finalized_at=None,
        metadata={"profile": "local"},
    )


@pytest.mark.asyncio
async def test_topic_store_schema_is_idempotent(
    store: PGTopicStore,
    fake_pool: FakeTopicPool,
) -> None:
    await store.create_topic(_open_topic("topic-1"))
    await store.load_topic("topic-1")
    await store.record_topic_anchor(
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=3,
            anchor_type="topic_initial",
            entry_id="entry-1",
            metadata={},
        )
    )

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS topics" in query
    ]

    assert len(schema_calls) == 1
    assert "CREATE TABLE IF NOT EXISTS topic_anchors" in schema_calls[0]
    assert "CREATE TABLE IF NOT EXISTS topic_recall_links" in schema_calls[0]
    assert "CREATE TABLE IF NOT EXISTS topic_costs" in schema_calls[0]
    assert "agent_tapes" not in schema_calls[0]


@pytest.mark.asyncio
async def test_topic_create_finalize_abort_and_list(
    store: PGTopicStore,
) -> None:
    created = await store.create_topic(_open_topic("topic-1"))
    await store.create_topic(
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

    finalized = await store.finalize_topic(
        "topic-1",
        summary="Sanitized topic summary",
        topic_finalized_seq=9,
        finalized_at=_dt(10),
        metadata={"status_reason": "done"},
    )
    aborted = await store.abort_topic(
        "topic-2",
        summary="Stopped before completion",
        topic_finalized_seq=4,
        finalized_at=_dt(10, 5),
        metadata={"status_reason": "aborted"},
    )
    loaded = await store.load_topic("topic-1")
    finalized_topics = await store.list_topics(
        session_id="session-1", status="finalized"
    )

    assert created.status == "open"
    assert finalized.status == "finalized"
    assert finalized.topic_finalized_seq == 9
    assert finalized.summary == "Sanitized topic summary"
    assert aborted.status == "aborted"
    assert loaded == finalized
    assert [topic.topic_id for topic in finalized_topics] == ["topic-1"]


@pytest.mark.asyncio
async def test_create_topic_returns_existing_record_without_reopening_closed_topic(
    store: PGTopicStore,
) -> None:
    await store.create_topic(_open_topic("topic-1"))
    finalized = await store.finalize_topic(
        "topic-1",
        summary="Closed safely",
        topic_finalized_seq=9,
        finalized_at=_dt(10),
        metadata={"status_reason": "done"},
    )

    duplicate = await store.create_topic(_open_topic("topic-1"))

    assert duplicate == finalized
    assert duplicate.status == "finalized"
    assert duplicate.topic_finalized_seq == 9


@pytest.mark.asyncio
async def test_finalize_rejects_range_before_topic_initial_without_mutating(
    store: PGTopicStore,
) -> None:
    await store.create_topic(_open_topic("topic-1", seq=10))

    with pytest.raises(KeyError, match="open topic not found"):
        await store.finalize_topic(
            "topic-1",
            summary="Invalid range",
            topic_finalized_seq=5,
            finalized_at=_dt(10),
            metadata={},
        )
    loaded = await store.load_topic("topic-1")

    assert loaded is not None
    assert loaded.status == "open"
    assert loaded.topic_finalized_seq is None
    assert loaded.summary is None


@pytest.mark.asyncio
async def test_find_open_topic_by_session_and_tape(store: PGTopicStore) -> None:
    await store.create_topic(_open_topic("topic-1"))

    found = await store.find_open_topic(session_id="session-1", tape_id="tape-1")
    missing = await store.find_open_topic(session_id="session-1", tape_id="missing")

    assert found is not None
    assert found.topic_id == "topic-1"
    assert missing is None


@pytest.mark.asyncio
async def test_record_topic_anchor_and_recall_link(store: PGTopicStore) -> None:
    await store.create_topic(_open_topic("topic-1"))
    await store.create_topic(_open_topic("topic-0", seq=0))

    initial = await store.record_topic_anchor(
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=3,
            anchor_type="topic_initial",
            entry_id="entry-initial",
            metadata={"encoded_as": "topic_start"},
        )
    )
    finalized = await store.record_topic_anchor(
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=9,
            anchor_type="topic_finalized",
            entry_id="entry-finalized",
            metadata={"encoded_as": "topic_end"},
        )
    )
    recall = await store.record_recall_link(
        TopicRecallLinkRecord(
            source_topic_id="topic-1",
            recalled_topic_id="topic-0",
            relation="summary_recall",
            anchor_seq=5,
            source_entry_start_seq=3,
            source_entry_end_seq=5,
            metadata={"reason": "deterministic_match"},
        )
    )

    anchors = await store.list_topic_anchors("topic-1")
    recall_links = await store.list_recall_links("topic-1")

    assert initial.anchor_type == "topic_initial"
    assert finalized.anchor_type == "topic_finalized"
    assert [anchor.seq for anchor in anchors] == [3, 9]
    assert recall.recalled_topic_id == "topic-0"
    assert recall_links == [recall]


@pytest.mark.asyncio
async def test_update_and_load_topic_cost_aggregate(store: PGTopicStore) -> None:
    first = await store.update_topic_cost(
        TopicCostRecord(
            topic_id="topic-1",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            run_count=1,
            action_count=2,
            validation_count=1,
            tool_call_count=3,
            metadata={"profile": "local"},
        )
    )
    second = await store.update_topic_cost(
        TopicCostRecord(
            topic_id="topic-1",
            prompt_tokens=7,
            completion_tokens=4,
            total_tokens=11,
            run_count=1,
            action_count=1,
            validation_count=0,
            tool_call_count=2,
            metadata={"profile": "local"},
        )
    )
    loaded = await store.load_topic_cost("topic-1")

    assert first.total_tokens == 15
    assert second.prompt_tokens == 17
    assert second.completion_tokens == 9
    assert second.total_tokens == 26
    assert second.run_count == 2
    assert second.action_count == 3
    assert second.validation_count == 1
    assert second.tool_call_count == 5
    assert loaded == second


@pytest.mark.asyncio
async def test_topic_store_rejects_invalid_status_and_ranges(
    store: PGTopicStore,
) -> None:
    with pytest.raises(ValueError, match="topic status"):
        TopicRecord(
            topic_id="topic-bad",
            tape_id="tape-1",
            session_id="session-1",
            kind="coding",
            status="done",
            title=None,
            summary=None,
            owner=None,
            topic_initial_seq=1,
            topic_finalized_seq=None,
            created_at=_dt(9),
            finalized_at=None,
            metadata={},
        )
    with pytest.raises(ValueError, match="source_entry_end_seq"):
        TopicRecallLinkRecord(
            source_topic_id="topic-1",
            recalled_topic_id="topic-0",
            relation="summary_recall",
            source_entry_start_seq=5,
            source_entry_end_seq=4,
        )
    with pytest.raises(KeyError, match="open topic not found"):
        await store.finalize_topic(
            "missing",
            summary="Missing",
            topic_finalized_seq=1,
            finalized_at=_dt(10),
            metadata={},
        )


def test_topic_store_rejects_unbounded_or_sensitive_durable_fields() -> None:
    with pytest.raises(ValueError, match="title must be at most"):
        TopicRecord(
            topic_id="topic-large-title",
            tape_id="tape-1",
            session_id="session-1",
            kind="coding",
            status="open",
            title="x" * 257,
            summary=None,
            owner=None,
            topic_initial_seq=1,
            topic_finalized_seq=None,
            created_at=_dt(9),
            finalized_at=None,
            metadata={},
        )
    with pytest.raises(ValueError, match="forbidden metadata key"):
        TopicAnchorRecord(
            topic_id="topic-1",
            tape_id="tape-1",
            seq=1,
            anchor_type="topic_initial",
            entry_id="entry-1",
            metadata={"prompt": "raw user request"},
        )
    with pytest.raises(ValueError, match="secret-shaped"):
        TopicRecallLinkRecord(
            source_topic_id="topic-1",
            recalled_topic_id="topic-0",
            relation="summary_recall",
            metadata={"safe_label": "token=abc123"},
        )
