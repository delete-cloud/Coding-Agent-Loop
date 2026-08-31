from __future__ import annotations

import hashlib
import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from agentkit.plugin import PluginCapability
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.messages import (
    RuntimeMessage,
    RuntimeMessageKind,
    SequencedRuntimeMessage,
)
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.plugins.semantic_memory import SemanticMemoryPlugin
from coding_agent.topics.context_pack import CONTEXT_PACK_STASH_KEY
from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.semantic_backends import FakeSemanticMemoryBackend
from coding_agent.topics.semantic_grounding import (
    SemanticMemoryGroundingInput,
    SemanticMemoryGroundingProvider,
    semantic_grounding_query_digest,
)
from coding_agent.topics.semantic_index import (
    SafeSemanticMemoryIndex,
    SemanticDocId,
    SemanticMemoryDocument,
    SemanticSourceRef,
)
from coding_agent.topics.store import TopicRecord


class FakeTopicStore:
    def __init__(self, topics: tuple[TopicRecord, ...]) -> None:
        self.topics = {topic.topic_id: topic for topic in topics}
        self.loaded: list[str] = []
        self.listed: list[dict[str, object]] = []

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        self.loaded.append(topic_id)
        return self.topics.get(topic_id)

    async def list_topics(self, **kwargs: object) -> tuple[TopicRecord, ...]:
        self.listed.append(dict(kwargs))
        return tuple(
            topic
            for topic in self.topics.values()
            if topic.status == kwargs.get("status")
        )


class SemanticOnlyReviewStore:
    def __init__(self, records: tuple[ReviewedMemoryRecord, ...]) -> None:
        self.records = {
            record.candidate.candidate_id: record
            for record in records
            if record.candidate.candidate_id is not None
        }

    def accepted_memories(self) -> tuple[ReviewedMemoryRecord, ...]:
        return ()

    def load_memory(self, candidate_id: str) -> ReviewedMemoryRecord | None:
        return self.records.get(candidate_id)


class _SemanticGroundingHarness:
    def __init__(self, **kwargs: object) -> None:
        self.provider = SemanticMemoryGroundingProvider(**kwargs)
        self.plugin = SemanticMemoryPlugin()
        self.last_input: SemanticMemoryGroundingInput | None = None

    async def build_context(
        self, tape: Tape | None = None, **kwargs: object
    ) -> list[dict[str, object]]:
        if tape is None:
            return []
        raw_ctx = kwargs.get("ctx")
        ctx = (
            raw_ctx
            if isinstance(raw_ctx, PipelineContext)
            else PipelineContext(
                tape=tape,
                session_id="semantic-memory-legacy-context",
            )
        )
        inputs = await self.provider.snapshot(ctx)
        plugin_input = inputs["semantic_memory"]
        if not isinstance(plugin_input, SemanticMemoryGroundingInput):
            raise TypeError("semantic_memory input must be frozen grounding")
        self.last_input = plugin_input
        return await self.plugin.build_context(input=plugin_input)


def _semantic_harness(**kwargs: object) -> _SemanticGroundingHarness:
    return _SemanticGroundingHarness(**kwargs)


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
    plugin = _semantic_harness(
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
async def test_build_context_rehydrates_topic_hits_from_authoritative_store() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="Authoritative topic summary says JWT middleware lives in auth gateway.",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(topic)
    topic_store = FakeTopicStore((topic,))
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_topic(topic),
            text="jwt middleware backend sentinel must never render",
            metadata={"kind": "topic_summary"},
            source_refs=(SemanticSourceRef.for_topic(topic),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=topic_store,
        topic_index=topic_index,
    )

    result = await plugin.build_context(tape=_tape("jwt middleware"))

    assert topic_store.loaded == ["topic-auth"]
    assert len(result) == 1
    rendered = result[0]["content"]
    assert "Cross-topic recall references" in rendered
    assert "Authoritative topic summary says JWT middleware lives in auth gateway." in (
        rendered
    )
    assert "jwt middleware backend sentinel must never render" not in rendered


@pytest.mark.asyncio
async def test_build_context_labels_deterministic_topic_score_as_overlap() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="JWT middleware lives in auth gateway.",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(topic)
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((topic,)),
        topic_index=topic_index,
    )

    result = await plugin.build_context(tape=_tape("jwt unrelated"))

    rendered = "\n".join(str(item["content"]) for item in result)
    assert "(overlap 0.5)" in rendered
    assert "(score 0.5)" not in rendered
    assert "(similarity 0.5)" not in rendered


