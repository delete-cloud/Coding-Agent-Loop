from __future__ import annotations

import types
from datetime import UTC, datetime

import pytest

from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.topics.memory import ReviewedMemoryRecord, TopicDerivedMemoryCandidate
from coding_agent.topics.semantic_backends import (
    FAKE_SEMANTIC_INDEX_SCHEMA,
    FakeSemanticMemoryBackend,
)
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex
from coding_agent.topics.semantic_maintenance import SemanticMemoryMaintainer
from coding_agent.topics.semantic_sync import SemanticMemorySyncer
from coding_agent.topics.store import TopicRecord


class FakePagedTopicStore:
    def __init__(self, topics: tuple[TopicRecord, ...]) -> None:
        self._topics = topics
        self.calls: list[tuple[int, str | None]] = []

    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: str | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TopicRecord]:
        del session_id, tape_id
        self.calls.append((limit, after_topic_id))
        topics = [
            topic for topic in self._topics if status is None or topic.status == status
        ]
        topics.sort(key=lambda topic: (topic.created_at, topic.topic_id))
        if after_created_at is not None and after_topic_id is not None:
            topics = [
                topic
                for topic in topics
                if (topic.created_at, topic.topic_id)
                > (after_created_at, after_topic_id)
            ]
        return topics[offset : offset + limit]


class DuplicatingPagedTopicStore(FakePagedTopicStore):
    def __init__(self, topics: tuple[TopicRecord, ...]) -> None:
        super().__init__(topics)
        self._first_topic = topics[0]

    async def list_topics(
        self,
        *,
        session_id: str | None = None,
        tape_id: str | None = None,
        status: str | None = None,
        after_created_at: datetime | None = None,
        after_topic_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TopicRecord]:
        page = await super().list_topics(
            session_id=session_id,
            tape_id=tape_id,
            status=status,
            after_created_at=after_created_at,
            after_topic_id=after_topic_id,
            limit=limit,
            offset=offset,
        )
        if len(self.calls) > 1:
            return [self._first_topic, *page]
        return page


class FakeReviewStore:
    def __init__(self, records: tuple[ReviewedMemoryRecord, ...]) -> None:
        self._records = records

    def list_memories(
        self,
        *,
        status: str | None = None,
    ) -> tuple[ReviewedMemoryRecord, ...]:
        if status is None:
            return self._records
        return tuple(record for record in self._records if record.status == status)


@pytest.mark.asyncio
async def test_semantic_maintenance_rebuild_scans_topic_store_in_pages() -> None:
    backend, syncer = _syncer()
    topics = tuple(
        _topic(
            f"topic-{index:03d}",
            title=f"Topic {index:03d}",
            summary=f"Summary {index:03d}",
        )
        for index in range(125)
    )
    topic_store = FakePagedTopicStore(topics)
    review_store = FakeReviewStore(
        (
            ReviewedMemoryRecord(
                candidate=_candidate("memory-auth", summary="Accepted memory"),
                status="accepted",
            ),
        )
    )
    maintainer = SemanticMemoryMaintainer(
        syncer=syncer,
        backend=backend,
        review_store=review_store,
        topic_store=topic_store,
    )

    report = await maintainer.rebuild(batch_size=50)

    assert topic_store.calls == [(50, None), (50, "topic-049"), (50, "topic-099")]
    assert report.topic_count == 125
    assert report.reviewed_memory_count == 1
    assert report.indexed_count == 126
    assert len(await backend.list_ids()) == 126


@pytest.mark.asyncio
async def test_semantic_maintenance_rebuild_requires_topic_store_before_clearing() -> (
    None
):
    backend, syncer = _syncer()
    record = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", summary="Accepted memory"),
        status="accepted",
    )
    await syncer.sync_topic(
        _topic("topic-existing", title="Existing", summary="Keep existing topic")
    )
    review_store = FakeReviewStore((record,))
    maintainer = SemanticMemoryMaintainer(
        syncer=syncer,
        backend=backend,
        review_store=review_store,
    )

    with pytest.raises(RuntimeError, match="topic_store is required"):
        await maintainer.rebuild(batch_size=50)

    assert await backend.list_ids() == ["topic-summary:topic-existing:2-9"]


