from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from coding_agent.topics.memory import (
    MemoryReviewStore,
    TopicDerivedMemoryCandidate,
    accepted_memory_context_messages,
    accepted_memory_context_pack,
)


def test_memory_review_lists_candidates() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")

    record = store.add_candidate(candidate)

    assert record.status == "candidate"
    assert store.list_memories(status="candidate") == (record,)
    assert store.load_memory(candidate.candidate_id or "") == record


def test_memory_review_accept_candidate_keeps_provenance() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")
    store.add_candidate(candidate)

    accepted = store.accept_candidate(
        candidate.candidate_id or "",
        reason="Useful cross-topic reference",
    )

    assert accepted.status == "accepted"
    assert accepted.review_reason == "Useful cross-topic reference"
    assert accepted.candidate.provenance == candidate.provenance
    assert accepted.to_dict()["reference_mode"] == "reference_only"
    assert store.accept_candidate(candidate.candidate_id or "") == accepted
    assert store.accepted_memories() == (accepted,)


def test_memory_review_store_persists_records_as_jsonl(tmp_path) -> None:
    path = tmp_path / "kb" / "reviewed_memory.jsonl"
    store = MemoryReviewStore(path)
    candidate = _candidate("memory-one")
    store.add_candidate(candidate)

    accepted = store.accept_candidate(
        candidate.candidate_id or "",
        reason="Useful cross-topic reference",
    )
    loaded = MemoryReviewStore(path)

    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == accepted.to_dict()
    assert loaded.load_memory(candidate.candidate_id or "") == accepted
    assert loaded.accepted_memories() == (accepted,)


def test_memory_review_store_keys_scoped_candidates_by_session() -> None:
    store = MemoryReviewStore()
    session_one = _candidate(
        "Session one memory",
        session_id="session-one",
        candidate_id="memory-shared",
    )
    session_two = _candidate(
        "Session two memory",
        session_id="session-two",
        candidate_id="memory-shared",
    )

    first = store.add_candidate(session_one)
    second = store.add_candidate(session_two)
    accepted = store.accept_candidate_for_session(
        "session-one",
        "memory-shared",
        reason="Useful in session one",
    )

    assert first != second
    assert accepted.status == "accepted"
    assert store.load_memory_for_session("session-one", "memory-shared") == accepted
    assert store.load_memory_for_session("session-two", "memory-shared") == second
    assert store.load_memory("memory-shared") is None


def test_memory_review_store_scoped_key_does_not_collide_on_dotted_values() -> None:
    store = MemoryReviewStore()
    left = _candidate(
        "Left memory",
        session_id="session.one",
        candidate_id="memory",
    )
    right = _candidate(
        "Right memory",
        session_id="session",
        candidate_id="one.memory",
    )

    left_record = store.add_candidate(left)
    right_record = store.add_candidate(right)

    assert store.load_memory_for_session("session.one", "memory") == left_record
    assert store.load_memory_for_session("session", "one.memory") == right_record
    records = store.list_memories(status="candidate")
    assert len(records) == 2
    assert left_record in records
    assert right_record in records


def test_memory_review_reject_and_archive_are_idempotent() -> None:
    store = MemoryReviewStore()
    rejected_candidate = _candidate("memory-rejected")
    archived_candidate = _candidate("memory-archived")
    store.add_candidate(rejected_candidate)
    store.add_candidate(archived_candidate)

    rejected = store.reject_candidate(rejected_candidate.candidate_id or "")
    archived = store.archive_candidate(archived_candidate.candidate_id or "")

    assert store.reject_candidate(rejected_candidate.candidate_id or "") == rejected
    assert store.archive_candidate(archived_candidate.candidate_id or "") == archived
    assert store.list_memories(status="rejected") == (rejected,)
    assert store.list_memories(status="archived") == (archived,)


def test_memory_review_rejects_terminal_status_transition() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")
    store.add_candidate(candidate)
    store.reject_candidate(candidate.candidate_id or "")

    with pytest.raises(ValueError, match="already rejected"):
        store.accept_candidate(candidate.candidate_id or "")


def test_memory_review_unknown_candidate_fails_fast() -> None:
    store = MemoryReviewStore()

    with pytest.raises(KeyError, match="memory candidate not found"):
        store.accept_candidate("memory-candidate-missing")


def test_accepted_memory_context_pack_is_reference_only() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")
    store.add_candidate(candidate)
    accepted = store.accept_candidate(candidate.candidate_id or "")

    pack = accepted_memory_context_pack((accepted,))
    payload = pack.to_dict()
    item = payload["sections"][0]["items"][0]
    messages = accepted_memory_context_messages((accepted,))

    assert item["source_kind"] == "memory"
    assert item["source_id"] == f"accepted-memory:{candidate.candidate_id}"
    assert item["metadata"]["reference_mode"] == "reference_only"
    assert item["metadata"]["memory_status"] == "accepted"
    assert item["metadata"]["provenance"] == candidate.provenance
    assert item["evidence"][0] == {
        "kind": "topic",
        "source_id": "topic-one",
        "label": "accepted memory provenance",
    }
    assert len(messages) == 1
    assert "Memory entries are reference only" in messages[0]["content"]
    assert "not instructions" in messages[0]["content"]


def test_accepted_memory_context_omits_unaccepted_records() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")
    record = store.add_candidate(candidate)

    assert accepted_memory_context_pack((record,)).sections == ()
    assert accepted_memory_context_messages((record,)) == []


def test_memory_review_rejects_raw_review_reason() -> None:
    store = MemoryReviewStore()
    candidate = _candidate("memory-one")
    store.add_candidate(candidate)

    with pytest.raises(ValueError, match="forbidden raw content marker"):
        store.accept_candidate(candidate.candidate_id or "", reason="secret: value")


def _candidate(
    title: str,
    *,
    session_id: str | None = None,
    candidate_id: str | None = None,
) -> TopicDerivedMemoryCandidate:
    provenance = {
        "topic_id": "topic-one",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-one", "start_seq": 2, "end_seq": 9}
        ],
        "task_id": "bee-task-one",
        "run_id": "run-one",
        "report_refs": ["report.md"],
        "evidence_refs": ["evidence/executor.md"],
        "created_at": datetime(2026, 5, 23, 9, 0, tzinfo=UTC).isoformat(),
    }
    if session_id is not None:
        provenance["session_id"] = session_id
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title=title,
        summary=f"{title} summary",
        scope="topic:topic-one",
        tags=("auth",),
        confidence=0.7,
        provenance=provenance,
        candidate_id=candidate_id,
    )