@pytest.mark.asyncio
async def test_build_context_derives_topic_range_index_from_topic_store() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="Authoritative topic summary says JWT middleware lives in auth gateway.",
    )
    topic_store = FakeTopicStore((topic,))
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=topic_store,
    )

    tape = _tape("jwt middleware")
    result = await plugin.build_context(tape=tape)

    assert topic_store.listed == [{"status": "finalized", "limit": 10001}]
    assert result
    rendered = result[0]["content"]
    assert "Cross-topic recall references" in rendered
    assert "Authoritative topic summary says JWT middleware lives in auth gateway." in (
        rendered
    )

    second_result = await plugin.build_context(tape=tape)

    assert second_result == result
    assert topic_store.listed == [{"status": "finalized", "limit": 10001}]


@pytest.mark.asyncio
async def test_build_context_skips_recall_unsafe_topics_from_derived_index() -> None:
    safe_topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="JWT middleware lives in auth gateway.",
    )
    unsafe_topic = _topic(
        "topic-unsafe",
        title="Unsafe topic",
        summary="stdout: raw command output must not enter recall context",
    )
    topic_store = FakeTopicStore((unsafe_topic, safe_topic))
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=topic_store,
    )

    result = await plugin.build_context(tape=_tape("jwt middleware"))

    assert topic_store.listed == [{"status": "finalized", "limit": 10001}]
    assert result
    rendered = result[0]["content"]
    assert "JWT middleware lives in auth gateway." in rendered
    assert "stdout: raw command output" not in rendered


@pytest.mark.asyncio
async def test_grounding_input_records_query_digest_and_hit_count() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="JWT middleware lives in auth gateway.",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_topic(topic),
            text="jwt middleware",
            metadata={"kind": "topic_summary"},
            source_refs=(SemanticSourceRef.for_topic(topic),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((topic,)),
    )
    tape = _tape("jwt middleware")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")

    await plugin.build_context(tape=tape, ctx=ctx)

    assert plugin.last_input is not None
    assert plugin.last_input.query_digest == semantic_grounding_query_digest(
        "jwt middleware"
    )
    assert plugin.last_input.hit_count == 1
    assert "semantic_memory.grounding_marker" not in ctx.config


@pytest.mark.asyncio
async def test_build_context_stashes_context_pack_in_pipeline_context() -> None:
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="JWT middleware lives in auth gateway.",
    )
    topic_index = TopicRangeIndex()
    topic_index.index_topic(topic)
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((topic,)),
        topic_index=topic_index,
    )
    tape = _tape("jwt unrelated")
    ctx = PipelineContext(tape=tape)

    await plugin.build_context(tape=tape, ctx=ctx)

    stash = ctx.config[CONTEXT_PACK_STASH_KEY]
    pack = stash["semantic_memory"]
    section = pack["sections"][0]
    assert section["title"] == "Cross-topic recall references"
    item = section["items"][0]
    assert item["source_kind"] == "topic_summary"
    assert item["source_id"] == "topic:topic-auth"
    assert item["label"] == "Auth gateway"
    assert item["score"] == 0.5
    assert item["score_scale"] == "overlap"


@pytest.mark.asyncio
async def test_build_context_clears_stale_context_pack_stash() -> None:
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
    )
    tape = Tape(tape_id="tape-semantic")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")
    ctx.config[CONTEXT_PACK_STASH_KEY] = {
        "semantic_memory": {"title": "Context Pack", "sections": [{"title": "stale"}]}
    }

    await plugin.build_context(tape=tape, ctx=ctx)

    assert CONTEXT_PACK_STASH_KEY not in ctx.config


