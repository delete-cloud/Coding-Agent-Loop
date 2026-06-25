from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.semantic_backends import (
    FAKE_SEMANTIC_INDEX_SCHEMA,
    FakeSemanticMemoryBackend,
    SemanticIndexSchema,
    SemanticSchemaMismatch,
)
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex, SemanticDocId
from coding_agent.topics.semantic_sync import (
    SemanticMemoryReviewSyncService,
    SemanticMemorySyncer,
)
from coding_agent.topics.store import TopicRecord


@pytest.mark.asyncio
async def test_sync_indexes_only_finalized_topic_summaries() -> None:
    backend, syncer = _syncer()
    finalized = _topic("topic-final", title="Auth convention", summary="Use JWT")
    open_topic = _topic(
        "topic-open",
        status="open",
        title="Open topic",
        summary="Should not index",
        finalized_seq=None,
    )
    aborted = _topic(
        "topic-aborted",
        status="aborted",
        title="Aborted topic",
        summary="Should not index",
    )
    no_summary = _topic("topic-nosummary", title="No summary", summary=None)

    indexed = await syncer.sync_topic(finalized)
    skipped_open = await syncer.sync_topic(open_topic)
    skipped_aborted = await syncer.sync_topic(aborted)
    skipped_no_summary = await syncer.sync_topic(no_summary)

    assert indexed.indexed_ids == ("topic-summary:topic-final:2-9",)
    assert indexed.indexed_count == 1
    assert indexed.deleted_ids == ()
    assert skipped_open.skipped_count == 1
    assert skipped_aborted.skipped_count == 1
    assert skipped_no_summary.skipped_count == 1
    hits = await backend.search("JWT auth", limit=5)
    assert [(hit.memory_id, hit.text, hit.metadata) for hit in hits] == [
        (
            "topic-summary:topic-final:2-9",
            "Auth convention\n\nUse JWT",
            {
                "kind": "topic_summary",
                "topic_id": "topic-final",
                "tape_id": "tape-1",
                "session_id": "session-1",
                "topic_kind": "coding",
                "topic_status": "finalized",
                "source_start_seq": 2,
                "source_end_seq": 9,
                "source_refs": ["topic:topic-final"],
                "profile": "local",
            },
        )
    ]


@pytest.mark.asyncio
async def test_sync_indexes_only_accepted_reviewed_memories() -> None:
    backend, syncer = _syncer()
    accepted = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", title="Auth memory", summary="Use JWT"),
        status="accepted",
        review_reason="Useful",
    )
    expected_id = _reviewed_memory_doc_id(accepted)

    report = await syncer.sync_reviewed_memory(accepted)

    assert report.indexed_ids == (expected_id,)
    assert report.indexed_count == 1
    assert await backend.list_ids() == [expected_id]
    hits = await backend.search("JWT", limit=5)
    assert [(hit.memory_id, hit.text, hit.metadata) for hit in hits] == [
        (
            expected_id,
            "Auth memory\n\nUse JWT",
            {
                "kind": "accepted_reviewed_memory",
                "memory_kind": "fact",
                "candidate_id": "memory-auth",
                "memory_status": "accepted",
                "session_id": "session-1",
                "tape_id": "tape-1",
                "scope": "topic:topic-auth",
                "tags": ["auth", "jwt"],
                "source_refs": ["memory:memory-auth"],
                "profile": "local",
            },
        )
    ]


@pytest.mark.asyncio
async def test_sync_indexes_same_candidate_id_per_session_as_distinct_documents() -> (
    None
):
    backend, syncer = _syncer()
    first = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", session_id="session-1", tape_id="tape-1"),
        status="accepted",
    )
    second = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", session_id="session-2", tape_id="tape-2"),
        status="accepted",
    )
    first_id = _reviewed_memory_doc_id(first)
    second_id = _reviewed_memory_doc_id(second)

    first_report = await syncer.sync_reviewed_memory(first)
    second_report = await syncer.sync_reviewed_memory(second)

    assert first_id != second_id
    assert first_report.indexed_ids == (first_id,)
    assert second_report.indexed_ids == (second_id,)
    assert await backend.list_ids() == sorted([first_id, second_id])


