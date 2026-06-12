from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from agentkit.tape.anchor import Anchor
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.topic_lifecycle import (
    TOPIC_ABORTED,
    TOPIC_FINALIZED,
    TOPIC_INITIAL,
    TopicLifecycle,
    find_topic_anchors,
    topic_range_entries,
)
from coding_agent.topic_memory import MemoryReviewStore
from coding_agent.topic_store import (
    JSONObject,
    TopicAnchorRecord,
    TopicRecord,
)


class FakeTopicStore:
    def __init__(self) -> None:
        self.topics: dict[str, TopicRecord] = {}
        self.anchors: list[TopicAnchorRecord] = []
        self.fail_close = False

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        existing = self.topics.get(record.topic_id)
        if existing is not None:
            return existing
        self.topics[record.topic_id] = record
        return record

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        if self.fail_close:
            raise KeyError(f"open topic not found: {topic_id}")
        topic = self._open_topic(topic_id)
        closed = _replace_topic(
            topic,
            status="finalized",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = closed
        return closed

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        if self.fail_close:
            raise KeyError(f"open topic not found: {topic_id}")
        topic = self._open_topic(topic_id)
        closed = _replace_topic(
            topic,
            status="aborted",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = closed
        return closed

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record

    def _open_topic(self, topic_id: str) -> TopicRecord:
        topic = self.topics.get(topic_id)
        if topic is None or topic.status != "open":
            raise KeyError(f"open topic not found: {topic_id}")
        return topic


class FailingMemoryReviewStore(MemoryReviewStore):
    def add_candidate(self, candidate):
        raise OSError("review store unavailable")


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 5, 21, 9, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._now
        self._now += timedelta(minutes=1)
        return value


def _replace_topic(
    topic: TopicRecord,
    *,
    status: str,
    summary: str | None,
    topic_finalized_seq: int | None,
    finalized_at: datetime,
    metadata: JSONObject,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic.topic_id,
        tape_id=topic.tape_id,
        session_id=topic.session_id,
        kind=topic.kind,
        status=status,
        title=topic.title,
        summary=summary,
        owner=topic.owner,
        topic_initial_seq=topic.topic_initial_seq,
        topic_finalized_seq=topic_finalized_seq,
        created_at=topic.created_at,
        finalized_at=finalized_at,
        metadata=metadata,
    )


def _lifecycle(
    store: FakeTopicStore,
    *,
    memory_review_store: MemoryReviewStore | None = None,
) -> TopicLifecycle:
    ids = iter(("topic-1", "topic-2", "topic-3"))
    return TopicLifecycle(
        store=store,
        now=FakeClock(),
        topic_id_factory=lambda: next(ids),
        memory_review_store=memory_review_store,
    )


@pytest.mark.asyncio
async def test_create_topic_writes_topic_initial_anchor() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "ok"}))

    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
        title="Safe topic",
        owner="local",
        metadata={"profile": "local"},
    )

    anchor = cast(object, tape[-1])
    assert topic.topic_id == "topic-1"
    assert topic.topic_initial_seq == 1
    assert getattr(anchor, "anchor_type") == "topic_start"
    assert getattr(anchor, "payload") == {"label": "Safe topic"}
    assert getattr(anchor, "meta")["product_anchor_type"] == TOPIC_INITIAL
    assert getattr(anchor, "meta")["skip"] is True
    assert store.anchors[0].anchor_type == TOPIC_INITIAL
    assert store.anchors[0].seq == 1
    assert store.anchors[0].metadata["encoded_anchor_type"] == "topic_start"


@pytest.mark.asyncio
async def test_finalize_topic_writes_topic_finalized_anchor() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )
    tape.append(Entry(kind="event", payload={"kind": "work"}))

    finalized = await lifecycle.finalize_topic(
        tape=tape,
        topic=topic,
        summary="Done safely",
        metadata={"status_reason": "done"},
    )

    anchor = cast(object, tape[-1])
    assert finalized.status == "finalized"
    assert finalized.topic_finalized_seq == 2
    assert finalized.summary == "Done safely"
    assert getattr(anchor, "anchor_type") == "topic_end"
    assert getattr(anchor, "payload") == {"label": "Done safely"}
    assert getattr(anchor, "meta")["product_anchor_type"] == TOPIC_FINALIZED
    assert store.anchors[-1].anchor_type == TOPIC_FINALIZED
    assert store.anchors[-1].seq == 2