@pytest.mark.asyncio
async def test_recall_min_score_filters_semantic_accepted_memory_hits() -> None:
    accepted = ReviewedMemoryRecord(
        candidate=TopicDerivedMemoryCandidate(
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
            candidate_id="memory-low-score",
        ),
        status="accepted",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_reviewed_memory(accepted),
            text="auth unrelated",
            metadata={"kind": "accepted_reviewed_memory"},
            source_refs=(SemanticSourceRef.for_reviewed_memory(accepted),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=SemanticOnlyReviewStore((accepted,)),
        read_enabled=True,
        recall_min_score=0.75,
    )
    tape = _tape("auth retry")
    ctx = PipelineContext(tape=tape)

    result = await plugin.build_context(tape=tape, ctx=ctx)

    assert result == []
    assert plugin.last_input is not None
    assert plugin.last_input.query_digest == semantic_grounding_query_digest(
        "auth retry"
    )
    assert plugin.last_input.hit_count == 0
    assert "semantic_memory.grounding_marker" not in ctx.config


@pytest.mark.asyncio
async def test_build_context_does_not_stash_grounding_summary_marker() -> None:
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
    )
    tape = Tape(tape_id="tape-semantic")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")

    await plugin.build_context(tape=tape, ctx=ctx)

    assert "semantic_memory.grounding_marker" not in ctx.config


@pytest.mark.asyncio
async def test_build_context_uses_runtime_prompt_when_tape_has_no_user_message() -> (
    None
):
    topic = _topic(
        "topic-runtime",
        title="Runtime prompt recall",
        summary="Authoritative runtime prompt summary says image tag 085c82f.",
    )
    topic_store = FakeTopicStore((topic,))
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_topic(topic),
            text="runtime prompt image tag backend sentinel must never render",
            metadata={"kind": "topic_summary"},
            source_refs=(SemanticSourceRef.for_topic(topic),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=topic_store,
    )
    tape = Tape(tape_id="tape-runtime")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")
    ctx.runtime_messages.append(
        SequencedRuntimeMessage(
            sequence=1,
            message=RuntimeMessage(
                message_id="msg-runtime-prompt",
                kind=RuntimeMessageKind.USER_STEER,
                payload={"text": "Which coding-agent image tag did o6n deploy?"},
            ),
        )
    )

    result = await plugin.build_context(tape=tape, ctx=ctx)

    assert topic_store.loaded == ["topic-runtime"]
    assert len(result) == 1
    rendered = result[0]["content"]
    assert "Cross-topic recall references" in rendered
    assert "Authoritative runtime prompt summary says image tag 085c82f." in rendered
    assert "runtime prompt image tag backend sentinel must never render" not in rendered


@pytest.mark.asyncio
async def test_build_context_runtime_prompt_takes_precedence_over_stale_tape_user() -> (
    None
):
    runtime_topic = _topic(
        "topic-runtime",
        title="Runtime prompt recall",
        summary="Runtime prompt summary says image tag 085c82f.",
    )
    stale_tape_topic = _topic(
        "topic-stale-tape",
        title="Stale tape recall",
        summary="Stale tape summary should not be recalled.",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    for topic, text in (
        (runtime_topic, "image tag runtime prompt"),
        (stale_tape_topic, "stale tape decoy"),
    ):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id=SemanticDocId.for_topic(topic),
                text=text,
                metadata={"kind": "topic_summary"},
                source_refs=(SemanticSourceRef.for_topic(topic),),
            )
        )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((runtime_topic, stale_tape_topic)),
        limit=1,
    )
    tape = _tape("stale tape decoy", tape_id="tape-runtime")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")
    ctx.runtime_messages.append(
        SequencedRuntimeMessage(
            sequence=1,
            message=RuntimeMessage(
                message_id="msg-runtime-prompt",
                kind=RuntimeMessageKind.USER_STEER,
                payload={"text": "Which coding-agent image tag did o6n deploy?"},
            ),
        )
    )

    result = await plugin.build_context(tape=tape, ctx=ctx)

    rendered = "\n".join(str(item["content"]) for item in result)
    assert "Runtime prompt summary says image tag 085c82f." in rendered
    assert "Stale tape summary should not be recalled." not in rendered


