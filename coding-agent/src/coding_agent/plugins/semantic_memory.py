"""Semantic accepted-memory recall plugin for Coding Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from agentkit.tape.tape import Tape
from coding_agent.topics.memory import MemoryReviewStore
from coding_agent.topics.range_index import TopicRangeIndex, require_recall_safe_text
from coding_agent.topics.recall_context import (
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    recall_context_messages,
)
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex
from coding_agent.topics.semantic_recall import SemanticRecallPlanner
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
        limit: int = 5,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._semantic_index = semantic_index
        self._memory_review_store = memory_review_store
        self._read_enabled = read_enabled
        self._limit = limit
        self._topic_store = _NoopTopicStore()

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"build_context": self.build_context}

    async def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, object]]:
        del kwargs
        if not self._read_enabled:
            return []
        if tape is None:
            return []

        user_message = _latest_user_message(tape)
        if user_message is None:
            return []
        if not _is_recall_safe_query(user_message):
            return []

        planner = SemanticRecallPlanner(
            topic_planner=TopicRecallPlanner(
                topic_index=TopicRangeIndex(),
                accepted_memories=self._memory_review_store.accepted_memories(),
            ),
            semantic_index=self._semantic_index,
            memory_review_store=self._memory_review_store,
            topic_store=self._topic_store,
        )
        plan = await planner.plan(
            TopicRecallPlannerInput(
                source_topic=_source_topic_from_tape(tape),
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


def _source_topic_from_tape(tape: Tape) -> TopicRecord:
    return TopicRecord(
        topic_id="semantic-memory-current-turn",
        tape_id=tape.tape_id,
        session_id="semantic-memory-build-context",
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