@pytest.mark.asyncio
async def test_finalize_topic_adds_memory_candidate_to_review_store() -> None:
    store = FakeTopicStore()
    review_store = MemoryReviewStore()
    lifecycle = _lifecycle(store, memory_review_store=review_store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
        title="Auth convention",
    )
    tape.append(Entry(kind="event", payload={"kind": "work"}))

    finalized = await lifecycle.finalize_topic(
        tape=tape,
        topic=topic,
        summary="JWT validation belongs in shared middleware",
    )

    records = review_store.list_memories(status="candidate")
    assert len(records) == 1
    assert records[0].candidate.title == "Auth convention"
    assert records[0].candidate.summary == finalized.summary
    assert records[0].candidate.provenance["topic_id"] == finalized.topic_id


@pytest.mark.asyncio
async def test_abort_topic_writes_topic_aborted_anchor() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )

    aborted = await lifecycle.abort_topic(
        tape=tape,
        topic=topic,
        summary="Stopped safely",
        metadata={"status_reason": "aborted"},
    )

    anchor = cast(object, tape[-1])
    assert aborted.status == "aborted"
    assert aborted.topic_finalized_seq == 1
    assert getattr(anchor, "anchor_type") == "topic_end"
    assert getattr(anchor, "meta")["product_anchor_type"] == TOPIC_ABORTED
    assert store.anchors[-1].anchor_type == TOPIC_ABORTED


@pytest.mark.asyncio
async def test_topic_range_lists_entries_between_anchors() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )
    work = Entry(kind="event", payload={"kind": "work"})
    tape.append(work)
    finalized = await lifecycle.finalize_topic(
        tape=tape,
        topic=topic,
        summary="Done safely",
    )

    with_anchors = topic_range_entries(tape, finalized)
    without_anchors = topic_range_entries(tape, finalized, include_anchors=False)

    assert [entry.kind for entry in with_anchors] == ["anchor", "event", "anchor"]
    assert without_anchors == [work]


@pytest.mark.asyncio
async def test_topic_anchors_searchable_on_fixture_tape() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )
    finalized = await lifecycle.finalize_topic(
        tape=tape,
        topic=topic,
        summary="Done safely",
    )

    all_anchors = find_topic_anchors(tape)
    finalized_anchors = find_topic_anchors(tape, product_anchor_type=TOPIC_FINALIZED)

    assert [view.product_anchor_type for view in all_anchors] == [
        TOPIC_INITIAL,
        TOPIC_FINALIZED,
    ]
    assert finalized_anchors[0].seq == finalized.topic_finalized_seq
    assert finalized_anchors[0].topic_id == "topic-1"


def test_old_tapes_without_topic_anchors_still_work() -> None:
    source = Tape(tape_id="old-tape")
    source.append(
        Entry(
            kind="message",
            payload={"role": "assistant", "content": "old tape"},
        )
    )
    source.handoff(Anchor(anchor_type="handoff", payload={"content": "summary"}))
    loaded = Tape.from_list(source.to_list(), tape_id="old-tape")

    anchors = find_topic_anchors(loaded)

    assert len(loaded) == 2
    assert anchors == []


@pytest.mark.asyncio
async def test_topic_lifecycle_rejects_wrong_tape() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    other_tape = Tape(tape_id="tape-2")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )

    with pytest.raises(ValueError, match="belongs to tape"):
        await lifecycle.finalize_topic(
            tape=other_tape,
            topic=topic,
            summary="Done safely",
        )


@pytest.mark.asyncio
async def test_failed_close_does_not_leave_orphan_topic_anchor() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store)
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
    )
    before = tape.to_list()
    store.fail_close = True

    with pytest.raises(KeyError, match="open topic not found"):
        await lifecycle.finalize_topic(
            tape=tape,
            topic=topic,
            summary="Rejected safely",
        )

    assert tape.to_list() == before
    assert [view.product_anchor_type for view in find_topic_anchors(tape)] == [
        TOPIC_INITIAL
    ]


@pytest.mark.asyncio
async def test_memory_candidate_failure_keeps_finalized_topic_anchor() -> None:
    store = FakeTopicStore()
    lifecycle = _lifecycle(store, memory_review_store=FailingMemoryReviewStore())
    tape = Tape(tape_id="tape-1")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-1",
        kind="coding",
        title="Auth convention",
    )
    tape.append(Entry(kind="event", payload={"kind": "work"}))

    with pytest.raises(OSError, match="review store unavailable"):
        await lifecycle.finalize_topic(
            tape=tape,
            topic=topic,
            summary="JWT validation belongs in shared middleware",
        )

    assert store.topics[topic.topic_id].status == "finalized"
    assert [view.product_anchor_type for view in find_topic_anchors(tape)] == [
        TOPIC_INITIAL,
        TOPIC_FINALIZED,
    ]
    assert store.anchors[-1].metadata["encoded_anchor_type"] == "topic_end"