@pytest.mark.asyncio
async def test_build_context_runtime_prompt_ignores_later_system_notice() -> None:
    user_topic = _topic(
        "topic-user-steer",
        title="User steer recall",
        summary="User steer summary says image tag 085c82f.",
    )
    notice_topic = _topic(
        "topic-system-notice",
        title="System notice recall",
        summary="System notice summary says checkpoint restored.",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    for topic, text in (
        (user_topic, "image tag user steer"),
        (notice_topic, "checkpoint restored system notice"),
    ):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id=SemanticDocId.for_topic(topic),
                text=text,
                metadata={"kind": "topic_summary"},
                source_refs=(SemanticSourceRef.for_topic(topic),),
            )
        )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((user_topic, notice_topic)),
        limit=1,
    )
    tape = Tape(tape_id="tape-runtime")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")
    ctx.runtime_messages.extend(
        [
            SequencedRuntimeMessage(
                sequence=1,
                message=RuntimeMessage(
                    message_id="msg-user-steer",
                    kind=RuntimeMessageKind.USER_STEER,
                    payload={"text": "Which coding-agent image tag did o6n deploy?"},
                ),
            ),
            SequencedRuntimeMessage(
                sequence=2,
                message=RuntimeMessage(
                    message_id="msg-system-notice",
                    kind=RuntimeMessageKind.SYSTEM_NOTICE,
                    payload={"text": "Checkpoint restored"},
                ),
            ),
        ]
    )

    result = await plugin.build_context(tape=tape, ctx=ctx)

    rendered = "\n".join(str(item["content"]) for item in result)
    assert "User steer summary says image tag 085c82f." in rendered
    assert "System notice summary says checkpoint restored." not in rendered


@pytest.mark.parametrize(
    "ignored_kind",
    [
        RuntimeMessageKind.SYSTEM_NOTICE,
        RuntimeMessageKind.APPROVAL_DECISION,
        RuntimeMessageKind.INTERRUPT,
    ],
)
@pytest.mark.asyncio
async def test_build_context_subagent_prompt_ignores_later_non_query_runtime_messages(
    ignored_kind: RuntimeMessageKind,
) -> None:
    subagent_topic = _topic(
        "topic-subagent",
        title="Subagent recall",
        summary="Subagent message summary says shard placement is node-a.",
    )
    ignored_topic = _topic(
        "topic-ignored-runtime",
        title="Ignored runtime recall",
        summary="Ignored runtime summary should not be recalled.",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    for topic, text in (
        (subagent_topic, "subagent shard placement node-a"),
        (ignored_topic, "override stronger keyword"),
    ):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id=SemanticDocId.for_topic(topic),
                text=text,
                metadata={"kind": "topic_summary"},
                source_refs=(SemanticSourceRef.for_topic(topic),),
            )
        )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=FakeTopicStore((subagent_topic, ignored_topic)),
        limit=1,
    )
    tape = Tape(tape_id="tape-runtime")
    ctx = PipelineContext(tape=tape, session_id="session-runtime")
    ctx.runtime_messages.extend(
        [
            SequencedRuntimeMessage(
                sequence=1,
                message=RuntimeMessage(
                    message_id="msg-subagent",
                    kind=RuntimeMessageKind.SUBAGENT_MESSAGE,
                    payload={"text": "Where is the subagent shard placement?"},
                ),
            ),
            SequencedRuntimeMessage(
                sequence=2,
                message=RuntimeMessage(
                    message_id="msg-ignored-runtime",
                    kind=ignored_kind,
                    payload={"text": "override stronger keyword"},
                ),
            ),
        ]
    )

    result = await plugin.build_context(tape=tape, ctx=ctx)

    rendered = "\n".join(str(item["content"]) for item in result)
    assert "Subagent message summary says shard placement is node-a." in rendered
    assert "Ignored runtime summary should not be recalled." not in rendered