@pytest.mark.asyncio
async def test_semantic_maintenance_rebuild_rejects_duplicate_topic_scan() -> None:
    backend, syncer = _syncer()
    await syncer.sync_topic(
        _topic("topic-existing", title="Existing", summary="Keep existing topic")
    )
    topic_store = DuplicatingPagedTopicStore(
        (
            _topic("topic-001", title="First", summary="First summary"),
            _topic("topic-002", title="Second", summary="Second summary"),
            _topic("topic-003", title="Third", summary="Third summary"),
        )
    )
    maintainer = SemanticMemoryMaintainer(
        syncer=syncer,
        backend=backend,
        review_store=FakeReviewStore(()),
        topic_store=topic_store,
    )

    with pytest.raises(RuntimeError, match="topic scan changed during rebuild"):
        await maintainer.rebuild(batch_size=2)

    assert topic_store.calls == [(2, None), (2, "topic-002")]
    assert await backend.list_ids() == ["topic-summary:topic-existing:2-9"]


@pytest.mark.asyncio
async def test_semantic_maintenance_status_counts_documents_and_review_states() -> None:
    backend, syncer = _syncer()
    accepted = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", summary="Accepted memory"),
        status="accepted",
    )
    review_store = FakeReviewStore(
        (
            accepted,
            ReviewedMemoryRecord(
                candidate=_candidate("memory-rejected", summary="Rejected memory"),
                status="rejected",
            ),
        )
    )
    maintainer = SemanticMemoryMaintainer(
        syncer=syncer,
        backend=backend,
        review_store=review_store,
    )
    await syncer.sync_reviewed_memory(accepted)

    status = await maintainer.status()

    assert status.document_count == 1
    assert status.reviewed_memory_count == 2
    assert status.accepted_reviewed_memory_count == 1
    assert status.topic_store_available is False


@pytest.mark.asyncio
async def test_semantic_maintenance_rejects_invalid_batch_size() -> None:
    backend, syncer = _syncer()
    maintainer = SemanticMemoryMaintainer(
        syncer=syncer,
        backend=backend,
        review_store=FakeReviewStore(()),
    )

    with pytest.raises(ValueError, match="batch_size must be positive"):
        await maintainer.rebuild(batch_size=0)


@pytest.mark.asyncio
async def test_semantic_maintenance_factory_unavailable_when_semantic_disabled() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())

    async def ensure_runtime(session_id: str) -> object:
        assert session_id == "session-1"
        return types.SimpleNamespace(config={})

    manager.ensure_session_runtime = ensure_runtime  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="semantic memory is disabled"):
        await manager.semantic_memory_maintainer("session-1")


@pytest.mark.asyncio
async def test_semantic_maintenance_factory_reports_topic_store_available_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, syncer = _syncer()
    review_store = FakeReviewStore(())
    topic_store = FakePagedTopicStore(())
    manager = SessionManager(store=InMemorySessionStore())

    async def ensure_runtime(session_id: str) -> object:
        assert session_id == "session-1"
        return types.SimpleNamespace(
            config={
                "semantic_memory_backend": backend,
                "semantic_memory_syncer": syncer,
                "memory_review_store": review_store,
            }
        )

    manager.ensure_session_runtime = ensure_runtime  # type: ignore[method-assign]
    monkeypatch.setattr(manager, "selected_topic_store", lambda: topic_store)

    maintainer = await manager.semantic_memory_maintainer("session-1")
    status = await maintainer.status()

    assert status.topic_store_available is True


def _syncer() -> tuple[FakeSemanticMemoryBackend, SemanticMemorySyncer]:
    backend = FakeSemanticMemoryBackend()
    return backend, SemanticMemorySyncer(
        index=SafeSemanticMemoryIndex(backend),
        backend=backend,
        schema=FAKE_SEMANTIC_INDEX_SCHEMA,
    )


def _topic(
    topic_id: str,
    *,
    title: str,
    summary: str,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status="finalized",
        title=title,
        summary=summary,
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=9,
        created_at=datetime(2026, 6, 24, tzinfo=UTC),
        finalized_at=datetime(2026, 6, 24, 1, tzinfo=UTC),
        metadata={"profile": "local"},
    )


def _candidate(candidate_id: str, *, summary: str) -> TopicDerivedMemoryCandidate:
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title="Memory",
        summary=summary,
        scope="topic:topic-auth",
        tags=("memory",),
        confidence=0.8,
        candidate_id=candidate_id,
        provenance={
            "topic_id": "topic-auth",
            "session_id": "session-1",
            "tape_id": "tape-1",
            "topic_status": "finalized",
            "topic_kind": "coding",
            "profile": "local",
            "source_entry_ranges": [
                {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
            ],
        },
    )
