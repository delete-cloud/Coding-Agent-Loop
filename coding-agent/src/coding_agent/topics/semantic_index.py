"""Safe semantic memory index helpers for topic recall."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from agentkit.storage.protocols import MemoryHit, MemoryIndex
from coding_agent.topics.range_index import (
    TopicRangeSearchResult,
    require_recall_safe_text,
)


@dataclass(frozen=True)
class SemanticMemoryDocument:
    memory_id: str
    text: str
    metadata: dict[str, Any]
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_safe_identity("memory_id", self.memory_id)
        for index, source_ref in enumerate(self.source_refs):
            require_recall_safe_text(f"source_refs[{index}]", source_ref)
        object.__setattr__(self, "source_refs", tuple(sorted(set(self.source_refs))))


class SafeSemanticMemoryIndex:
    """Coding Agent policy wrapper around a generic semantic memory index."""

    def __init__(self, index: MemoryIndex) -> None:
        self._index = index

    async def upsert(self, document: SemanticMemoryDocument) -> None:
        require_recall_safe_text("text", document.text)
        metadata = dict(document.metadata)
        metadata["source_refs"] = list(document.source_refs)
        _require_safe_metadata("metadata", metadata)
        await self._index.upsert(document.memory_id, document.text, metadata)

    async def search(self, query: str, limit: int = 10) -> tuple[MemoryHit, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        require_recall_safe_text("query", query)
        return tuple(
            _require_safe_hit(hit)
            for hit in await self._index.search(query, limit=limit)
        )


@dataclass(frozen=True)
class HybridRecallHit:
    identity: str
    topic_result: TopicRangeSearchResult | None = None
    semantic_hit: MemoryHit | None = None
    deterministic_rank: int | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None


def merge_hybrid_recall_hits(
    topic_results: Iterable[TopicRangeSearchResult],
    semantic_hits: Iterable[MemoryHit],
    *,
    limit: int | None = None,
) -> tuple[HybridRecallHit, ...]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")

    ordered_topics = _ordered_topic_results(topic_results)
    topic_identities = tuple(f"topic:{result.topic_id}" for result in ordered_topics)
    merged: dict[str, HybridRecallHit] = {}

    for rank, result in enumerate(ordered_topics):
        identity = f"topic:{result.topic_id}"
        merged[identity] = HybridRecallHit(
            identity=identity,
            topic_result=result,
            deterministic_rank=rank,
        )

    safe_semantic_hits = tuple(_require_safe_hit(hit) for hit in semantic_hits)
    ordered_semantic_hits = sorted(
        safe_semantic_hits,
        key=lambda hit: (
            -hit.score,
            _semantic_identity(hit, topic_identities),
            hit.memory_id,
        ),
    )
    for rank, hit in enumerate(ordered_semantic_hits):
        identity = _semantic_identity(hit, topic_identities)
        existing = merged.get(identity)
        if existing is not None:
            if existing.semantic_hit is None:
                merged[identity] = HybridRecallHit(
                    identity=existing.identity,
                    topic_result=existing.topic_result,
                    semantic_hit=hit,
                    deterministic_rank=existing.deterministic_rank,
                    semantic_rank=rank,
                    semantic_score=hit.score,
                )
            continue
        merged[identity] = HybridRecallHit(
            identity=identity,
            semantic_hit=hit,
            semantic_rank=rank,
            semantic_score=hit.score,
        )

    results = tuple(sorted(merged.values(), key=_hybrid_sort_key))
    if limit is not None:
        return results[:limit]
    return results


def _ordered_topic_results(
    topic_results: Iterable[TopicRangeSearchResult],
) -> tuple[TopicRangeSearchResult, ...]:
    return tuple(
        sorted(topic_results, key=lambda result: (-result.score, result.topic_id))
    )


def _semantic_identity(hit: MemoryHit, topic_identities: Sequence[str]) -> str:
    source_refs = tuple(sorted(set(hit.source_refs)))
    for identity in topic_identities:
        if identity in source_refs:
            return identity
    if source_refs:
        return source_refs[0]
    _require_safe_identity("memory_id", hit.memory_id)
    return f"memory:{hit.memory_id}"


def _require_safe_hit(hit: MemoryHit) -> MemoryHit:
    _require_safe_identity("memory_id", hit.memory_id)
    require_recall_safe_text("text", hit.text)
    for index, source_ref in enumerate(hit.source_refs):
        require_recall_safe_text(f"source_refs[{index}]", source_ref)
    _require_safe_metadata("metadata", hit.metadata)
    return hit


def _require_safe_metadata(field_name: str, value: Any) -> None:
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        require_recall_safe_text(field_name, value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} keys must be strings")
            require_recall_safe_text(f"{field_name}.{key}", key)
            _require_safe_metadata(f"{field_name}.{key}", item)
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _require_safe_metadata(f"{field_name}[{index}]", item)
        return
    raise TypeError(
        f"{field_name} has unsupported metadata type: {type(value).__name__}"
    )


def _hybrid_sort_key(hit: HybridRecallHit) -> tuple[float, int, int, str]:
    semantic_score = hit.semantic_score if hit.semantic_score is not None else -1.0
    deterministic_rank = (
        hit.deterministic_rank if hit.deterministic_rank is not None else 1_000_000
    )
    semantic_rank = hit.semantic_rank if hit.semantic_rank is not None else 1_000_000
    return (-semantic_score, deterministic_rank, semantic_rank, hit.identity)


def _require_safe_identity(field_name: str, value: str) -> None:
    require_recall_safe_text(field_name, value)
    if any(char.isspace() for char in value):
        raise ValueError(f"{field_name} must not contain whitespace")
