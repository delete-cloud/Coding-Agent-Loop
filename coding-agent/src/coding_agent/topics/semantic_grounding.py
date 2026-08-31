"""Host-owned semantic-memory grounding snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType

from agentkit.runtime.messages import RuntimeMessageKind
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.topics.context_pack import (
    ContextPack,
    ContextPackRenderer,
    stash_context_pack,
)
from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    memory_candidate_session_id,
)
from coding_agent.topics.provenance import topic_entry_range
from coding_agent.topics.range_index import (
    TopicRangeIndex,
    TopicRangeSearchResult,
    require_recall_safe_text,
)
from coding_agent.topics.range_index_builder import build_topic_range_index_from_store
from coding_agent.topics.recall_context import (
    TopicRecallPlan,
    TopicRecallPlanner,
    TopicRecallPlannerInput,
    recall_context_pack,
)
from coding_agent.topics.recall_floor import validate_recall_floor
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex
from coding_agent.topics.semantic_recall import (
    SemanticRecallPlanner,
    SemanticTopicStore,
)
from coding_agent.topics.store import TopicRecord

SEMANTIC_MEMORY_CONTEXT_PACK_CONTRIBUTOR = "semantic_memory"
_RUNTIME_QUERY_KINDS = frozenset(
    {
        RuntimeMessageKind.USER_STEER,
        RuntimeMessageKind.SUBAGENT_MESSAGE,
    }
)
_CONTEXT_PACK_RENDERER = ContextPackRenderer()


@dataclass(frozen=True, slots=True)
class GroundingMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class SemanticMemoryGroundingInput:
    input_id: str
    query_digest: str
    hit_count: int
    messages: tuple[GroundingMessage, ...]


@dataclass(frozen=True, slots=True)
class _SelectedSource:
    kind: str
    source_id: str
    query: str | None


@dataclass(frozen=True, slots=True)
class _CachedSnapshot:
    plugin_input: SemanticMemoryGroundingInput
    context_pack: ContextPack
    inputs: Mapping[str, object]


class _NoopTopicStore:
    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        del topic_id
        return None


class _SnapshotTopicStore:
    """Read each topic at most once while one grounding snapshot is built."""

    def __init__(self, source: SemanticTopicStore) -> None:
        self._source = source
        self._topics: dict[str, TopicRecord | None] = {}

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        if topic_id not in self._topics:
            self._topics[topic_id] = await self._source.load_topic(topic_id)
        return self._topics[topic_id]


class SemanticMemoryGroundingProvider:
    """Own semantic stores and expose stable, immutable per-plugin inputs."""

    def __init__(
        self,
        *,
        semantic_index: SafeSemanticMemoryIndex,
        memory_review_store: MemoryReviewStore,
        read_enabled: bool,
        topic_store: SemanticTopicStore | None = None,
        topic_index: TopicRangeIndex | None = None,
        limit: int = 5,
        recall_min_score: float | None = None,
        recall_min_overlap: float | None = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._semantic_index = semantic_index
        self._memory_review_store = memory_review_store
        self._read_enabled = read_enabled
        self._topic_store = _validate_topic_store(topic_store)
        self._topic_index = _validate_topic_index(topic_index)
        self._derived_topic_index: TopicRangeIndex | None = None
        self._limit = limit
        self._recall_min_score = validate_recall_floor(
            "recall_min_score", recall_min_score
        )
        self._recall_min_overlap = validate_recall_floor(
            "recall_min_overlap", recall_min_overlap
        )
        self._snapshots: dict[str, _CachedSnapshot] = {}
        self._snapshot_lock = asyncio.Lock()

    async def snapshot(self, ctx: PipelineContext) -> Mapping[str, object]:
        source = _selected_source(ctx)
        session_id = ctx.session_id or "semantic-memory-legacy-context"
        input_id = _semantic_input_id(
            session_id=session_id,
            source_kind=source.kind,
            source_id=source.source_id,
        )
        cached = self._snapshots.get(input_id)
        if cached is None:
            async with self._snapshot_lock:
                cached = self._snapshots.get(input_id)
                if cached is None:
                    cached = await self._build_snapshot(
                        ctx=ctx,
                        session_id=session_id,
                        input_id=input_id,
                        query=source.query,
                    )
                    self._snapshots[input_id] = cached
        stash_context_pack(
            ctx.config,
            contributor=SEMANTIC_MEMORY_CONTEXT_PACK_CONTRIBUTOR,
            pack=cached.context_pack,
        )
        return cached.inputs

    async def _build_snapshot(
        self,
        *,
        ctx: PipelineContext,
        session_id: str,
        input_id: str,
        query: str | None,
    ) -> _CachedSnapshot:
        query_digest = semantic_grounding_query_digest(query or "")
        if query is None or not self._read_enabled or not _is_recall_safe_query(query):
            return _cached_snapshot(
                SemanticMemoryGroundingInput(
                    input_id=input_id,
                    query_digest=query_digest,
                    hit_count=0,
                    messages=(),
                ),
                ContextPack(sections=()),
            )

        topic_index = await self._topic_index_for_context()
        snapshot_topic_store = _SnapshotTopicStore(self._topic_store)
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
            topic_store=snapshot_topic_store,
            recall_min_score=self._recall_min_score,
            recall_min_overlap=self._recall_min_overlap,
        )
        plan = await planner.plan(
            TopicRecallPlannerInput(
                source_topic=_source_topic_from_tape(ctx.tape, session_id=session_id),
                text=query,
                limit=self._limit,
                enabled=True,
            )
        )
        plan = await _materialize_selected_topics(plan, snapshot_topic_store)
        pack = recall_context_pack(plan, enabled=True)
        messages = tuple(
            GroundingMessage(role=message["role"], content=message["content"])
            for message in _CONTEXT_PACK_RENDERER.render_messages(pack)
        )
        return _cached_snapshot(
            SemanticMemoryGroundingInput(
                input_id=input_id,
                query_digest=query_digest,
                hit_count=len(plan.topic_results) + len(plan.accepted_memories),
                messages=messages,
            ),
            pack,
        )

    async def _topic_index_for_context(self) -> TopicRangeIndex:
        if self._topic_index is not None:
            return self._topic_index
        if self._derived_topic_index is None:
            derived = await build_topic_range_index_from_store(self._topic_store)
            self._derived_topic_index = (
                TopicRangeIndex() if derived is None else derived.index
            )
        return self._derived_topic_index


async def _materialize_selected_topics(
    plan: TopicRecallPlan,
    topic_store: _SnapshotTopicStore,
) -> TopicRecallPlan:
    materialized: list[TopicRangeSearchResult] = []
    for result in plan.topic_results:
        topic = await topic_store.load_topic(result.topic_id)
        if topic is None or topic.summary is None:
            continue
        try:
            require_recall_safe_text("topic summary", topic.summary)
            if topic.title is not None:
                require_recall_safe_text("topic title", topic.title)
        except ValueError:
            continue
        materialized.append(
            replace(
                result,
                tape_id=topic.tape_id,
                session_id=topic.session_id,
                title=topic.title,
                summary=topic.summary,
                source_ranges=(topic_entry_range(topic),),
                kind=topic.kind,
                status=topic.status,
                created_at=topic.created_at,
                finalized_at=topic.finalized_at,
            )
        )
    return TopicRecallPlan(
        source_topic=plan.source_topic,
        topic_results=tuple(materialized),
        accepted_memories=plan.accepted_memories,
    )


def _cached_snapshot(
    plugin_input: SemanticMemoryGroundingInput,
    context_pack: ContextPack,
) -> _CachedSnapshot:
    summary = MappingProxyType(
        {
            "query_digest": plugin_input.query_digest,
            "hit_count": plugin_input.hit_count,
        }
    )
    compatibility_view = MappingProxyType({"semantic_memory": summary})
    inputs = MappingProxyType(
        {
            "semantic_memory": plugin_input,
            "kb": compatibility_view,
        }
    )
    return _CachedSnapshot(
        plugin_input=plugin_input,
        context_pack=context_pack,
        inputs=inputs,
    )


def semantic_grounding_query_digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _semantic_input_id(*, session_id: str, source_kind: str, source_id: str) -> str:
    identity = json.dumps(
        [session_id, source_kind, source_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"semantic-grounding:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _selected_source(ctx: PipelineContext) -> _SelectedSource:
    for item in reversed(ctx.runtime_messages):
        message = item.message
        if message.kind not in _RUNTIME_QUERY_KINDS:
            continue
        query = _runtime_payload_text(message.payload)
        if query is not None:
            return _SelectedSource(
                kind="runtime",
                source_id=message.message_id,
                query=query,
            )

    entry = _latest_windowed_user_entry(ctx.tape)
    if entry is not None:
        return _SelectedSource(
            kind="entry",
            source_id=entry.id,
            query=str(entry.payload["content"]),
        )
    return _SelectedSource(kind="none", source_id="none", query=None)


def _latest_windowed_user_entry(tape: Tape) -> Entry | None:
    for entry in reversed(tape.windowed_entries()):
        if entry.kind != "message":
            continue
        role = entry.payload.get("role")
        content = entry.payload.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            return entry
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