@pytest.mark.asyncio
async def test_sync_indexes_dotted_session_and_profile_as_distinct_documents() -> None:
    backend, syncer = _syncer()
    left = ReviewedMemoryRecord(
        candidate=_candidate(
            "memory-auth",
            session_id="session.one",
            profile="local",
        ),
        status="accepted",
    )
    right = ReviewedMemoryRecord(
        candidate=_candidate(
            "memory-auth",
            session_id="session",
            profile="one.local",
        ),
        status="accepted",
    )
    left_id = _reviewed_memory_doc_id(left)
    right_id = _reviewed_memory_doc_id(right)

    left_report = await syncer.sync_reviewed_memory(left)
    right_report = await syncer.sync_reviewed_memory(right)

    assert left_id != right_id
    assert left_report.indexed_ids == (left_id,)
    assert right_report.indexed_ids == (right_id,)
    assert await backend.list_ids() == sorted([left_id, right_id])


@pytest.mark.asyncio
async def test_sync_skips_candidate_rejected_and_archived_memories() -> None:
    backend, syncer = _syncer()

    reports = [
        await syncer.sync_reviewed_memory(
            ReviewedMemoryRecord(
                candidate=_candidate(f"memory-{status}"), status=status
            )
        )
        for status in ("candidate", "rejected", "archived")
    ]

    assert [report.skipped_count for report in reports] == [1, 1, 1]
    assert [report.indexed_ids for report in reports] == [(), (), ()]
    assert await backend.list_ids() == []


@pytest.mark.asyncio
async def test_full_rebuild_is_idempotent() -> None:
    backend, syncer = _syncer()
    stale = _topic("topic-stale", title="Stale", summary="Delete me")
    topic = _topic("topic-auth", title="Auth convention", summary="Use JWT")
    memory = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", title="Accepted auth", summary="JWT rule"),
        status="accepted",
    )
    memory_id = _reviewed_memory_doc_id(memory)
    await syncer.sync_topic(stale)

    first = await syncer.rebuild([topic], [memory])
    second = await syncer.rebuild([topic], [memory])

    expected_ids = (
        memory_id,
        "topic-summary:topic-auth:2-9",
    )
    assert first.indexed_ids == expected_ids
    assert first.deleted_ids == ("topic-summary:topic-stale:2-9",)
    assert first.indexed_count == 2
    assert first.deleted_count == 1
    assert second.indexed_ids == expected_ids
    assert second.deleted_ids == expected_ids
    assert second.indexed_count == 2
    assert second.deleted_count == 2
    assert await backend.list_ids() == list(expected_ids)


@pytest.mark.asyncio
async def test_full_topic_scan_handles_more_than_100_topics() -> None:
    backend, syncer = _syncer()
    topics = [
        _topic(
            f"topic-{index:03d}",
            title=f"Topic {index:03d}",
            summary=f"Summary {index:03d}",
        )
        for index in range(125)
    ]

    report = await syncer.rebuild(topics, [])

    expected_ids = tuple(f"topic-summary:topic-{index:03d}:2-9" for index in range(125))
    assert report.indexed_ids == expected_ids
    assert report.topic_count == 125
    assert report.indexed_count == 125
    assert await backend.list_ids() == list(expected_ids)


@pytest.mark.asyncio
async def test_manual_rebuild_startup_and_event_triggers_share_sync_contract() -> None:
    rebuild_backend, rebuild_syncer = _syncer()
    startup_backend, startup_syncer = _syncer()
    event_backend, event_syncer = _syncer()
    topic = _topic("topic-auth", title="Auth convention", summary="Use JWT")
    memory = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth", title="Accepted auth", summary="JWT rule"),
        status="accepted",
    )
    memory_id = _reviewed_memory_doc_id(memory)

    rebuild_report = await rebuild_syncer.rebuild([topic], [memory])
    startup_report = await startup_syncer.reconcile_startup([topic], [memory])
    topic_report = await event_syncer.sync_topic(topic)
    memory_report = await event_syncer.sync_reviewed_memory(memory)

    expected_ids = (
        memory_id,
        "topic-summary:topic-auth:2-9",
    )
    assert rebuild_report.indexed_ids == expected_ids
    assert startup_report.indexed_ids == expected_ids
    assert topic_report.indexed_ids == ("topic-summary:topic-auth:2-9",)
    assert memory_report.indexed_ids == (memory_id,)
    assert await _snapshot(rebuild_backend) == await _snapshot(startup_backend)
    assert await _snapshot(event_backend) == await _snapshot(rebuild_backend)


