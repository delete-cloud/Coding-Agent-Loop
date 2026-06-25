"""Semantic accepted-memory recall plugin for Coding Agent."""

from __future__ import annotations

from datetime import UTC, datetime
import inspect
from typing import Any, Callable

from agentkit.tape.tape import Tape
from agentkit.runtime.pipeline import PipelineContext
from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    memory_candidate_session_id,
)
from coding_agent.topics.range_index import TopicRangeIndex, require_recall_safe_text
from coding_agent.topics.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    recall_context_messages,
)
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex
from coding_agent.topics.semantic_recall import (
    SemanticRecallPlanner,
    SemanticTopicStore,
)
from coding_agent.topics.store import TopicRecord


class _NoopTopicStore:
    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        del topic_id
        return None


class SemanticMemoryPlugin:
    """Inject accepted reviewed-memory recall through build_context."""

    state_key = "semantic_memory"

    def __init__(
        self,
        *,
        semantic_index: SafeSemanticMemoryIndex,
        memory_review_store: MemoryReviewStore,
        read_enabled: bool,
        topic_store: SemanticTopicStore | None = None,
        topic_index: TopicRangeIndex | None = None,
        limit: int = 5,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._semantic_index = semantic_index
        self._memory_review_store = memory_review_store
        self._read_enabled = read_enabled
        self._limit = limit
        self._topic_store = _validate_topic_store(topic_store)
        self._topic_index = _validate_topic_index(topic_index)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"build_context": self.build_context}

    async def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, object]]:
        if not self._read_enabled:
            return []
        if tape is None:
            return []

        user_message = _latest_user_message(tape)
        if user_message is None:
            return []
        if not _is_recall_safe_query(user_message):
            return []

        session_id = _session_id_from_context_or_tape(kwargs.get("ctx"), tape)
        planner = SemanticRecallPlanner(
            topic_planner=TopicRecallPlanner(
                topic_index=self._topic_index,
                accepted_memories=_accepted_memories_for_session(
                    self._memory_review_store,
                    session_id=session_id,
                ),
            ),
            semantic_index=self._semantic_index,
            memory_review_store=self._memory_review_store,
            topic_store=self._topic_store,
        )
        plan = await planner.plan(
            TopicRecallPlannerInput(
                source_topic=_source_topic_from_tape(tape, session_id=session_id),
                text=user_message,
                limit=self._limit,
                enabled=self._read_enabled,
            )
        )
        return recall_context_messages(plan, enabled=self._read_enabled)


def _latest_user_message(tape: Tape) -> str | None:
    entries = (
        tape.windowed_entries() if hasattr(tape, "windowed_entries") else list(tape)
    )
    for entry in reversed(entries):
        if entry.kind != "message":
            continue
        role = entry.payload.get("role")
        content = entry.payload.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            return content
    return None


def _is_recall_safe_query(value: str) -> bool:
    try:
        require_recall_safe_text("semantic recall query", value)
    except ValueError:
        return False
    return True


def _validate_topic_store(
    topic_store: SemanticTopicStore | None,
) -> SemanticTopicStore:
    if topic_store is None:
        return _NoopTopicStore()
    load_topic = getattr(topic_store, "load_topic", None)
    if not callable(load_topic) or not inspect.iscoroutinefunction(load_topic):
        raise TypeError("topic_store must provide async load_topic(topic_id)")
    return topic_store


def _validate_topic_index(topic_index: TopicRangeIndex | None) -> TopicRangeIndex:
    if topic_index is None:
        return TopicRangeIndex()
    if not isinstance(topic_index, TopicRangeIndex):
        raise TypeError("topic_index must be TopicRangeIndex")
    return topic_index


def _session_id_from_context_or_tape(ctx: object, tape: Tape) -> str:
    if isinstance(ctx, PipelineContext) and ctx.session_id:
        return ctx.session_id
    return tape.tape_id


def _accepted_memories_for_session(
    review_store: MemoryReviewStore,
    *,
    session_id: str,
) -> tuple[ReviewedMemoryRecord, ...]:
    return tuple(
        record
        for record in review_store.accepted_memories()
        if memory_candidate_session_id(record.candidate) == session_id
    )


def _source_topic_from_tape(tape: Tape, *, session_id: str) -> TopicRecord:
    return TopicRecord(
        topic_id="semantic-memory-current-turn",
        tape_id=tape.tape_id,
        session_id=session_id,
        kind="coding",
        status="open",
        title="Current turn",
        summary=None,
        owner=None,
        topic_initial_seq=max(len(tape) - 1, 0),
        topic_finalized_seq=None,
        created_at=datetime.now(UTC),
        finalized_at=None,
    )
