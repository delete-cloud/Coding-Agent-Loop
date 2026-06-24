from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.plugins.semantic_memory import SemanticMemoryPlugin
from coding_agent.topics.memory import (
    MemoryReviewStore,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.semantic_backends import FakeSemanticMemoryBackend
from coding_agent.topics.semantic_index import (
    SafeSemanticMemoryIndex,
    SemanticDocId,
    SemanticMemoryDocument,
    SemanticSourceRef,
)


@pytest.mark.asyncio
async def test_build_context_rehydrates_accepted_memory_without_rendering_hit_text() -> (
    None
):
    review_store = MemoryReviewStore()
    accepted = review_store.add_candidate(
        TopicDerivedMemoryCandidate(
            kind="fact",
            title="Auth retry convention",
            summary="Retry auth refresh once after a 401 before surfacing failure.",
            scope="topic:topic-auth",
            tags=("auth", "retry"),
            confidence=0.8,
            provenance={
                "topic_id": "topic-auth",
                "topic_status": "finalized",
                "topic_kind": "coding",
                "source_entry_ranges": [
                    {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
                ],
            },
            candidate_id="memory-auth-retry",
        )
    )
    candidate_id = accepted.candidate.candidate_id
    assert candidate_id is not None
    accepted = review_store.accept_candidate(
        candidate_id,
        reason="verified",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_reviewed_memory(accepted),
            text="needle-only backend MemoryHit text must never render",
            metadata={"kind": "accepted_reviewed_memory"},
            source_refs=(SemanticSourceRef.for_reviewed_memory(accepted),),
        )
    )
    plugin = SemanticMemoryPlugin(
        semantic_index=index,
        memory_review_store=review_store,
        read_enabled=True,
    )

    result = await plugin.build_context(tape=_tape("needle-only"))

    assert len(result) == 1
    rendered = result[0]["content"]
    assert "Accepted memory references" in rendered
    assert "Retry auth refresh once after a 401 before surfacing failure." in rendered
    assert "needle-only backend MemoryHit text must never render" not in rendered


@pytest.mark.asyncio
async def test_build_context_returns_empty_when_read_disabled() -> None:
    plugin = SemanticMemoryPlugin(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=False,
    )

    assert await plugin.build_context(tape=_tape("auth retry")) == []


@pytest.mark.asyncio
async def test_build_context_returns_empty_without_latest_user_message() -> None:
    plugin = SemanticMemoryPlugin(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
    )
    tape = Tape(tape_id="tape-semantic")
    tape.append(
        Entry(
            kind="message",
            payload={"role": "assistant", "content": "No user prompt here."},
        )
    )

    assert await plugin.build_context(tape=tape) == []


@pytest.mark.asyncio
async def test_build_context_skips_unsafe_raw_prompt_without_failing() -> None:
    plugin = SemanticMemoryPlugin(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
    )

    assert await plugin.build_context(tape=_tape("stderr: traceback from pytest")) == []


def _tape(content: str) -> Tape:
    tape = Tape(tape_id="tape-semantic")
    tape.append(
        Entry(
            kind="message",
            payload={"role": "user", "content": content},
            timestamp=datetime(2026, 6, 24, tzinfo=UTC).timestamp(),
        )
    )
    return tape