@pytest.mark.asyncio
async def test_event_sync_deletes_stale_topic_scope_when_topic_not_finalized_or_summary_missing() -> (
    None
):
    backend, syncer = _syncer()
    finalized = _topic("topic-auth", title="Auth convention", summary="Use JWT")
    await syncer.sync_topic(finalized)

    open_report = await syncer.sync_topic(
        _topic(
            "topic-auth",
            status="open",
            title="Auth convention",
            summary="Use JWT",
            finalized_seq=None,
        )
    )
    await syncer.sync_topic(finalized)
    no_summary_report = await syncer.sync_topic(
        _topic("topic-auth", title="Auth convention", summary=None)
    )

    assert open_report.deleted_ids == ("topic-summary:topic-auth:2-9",)
    assert open_report.deleted_count == 1
    assert no_summary_report.deleted_ids == ("topic-summary:topic-auth:2-9",)
    assert no_summary_report.deleted_count == 1
    assert await backend.list_ids() == []


@pytest.mark.asyncio
async def test_event_sync_deletes_unaccepted_reviewed_memory_scope() -> None:
    backend, syncer = _syncer()
    accepted = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth"), status="accepted"
    )
    accepted_id = _reviewed_memory_doc_id(accepted)
    await syncer.sync_reviewed_memory(accepted)

    report = await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=_candidate("memory-auth"), status="rejected")
    )

    assert report.deleted_ids == (accepted_id,)
    assert report.deleted_count == 1
    assert report.skipped_count == 1
    assert await backend.list_ids() == []


@pytest.mark.asyncio
async def test_reviewed_memory_delete_scope_does_not_cross_session() -> None:
    backend, syncer = _syncer()
    await backend.ensure_schema(FAKE_SEMANTIC_INDEX_SCHEMA)
    other_record = ReviewedMemoryRecord(
        candidate=_candidate(
            "memory-auth",
            session_id="other-session",
            tape_id="other-tape",
        ),
        status="accepted",
    )
    other_id = _reviewed_memory_doc_id(other_record)
    await backend.upsert(
        other_id,
        "Other session auth memory",
        {
            "source_refs": ("memory:memory-auth",),
            "session_id": "other-session",
            "tape_id": "other-tape",
            "profile": "local",
        },
    )

    report = await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=_candidate("memory-auth"), status="rejected")
    )

    assert report.deleted_ids == ()
    assert report.deleted_count == 0
    assert report.skipped_count == 1
    assert await backend.list_ids() == [other_id]


@pytest.mark.asyncio
async def test_reviewed_memory_without_session_scope_has_no_index_side_effects() -> (
    None
):
    backend, syncer = _syncer()
    await backend.ensure_schema(FAKE_SEMANTIC_INDEX_SCHEMA)
    scoped_record = ReviewedMemoryRecord(
        candidate=_candidate("memory-auth"),
        status="accepted",
    )
    scoped_id = _reviewed_memory_doc_id(scoped_record)
    await backend.upsert(
        scoped_id,
        "Existing scoped memory",
        {
            "source_refs": ("memory:memory-auth",),
            "session_id": "session-1",
            "tape_id": "tape-1",
            "profile": "local",
        },
    )
    legacy_candidate = TopicDerivedMemoryCandidate(
        kind="fact",
        title="Legacy memory",
        summary="Legacy memory summary",
        scope="topic:topic-auth",
        tags=("auth",),
        confidence=0.8,
        provenance={
            "topic_id": "topic-auth",
            "topic_status": "finalized",
            "topic_kind": "coding",
            "source_entry_ranges": [
                {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
            ],
        },
        candidate_id="memory-auth",
    )

    accepted = await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=legacy_candidate, status="accepted")
    )
    rejected = await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=legacy_candidate, status="rejected")
    )

    assert accepted.indexed_ids == ()
    assert accepted.skipped_count == 1
    assert rejected.deleted_ids == ()
    assert rejected.skipped_count == 1
    assert await backend.list_ids() == [scoped_id]


@pytest.mark.asyncio
async def test_review_sync_service_accept_indexes_accepted_memory() -> None:
    backend, syncer = _syncer()
    store = MemoryReviewStore()
    candidate = _candidate("memory-auth", title="Auth memory", summary="Use JWT")
    store.add_candidate(candidate)
    service = SemanticMemoryReviewSyncService(review_store=store, syncer=syncer)

    record = await service.accept_candidate("memory-auth", reason="Useful")
    expected_id = _reviewed_memory_doc_id(record)

    assert record.status == "accepted"
    assert record.review_reason == "Useful"
    assert store.load_memory("memory-auth") == record
    assert await backend.list_ids() == [expected_id]
    hits = await backend.search("JWT", limit=5)
    assert [
        (hit.memory_id, hit.text, hit.metadata["memory_status"]) for hit in hits
    ] == [
        (
            expected_id,
            "Auth memory\n\nUse JWT",
            "accepted",
        )
    ]


