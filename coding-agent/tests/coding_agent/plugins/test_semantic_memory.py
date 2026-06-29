from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.messages import (
    RuntimeMessage,
    RuntimeMessageKind,
    SequencedRuntimeMessage,
)
from agentkit.runtime.pipeline import PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.pipeline import Pipeline
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.plugins.semantic_memory import SemanticMemoryPlugin
from coding_agent.topics.memory import (
    MemoryReviewStore,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.range_index import TopicRangeIndex
from coding_agent.topics.semantic_backends import FakeSemanticMemoryBackend
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

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        self.loaded.append(topic_id)
        return self.topics.get(topic_id)


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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
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
    plugin = SemanticMemoryPlugin(
        semantic_index=index,
        memory_review_store=review_store,
        read_enabled=True,
    )
    registry = PluginRegistry()
    registry.register(plugin)
    pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)
    tape = _tape("auth retry", tape_id="shared-tape")
    ctx = PipelineContext(tape=tape, session_id="session-current")

    await pipeline._stage_build_context(ctx)

    rendered = "\n".join(str(item["content"]) for item in ctx.messages)
    assert "Current session retry convention" in rendered


def test_constructor_rejects_invalid_explicit_topic_dependencies() -> None:
    with pytest.raises(TypeError, match="topic_store must provide async load_topic"):
        SemanticMemoryPlugin(
            semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
            memory_review_store=MemoryReviewStore(),
            read_enabled=True,
            topic_store=object(),
        )

    with pytest.raises(TypeError, match="topic_index must be TopicRangeIndex"):
        SemanticMemoryPlugin(
            semantic_index=SafeSemanticMemoryIndex(FakeSemanticMemoryBackend()),
            memory_review_store=MemoryReviewStore(),
            read_enabled=True,
            topic_index=object(),
        )


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