@pytest.mark.asyncio
async def test_build_context_scopes_accepted_memory_to_pipeline_session() -> None:
    review_store = MemoryReviewStore()
    current = review_store.add_candidate(
        _candidate(
            "memory-current",
            session_id="session-current",
            tape_id="tape-current",
            title="Current auth retry",
            summary="Current session retry convention",
        )
    )
    review_store.add_candidate(
        TopicDerivedMemoryCandidate(
            kind="fact",
            title="Legacy auth retry",
            summary="Legacy retry convention",
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
            candidate_id="memory-legacy",
        )
    )
    other = review_store.add_candidate(
        _candidate(
            "memory-other",
            session_id="session-other",
            tape_id="tape-other",
            title="Other auth retry",
            summary="Other session retry convention",
        )
    )
    current = review_store.accept_candidate_for_session(
        "session-current",
        "memory-current",
        reason="verified",
    )
    review_store.accept_candidate("memory-legacy", reason="verified")
    other = review_store.accept_candidate_for_session(
        "session-other",
        "memory-other",
        reason="verified",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    for record in (current, other):
        await index.upsert(
            SemanticMemoryDocument(
                memory_id=SemanticDocId.for_reviewed_memory(record),
                text=f"{record.candidate.title}\n\n{record.candidate.summary}",
                metadata={"kind": "accepted_reviewed_memory"},
                source_refs=(SemanticSourceRef.for_reviewed_memory(record),),
            )
        )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=review_store,
        read_enabled=True,
    )
    tape = _tape("auth retry", tape_id="shared-tape")

    result = await plugin.build_context(
        tape=tape,
        ctx=PipelineContext(tape=tape, session_id="session-current"),
    )

    rendered = "\n".join(str(item["content"]) for item in result)
    assert "Current session retry convention" in rendered
    assert "Legacy retry convention" in rendered
    assert "Other session retry convention" not in rendered


@pytest.mark.asyncio
async def test_build_context_does_not_use_tape_id_as_session_scope() -> None:
    review_store = MemoryReviewStore()
    scoped = review_store.add_candidate(
        _candidate(
            "memory-current",
            session_id="session-current",
            tape_id="tape-current",
            title="Current auth retry",
            summary="Current session retry convention",
        )
    )
    scoped = review_store.accept_candidate_for_session(
        "session-current",
        "memory-current",
        reason="verified",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_reviewed_memory(scoped),
            text="Current auth retry\n\nCurrent session retry convention",
            metadata={"kind": "accepted_reviewed_memory"},
            source_refs=(SemanticSourceRef.for_reviewed_memory(scoped),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=review_store,
        read_enabled=True,
    )

    result = await plugin.build_context(
        tape=_tape("auth retry", tape_id="session-current")
    )

    assert result == []


@pytest.mark.asyncio
async def test_pipeline_injects_session_context_into_semantic_memory_hook() -> None:
    review_store = MemoryReviewStore()
    current = review_store.add_candidate(
        _candidate(
            "memory-current",
            session_id="session-current",
            tape_id="tape-current",
            title="Current auth retry",
            summary="Current session retry convention",
        )
    )
    current = review_store.accept_candidate_for_session(
        "session-current",
        "memory-current",
        reason="verified",
    )
    index = SafeSemanticMemoryIndex(FakeSemanticMemoryBackend())
    await index.upsert(
        SemanticMemoryDocument(
            memory_id=SemanticDocId.for_reviewed_memory(current),
            text="Current auth retry\n\nCurrent session retry convention",
            metadata={"kind": "accepted_reviewed_memory"},
            source_refs=(SemanticSourceRef.for_reviewed_memory(current),),
        )
    )
    plugin = _semantic_harness(
        semantic_index=index,
        memory_review_store=review_store,
        read_enabled=True,
    )
    registry = PluginRegistry()
    registry.register(plugin.plugin)
    pipeline = Pipeline(
        runtime=HookRuntime(registry),
        registry=registry,
        context_input_provider=plugin.provider,
    )
    tape = _tape("auth retry", tape_id="shared-tape")
    ctx = PipelineContext(tape=tape, session_id="session-current")

    await pipeline._stage_build_context(ctx)

    rendered = "\n".join(str(item["content"]) for item in ctx.messages)
    assert "Current session retry convention" in rendered


def test_constructor_rejects_invalid_explicit_topic_dependencies() -> None:
    with pytest.raises(TypeError, match="topic_store must provide async load_topic"):
        _semantic_harness(
            semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
            memory_review_store=MemoryReviewStore(),
            read_enabled=True,
            topic_store=object(),
        )

    with pytest.raises(TypeError, match="topic_index must be TopicRangeIndex"):
        _semantic_harness(
            semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
            memory_review_store=MemoryReviewStore(),
            read_enabled=True,
            topic_index=object(),
        )


@pytest.mark.asyncio
async def test_build_context_returns_empty_when_read_disabled() -> None:
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=False,
    )

    assert await plugin.build_context(tape=_tape("auth retry")) == []