@pytest.mark.asyncio
async def test_review_sync_service_reject_deletes_stale_accepted_memory() -> None:
    backend, syncer = _syncer()
    candidate = _candidate("memory-auth")
    await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=candidate, status="accepted")
    )
    store = MemoryReviewStore()
    store.add_candidate(candidate)
    service = SemanticMemoryReviewSyncService(review_store=store, syncer=syncer)

    record = await service.reject_candidate("memory-auth", reason="Too narrow")

    assert record.status == "rejected"
    assert record.review_reason == "Too narrow"
    assert store.load_memory("memory-auth") == record
    assert await backend.list_ids() == []
    assert await backend.search("JWT", limit=5) == []


@pytest.mark.asyncio
async def test_review_sync_service_archive_deletes_stale_accepted_memory() -> None:
    backend, syncer = _syncer()
    candidate = _candidate("memory-auth")
    await syncer.sync_reviewed_memory(
        ReviewedMemoryRecord(candidate=candidate, status="accepted")
    )
    store = MemoryReviewStore()
    store.add_candidate(candidate)
    service = SemanticMemoryReviewSyncService(review_store=store, syncer=syncer)

    record = await service.archive_candidate("memory-auth", reason="Superseded")

    assert record.status == "archived"
    assert record.review_reason == "Superseded"
    assert store.load_memory("memory-auth") == record
    assert await backend.list_ids() == []
    assert await backend.search("JWT", limit=5) == []


@pytest.mark.asyncio
async def test_review_sync_service_sync_failure_leaves_store_accepted() -> None:
    store = MemoryReviewStore()
    store.add_candidate(_candidate("memory-auth"))
    syncer = _FailingReviewedMemorySyncer(RuntimeError("semantic sync unavailable"))
    service = SemanticMemoryReviewSyncService(review_store=store, syncer=syncer)

    with pytest.raises(RuntimeError, match="semantic sync unavailable"):
        await service.accept_candidate("memory-auth", reason="Useful")

    stored = store.load_memory("memory-auth")
    assert stored is not None
    assert stored.status == "accepted"
    assert stored.review_reason == "Useful"
    assert syncer.calls == (("memory-auth", "accepted"),)


@pytest.mark.asyncio
async def test_review_sync_service_store_failure_does_not_call_syncer() -> None:
    store = MemoryReviewStore()
    syncer = _RecordingReviewedMemorySyncer()
    service = SemanticMemoryReviewSyncService(review_store=store, syncer=syncer)

    with pytest.raises(KeyError, match="memory candidate not found"):
        await service.accept_candidate("memory-missing")

    assert store.load_memory("memory-missing") is None
    assert syncer.calls == ()


@pytest.mark.asyncio
async def test_sync_rejects_raw_topic_summary_before_backend_upsert() -> None:
    backend, syncer = _syncer()

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await syncer.sync_topic(
            _topic("topic-unsafe", title="Unsafe", summary="stdout: raw output")
        )

    assert await backend.list_ids() == []


@pytest.mark.asyncio
async def test_rebuild_rejects_raw_topic_summary_before_clearing_index() -> None:
    backend, syncer = _syncer()
    existing = _topic("topic-existing", title="Existing", summary="Keep me")
    unsafe = _topic("topic-unsafe", title="Unsafe", summary="stdout: raw output")
    await syncer.sync_topic(existing)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await syncer.rebuild([existing, unsafe], [])

    assert await backend.list_ids() == ["topic-summary:topic-existing:2-9"]


@pytest.mark.asyncio
async def test_schema_mismatch_allow_rebuild_clears_stale_docs() -> None:
    backend = FakeSemanticMemoryBackend(FAKE_SEMANTIC_INDEX_SCHEMA)
    syncer = SemanticMemorySyncer(
        index=SafeSemanticMemoryIndex(backend),
        backend=backend,
        schema=_schema(embedding_dim=16),
    )
    await backend.upsert(
        "topic-summary:topic-stale:1-3",
        "stale document",
        {"source_refs": ("topic:topic-stale",)},
    )

    with pytest.raises(SemanticSchemaMismatch):
        await syncer.ensure_schema(allow_rebuild=False)

    report = await syncer.rebuild(
        [_topic("topic-auth", title="Auth convention", summary="Use JWT")],
        [],
        allow_rebuild=True,
    )

    assert report.deleted_ids == ()
    assert report.indexed_ids == ("topic-summary:topic-auth:2-9",)
    assert await backend.list_ids() == ["topic-summary:topic-auth:2-9"]


