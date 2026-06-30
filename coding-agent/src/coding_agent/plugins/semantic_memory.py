"""Semantic accepted-memory recall plugin for Coding Agent."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from agentkit.runtime.messages import RuntimeMessageKind
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.tape import Tape
from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    memory_candidate_session_id,
)
from coding_agent.topics.range_index import TopicRangeIndex, require_recall_safe_text
from coding_agent.topics.range_index_builder import build_topic_range_index_from_store
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

_RUNTIME_QUERY_KINDS = frozenset(
    {
        RuntimeMessageKind.USER_STEER,
        RuntimeMessageKind.SUBAGENT_MESSAGE,
    }
)
SEMANTIC_MEMORY_GROUNDING_MARKER_KEY = "semantic_memory.grounding_marker"


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
        self._derived_topic_index: TopicRangeIndex | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"build_context": self.build_context}

    async def _topic_index_for_context(self) -> TopicRangeIndex:
        if self._topic_index is not None:
            return self._topic_index
        if self._derived_topic_index is None:
            derived = await build_topic_range_index_from_store(self._topic_store)
            self._derived_topic_index = (
                TopicRangeIndex() if derived is None else derived.index
            )
        return self._derived_topic_index

    async def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, object]]:
        _clear_semantic_memory_grounding_marker(kwargs.get("ctx"))
        if not self._read_enabled:
            return []
        if tape is None:
            return []

        user_message = _latest_runtime_prompt_message(kwargs.get("ctx"))
        if user_message is None:
            user_message = _latest_user_message(tape)
        if user_message is None:
            return []
        if not _is_recall_safe_query(user_message):
            return []

        session_id = _session_id_from_context(kwargs.get("ctx"))
        topic_index = await self._topic_index_for_context()
        planner = SemanticRecallPlanner(
            topic_planner=TopicRecallPlanner(
                topic_index=topic_index,
                accepted_memories=_accepted_memories_for_context(
                    self._memory_review_store,
                    session_id=session_id,
                ),
            ),
            semantic_index=self._semantic_index,
            memory_review_store=self._memory_review_store,
            topic_store=self._topic_store,
        )
        source_session_id = session_id or "semantic-memory-legacy-context"
        plan = await planner.plan(
            TopicRecallPlannerInput(
                source_topic=_source_topic_from_tape(
                    tape, session_id=source_session_id
                ),
                text=user_message,
                limit=self._limit,
                enabled=self._read_enabled,
            )
        )
        _set_semantic_memory_grounding_marker(
            kwargs.get("ctx"),
            tape=tape,
            query=user_message,
            hit_count=len(plan.topic_results) + len(plan.accepted_memories),
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


def _latest_runtime_prompt_message(ctx: object) -> str | None:
    if not isinstance(ctx, PipelineContext):
        return None

    for item in reversed(ctx.runtime_messages):
        message = item.message
        if message.kind not in _RUNTIME_QUERY_KINDS:
            continue
        text = _runtime_payload_text(message.payload)
        if text is not None:
            return text
    return None


def _runtime_payload_text(payload: Mapping[str, object]) -> str | None:
    for key in ("text", "message", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
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


def _validate_topic_index(
    topic_index: TopicRangeIndex | None,
) -> TopicRangeIndex | None:
    if topic_index is None:
        return None
    if not isinstance(topic_index, TopicRangeIndex):
        raise TypeError("topic_index must be TopicRangeIndex")
    return topic_index


def _session_id_from_context(ctx: object) -> str | None:
    if isinstance(ctx, PipelineContext) and ctx.session_id:
        return ctx.session_id
    return None


def _clear_semantic_memory_grounding_marker(ctx: object) -> None:
    if isinstance(ctx, PipelineContext):
        ctx.config.pop(SEMANTIC_MEMORY_GROUNDING_MARKER_KEY, None)


def _set_semantic_memory_grounding_marker(
    ctx: object,
    *,
    tape: Tape,
    query: str,
    hit_count: int,
) -> None:
    if isinstance(ctx, PipelineContext):
        ctx.config[SEMANTIC_MEMORY_GROUNDING_MARKER_KEY] = {
            "query_digest": semantic_grounding_query_digest(query),
            "tape_entry_count": len(tape),
            "hit_count": hit_count,
        }


def semantic_grounding_query_digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _accepted_memories_for_context(
    review_store: MemoryReviewStore,
    *,
    session_id: str | None,
) -> tuple[ReviewedMemoryRecord, ...]:
    records: list[ReviewedMemoryRecord] = []
    for record in review_store.accepted_memories():
        record_session_id = memory_candidate_session_id(record.candidate)
        if record_session_id is None or record_session_id == session_id:
            records.append(record)
    return tuple(records)


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