@pytest.mark.asyncio
async def test_build_context_returns_empty_without_latest_user_message() -> None:
    plugin = _semantic_harness(
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
    plugin = _semantic_harness(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
    )

    assert await plugin.build_context(tape=_tape("stderr: traceback from pytest")) == []


def _new_semantic_grounding_provider():
    topic = _topic(
        "topic-auth",
        title="Auth gateway",
        summary="Auth gateway handles JWT middleware.",
    )
    topic_store = FakeTopicStore((topic,))
    topic_index = TopicRangeIndex()
    topic_index.index_topic(topic)
    provider = SemanticMemoryGroundingProvider(
        semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
        memory_review_store=MemoryReviewStore(),
        read_enabled=True,
        topic_store=topic_store,
        topic_index=topic_index,
    )
    return provider, topic_store


def _user_entry(content: str, entry_id: str) -> Entry:
    return Entry(
        id=entry_id,
        kind="message",
        payload={"role": "user", "content": content},
        timestamp=datetime(2026, 8, 31, tzinfo=UTC).timestamp(),
    )


@pytest.mark.asyncio
async def test_semantic_grounding_snapshot_is_reused_for_same_input_identity() -> None:
    provider, topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(tape=tape, session_id="session-1")

    first = (await provider.snapshot(ctx))["semantic_memory"]
    second = (await provider.snapshot(ctx))["semantic_memory"]

    assert second is first
    assert first.query_digest == hashlib.sha256(b"auth gateway").hexdigest()
    assert first.hit_count == 1
    assert tuple(
        (message.role, message.content) for message in first.messages
    ) == tuple((message.role, message.content) for message in second.messages)
    assert topic_store.loaded == ["topic-auth"]


@pytest.mark.asyncio
async def test_semantic_grounding_input_identity_includes_session() -> None:
    provider, topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")

    first = (
        await provider.snapshot(PipelineContext(tape=tape, session_id="session-1"))
    )["semantic_memory"]
    second = (
        await provider.snapshot(PipelineContext(tape=tape, session_id="session-2"))
    )["semantic_memory"]

    assert second.input_id != first.input_id
    assert second.query_digest == first.query_digest
    assert topic_store.loaded == ["topic-auth", "topic-auth"]


@pytest.mark.asyncio
async def test_semantic_grounding_store_change_does_not_change_existing_snapshot() -> (
    None
):
    provider, topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(tape=tape, session_id="session-1")

    first = (await provider.snapshot(ctx))["semantic_memory"]
    topic_store.topics["topic-auth"] = _topic(
        "topic-auth",
        title="Mutated auth gateway",
        summary="This source-store mutation must not enter an existing snapshot.",
    )
    second = (await provider.snapshot(ctx))["semantic_memory"]

    assert second is first
    rendered = "\n".join(message.content for message in second.messages)
    assert "Auth gateway handles JWT middleware." in rendered
    assert "source-store mutation" not in rendered
    assert topic_store.loaded == ["topic-auth"]


@pytest.mark.asyncio
async def test_new_user_entry_identity_creates_new_semantic_grounding_snapshot() -> (
    None
):
    provider, topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(tape=tape, session_id="session-1")
    first = (await provider.snapshot(ctx))["semantic_memory"]
    topic_store.topics["topic-auth"] = _topic(
        "topic-auth",
        title="Current auth gateway",
        summary="Current auth gateway snapshot.",
    )

    tape.append(_user_entry("auth gateway", "user-2"))
    second = (await provider.snapshot(ctx))["semantic_memory"]

    assert second is not first
    assert second.input_id != first.input_id
    assert "Current auth gateway snapshot." in "\n".join(
        message.content for message in second.messages
    )
    assert topic_store.loaded == ["topic-auth", "topic-auth"]


@pytest.mark.asyncio
async def test_new_runtime_prompt_identity_creates_new_semantic_grounding_snapshot() -> (
    None
):
    provider, topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("stale tape query", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(tape=tape, session_id="session-1")
    tape_snapshot = (await provider.snapshot(ctx))["semantic_memory"]

    ctx.runtime_messages = [
        SequencedRuntimeMessage(
            sequence=1,
            message=RuntimeMessage(
                message_id="steer-1",
                kind=RuntimeMessageKind.USER_STEER,
                payload={"text": "runtime auth gateway"},
            ),
        )
    ]
    steer_snapshot = (await provider.snapshot(ctx))["semantic_memory"]
    ctx.runtime_messages.append(
        SequencedRuntimeMessage(
            sequence=2,
            message=RuntimeMessage(
                message_id="subagent-2",
                kind=RuntimeMessageKind.SUBAGENT_MESSAGE,
                payload={"text": "auth gateway from subagent"},
            ),
        )
    )
    subagent_snapshot = (await provider.snapshot(ctx))["semantic_memory"]

    assert (
        len(
            {
                tape_snapshot.input_id,
                steer_snapshot.input_id,
                subagent_snapshot.input_id,
            }
        )
        == 3
    )
    assert (
        steer_snapshot.query_digest
        == hashlib.sha256(b"runtime auth gateway").hexdigest()
    )
    assert (
        subagent_snapshot.query_digest
        == hashlib.sha256(b"auth gateway from subagent").hexdigest()
    )
    assert topic_store.loaded == ["topic-auth", "topic-auth"]


@pytest.mark.asyncio
async def test_window_change_of_selected_user_entry_creates_new_semantic_grounding_snapshot() -> (
    None
):
    class SelectableWindowTape(Tape):
        def __init__(self, entries):
            super().__init__(entries=entries, tape_id="tape-window")
            self.selected = [entries[0]]

        def windowed_entries(self):
            return list(self.selected)

    old_entry = _user_entry("old auth gateway", "user-old")
    new_entry = _user_entry("new auth gateway", "user-new")
    tape = SelectableWindowTape([old_entry, new_entry])
    provider, topic_store = _new_semantic_grounding_provider()
    ctx = PipelineContext(tape=tape, session_id="session-1")
    old_snapshot = (await provider.snapshot(ctx))["semantic_memory"]

    tape.selected = [new_entry]
    new_snapshot = (await provider.snapshot(ctx))["semantic_memory"]

    assert new_snapshot.input_id != old_snapshot.input_id
    assert old_snapshot.query_digest == hashlib.sha256(b"old auth gateway").hexdigest()
    assert new_snapshot.query_digest == hashlib.sha256(b"new auth gateway").hexdigest()
    assert topic_store.loaded == ["topic-auth", "topic-auth"]


@pytest.mark.asyncio
async def test_semantic_memory_plugin_owns_no_store_index_or_snapshot_cache() -> None:
    from coding_agent.topics.semantic_grounding import (
        GroundingMessage,
        SemanticMemoryGroundingInput,
    )

    plugin = SemanticMemoryPlugin()
    grounding_input = SemanticMemoryGroundingInput(
        input_id="session-1:user-1",
        query_digest="digest-1",
        hit_count=1,
        messages=(
            GroundingMessage(
                role="system",
                content="Frozen semantic grounding.",
            ),
        ),
    )

    first = await plugin.build_context(input=grounding_input)
    second = await plugin.build_context(input=grounding_input)

    assert plugin.capabilities == frozenset({PluginCapability.PENDING_FACT})
    assert tuple(inspect.signature(SemanticMemoryPlugin).parameters) == ()
    with pytest.raises(FrozenInstanceError):
        setattr(grounding_input, "hit_count", 2)
    with pytest.raises(FrozenInstanceError):
        setattr(grounding_input.messages[0], "content", "mutated")
    assert set(getattr(plugin, "__dict__", ())).isdisjoint(
        {
            "_semantic_index",
            "_memory_review_store",
            "_topic_store",
            "_topic_index",
            "_derived_topic_index",
            "_provider",
            "_snapshot_cache",
        }
    )
    assert first == [{"role": "system", "content": "Frozen semantic grounding."}]
    assert second == first
    assert second is not first
    assert second[0] is not first[0]


@pytest.mark.asyncio
async def test_host_records_semantic_context_pack_run_metadata() -> None:
    provider, _topic_store = _new_semantic_grounding_provider()
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(tape=tape, session_id="session-1")

    grounding_input = (await provider.snapshot(ctx))["semantic_memory"]

    assert tuple(field.name for field in fields(grounding_input)) == (
        "input_id",
        "query_digest",
        "hit_count",
        "messages",
    )
    pack = ctx.config[CONTEXT_PACK_STASH_KEY]["semantic_memory"]
    item = pack["sections"][0]["items"][0]
    assert item["source_kind"] == "topic_summary"
    assert item["source_id"] == "topic:topic-auth"
    assert item["label"] == "Auth gateway"


@pytest.mark.asyncio
async def test_incremental_and_full_context_rebuild_render_same_semantic_grounding() -> (
    None
):
    provider, topic_store = _new_semantic_grounding_provider()
    plugin = SemanticMemoryPlugin()
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(
        runtime=HookRuntime(registry),
        registry=registry,
        context_input_provider=provider,
    )
    tape = Tape(entries=[_user_entry("auth gateway", "user-1")], tape_id="tape-1")
    ctx = PipelineContext(
        tape=tape,
        session_id="session-1",
        config={"incremental_context": False},
    )

    await pipeline._stage_build_context(ctx)
    full_messages = tuple(
        (message["role"], message["content"]) for message in ctx.messages
    )
    ctx.config["incremental_context"] = True
    await pipeline._stage_build_context(ctx)
    incremental_messages = tuple(
        (message["role"], message["content"]) for message in ctx.messages
    )

    assert incremental_messages == full_messages
    assert topic_store.loaded == ["topic-auth"]


def _tape(content: str, *, tape_id: str = "tape-semantic") -> Tape:
    tape = Tape(tape_id=tape_id)
    tape.append(
        Entry(
            kind="message",
            payload={"role": "user", "content": content},
            timestamp=datetime(2026, 6, 24, tzinfo=UTC).timestamp(),
        )
    )
    return tape


def _candidate(
    candidate_id: str,
    *,
    session_id: str,
    tape_id: str,
    title: str,
    summary: str,
) -> TopicDerivedMemoryCandidate:
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title=title,
        summary=summary,
        scope="topic:topic-auth",
        tags=("auth", "retry"),
        confidence=0.8,
        provenance={
            "topic_id": "topic-auth",
            "session_id": session_id,
            "tape_id": tape_id,
            "topic_status": "finalized",
            "topic_kind": "coding",
            "profile": "local",
            "source_entry_ranges": [
                {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
            ],
        },
        candidate_id=candidate_id,
    )


def _topic(
    topic_id: str,
    *,
    title: str | None,
    summary: str,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-topic",
        session_id="session-topic",
        kind="coding",
        status="finalized",
        title=title,
        summary=summary,
        owner=None,
        topic_initial_seq=2,
        topic_finalized_seq=9,
        created_at=datetime(2026, 6, 24, 9, 0, tzinfo=UTC),
        finalized_at=datetime(2026, 6, 24, 9, 5, tzinfo=UTC),
    )