@pytest.mark.asyncio
async def test_rebuild_validates_before_schema_rebuild_clears_docs() -> None:
    backend = FakeSemanticMemoryBackend(FAKE_SEMANTIC_INDEX_SCHEMA)
    syncer = SemanticMemorySyncer(
        index=SafeSemanticMemoryIndex(backend),
        backend=backend,
        schema=_schema(embedding_dim=16),
    )
    await backend.upsert(
        "topic-summary:topic-stale:1-3",
        "stale document",
        {"source_refs": ("topic:topic-stale",)},
    )

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        await syncer.rebuild(
            [_topic("topic-unsafe", title="Unsafe", summary="stdout: raw output")],
            [],
            allow_rebuild=True,
        )

    assert backend.schema == FAKE_SEMANTIC_INDEX_SCHEMA
    assert await backend.list_ids() == ["topic-summary:topic-stale:1-3"]


def _syncer() -> tuple[FakeSemanticMemoryBackend, SemanticMemorySyncer]:
    backend = FakeSemanticMemoryBackend()
    return (
        backend,
        SemanticMemorySyncer(
            index=SafeSemanticMemoryIndex(backend),
            backend=backend,
            schema=FAKE_SEMANTIC_INDEX_SCHEMA,
        ),
    )


def _schema(**overrides: object) -> SemanticIndexSchema:
    values = {
        "schema_version": 1,
        "embedding_provider_id": "fake",
        "embedding_model": "fake-memory-v0",
        "embedding_dim": 8,
        "backend_adapter_id": "fake",
        "backend_schema_version": 1,
        "distance_metric": "cosine",
        "score_normalization": "overlap_high_is_better_v1",
    }
    values.update(overrides)
    return SemanticIndexSchema(**values)


def _topic(
    topic_id: str,
    *,
    title: str,
    summary: str | None,
    status: str = "finalized",
    finalized_seq: int | None = 9,
) -> TopicRecord:
    finalized_at = (
        datetime(2026, 6, 24, 9, 5, tzinfo=UTC) if status == "finalized" else None
    )
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-1",
        session_id="session-1",
        kind="coding",
        status=status,
        title=title,
        summary=summary,
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=finalized_seq,
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        finalized_at=finalized_at,
        metadata={"profile": "local"},
    )


def _candidate(
    candidate_id: str,
    *,
    title: str = "Auth convention",
    summary: str = "JWT middleware convention",
    session_id: str = "session-1",
    tape_id: str = "tape-1",
    profile: str = "local",
) -> TopicDerivedMemoryCandidate:
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title=title,
        summary=summary,
        scope="topic:topic-auth",
        tags=("jwt", "auth"),
        confidence=0.8,
        provenance={
            "topic_id": "topic-auth",
            "session_id": session_id,
            "tape_id": tape_id,
            "topic_status": "finalized",
            "topic_kind": "coding",
            "profile": profile,
            "source_entry_ranges": [
                {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
            ],
        },
        candidate_id=candidate_id,
    )


def _reviewed_memory_doc_id(record: ReviewedMemoryRecord) -> str:
    return str(SemanticDocId.for_reviewed_memory(record))


async def _snapshot(
    backend: FakeSemanticMemoryBackend,
) -> tuple[tuple[str, str, dict[str, object], tuple[str, ...]], ...]:
    hits = await backend.search("JWT auth", limit=10)
    return tuple(
        sorted(
            (
                hit.memory_id,
                hit.text,
                hit.metadata,
                hit.source_refs,
            )
            for hit in hits
        )
    )


class _RecordingReviewedMemorySyncer:
    def __init__(self) -> None:
        self.calls: tuple[tuple[str, str], ...] = ()

    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> object:
        self.calls = (
            *self.calls,
            (record.candidate.candidate_id or "", record.status),
        )
        return object()


class _FailingReviewedMemorySyncer(_RecordingReviewedMemorySyncer):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> object:
        await super().sync_reviewed_memory(record)
        raise self._error
