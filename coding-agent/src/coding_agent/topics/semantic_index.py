"""Safe semantic memory index helpers for topic recall."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from agentkit.storage.protocols import MemoryHit, MemoryIndex
from coding_agent.topics.memory import ReviewedMemoryRecord
from coding_agent.topics.provenance import topic_entry_range
from coding_agent.topics.range_index import (
    TopicRangeSearchResult,
    require_recall_safe_text,
)
from coding_agent.topics.store import TopicRecord


class SemanticDocKind(StrEnum):
    TOPIC_SUMMARY = "topic-summary"
    ACCEPTED_REVIEWED_MEMORY = "accepted-memory"


class SemanticSourceKind(StrEnum):
    TOPIC = "topic"
    ACCEPTED_MEMORY = "memory"


_SEMANTIC_ID_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TOPIC_SOURCE_VERSION_RE = re.compile(r"^[0-9]+-(?:[0-9]+|open)$")


@dataclass(frozen=True)
class SemanticDocId:
    kind: SemanticDocKind
    source_id: str
    source_version: str

    def __post_init__(self) -> None:
        _require_safe_id_part("semantic document source id", self.source_id)
        _require_doc_source_version(self.kind, self.source_version)

    @classmethod
    def for_topic(cls, topic: TopicRecord) -> SemanticDocId:
        source_range = topic_entry_range(topic)
        end_seq = (
            str(source_range.end_seq) if source_range.end_seq is not None else "open"
        )
        return cls(
            kind=SemanticDocKind.TOPIC_SUMMARY,
            source_id=topic.topic_id,
            source_version=f"{source_range.start_seq}-{end_seq}",
        )

    @classmethod
    def for_reviewed_memory(cls, record: ReviewedMemoryRecord) -> SemanticDocId:
        if record.status != "accepted":
            raise ValueError(
                "semantic reviewed memory document requires accepted status"
            )
        candidate_id = record.candidate.candidate_id
        if candidate_id is None:
            raise ValueError("reviewed memory candidate is missing candidate_id")
        return cls(
            kind=SemanticDocKind.ACCEPTED_REVIEWED_MEMORY,
            source_id=candidate_id,
            source_version=record.status,
        )

    @classmethod
    def parse(cls, value: str | SemanticDocId) -> SemanticDocId:
        if isinstance(value, cls):
            return value
        require_recall_safe_text("semantic document id", value)
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("semantic document id must be kind:source_id:version")
        kind_value, source_id, source_version = parts
        try:
            kind = SemanticDocKind(kind_value)
        except ValueError as exc:
            raise ValueError(
                f"semantic document id kind is not supported: {kind_value}"
            ) from exc
        return cls(kind=kind, source_id=source_id, source_version=source_version)

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.source_id}:{self.source_version}"


@dataclass(frozen=True)
class SemanticSourceRef:
    kind: SemanticSourceKind
    source_id: str

    def __post_init__(self) -> None:
        _require_safe_id_part("semantic source id", self.source_id)

    @classmethod
    def for_topic(cls, topic: TopicRecord) -> SemanticSourceRef:
        return cls(kind=SemanticSourceKind.TOPIC, source_id=topic.topic_id)

    @classmethod
    def for_reviewed_memory(cls, record: ReviewedMemoryRecord) -> SemanticSourceRef:
        if record.status != "accepted":
            raise ValueError("semantic reviewed memory source requires accepted status")
        candidate_id = record.candidate.candidate_id
        if candidate_id is None:
            raise ValueError("reviewed memory candidate is missing candidate_id")
        return cls(kind=SemanticSourceKind.ACCEPTED_MEMORY, source_id=candidate_id)

    @classmethod
    def parse(cls, value: str | SemanticSourceRef) -> SemanticSourceRef:
        if isinstance(value, cls):
            return value
        require_recall_safe_text("semantic source ref", value)
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("semantic source ref must be kind:source_id")
        kind_value, source_id = parts
        try:
            kind = SemanticSourceKind(kind_value)
        except ValueError as exc:
            raise ValueError(
                f"semantic source ref kind is not supported: {kind_value}"
            ) from exc
        return cls(kind=kind, source_id=source_id)

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.source_id}"


@dataclass(frozen=True)
class SemanticMemoryDocument:
    memory_id: str | SemanticDocId
    text: str
    metadata: dict[str, Any]
    source_refs: tuple[str | SemanticSourceRef, ...] = ()

    def __post_init__(self) -> None:
        document_id = SemanticDocId.parse(self.memory_id)
        source_refs = _canonical_source_refs(
            document_id,
            tuple(
                SemanticSourceRef.parse(source_ref) for source_ref in self.source_refs
            ),
        )
        object.__setattr__(self, "memory_id", str(document_id))
        object.__setattr__(self, "source_refs", source_refs)


class SafeSemanticMemoryIndex:
    """Coding Agent policy wrapper around a generic semantic memory index."""

    def __init__(self, index: MemoryIndex) -> None:
        self._index = index

    def require_safe_document(self, document: SemanticMemoryDocument) -> None:
        document_id = SemanticDocId.parse(document.memory_id)
        require_recall_safe_text("text", document.text)
        metadata = dict(document.metadata)
        metadata["source_refs"] = list(document.source_refs)
        _require_safe_metadata("metadata", metadata)
        _canonical_source_refs(
            document_id,
            tuple(
                SemanticSourceRef.parse(source_ref)
                for source_ref in document.source_refs
            ),
        )

    async def upsert(self, document: SemanticMemoryDocument) -> None:
        self.require_safe_document(document)
        metadata = dict(document.metadata)
        metadata["source_refs"] = list(document.source_refs)
        await self._index.upsert(str(document.memory_id), document.text, metadata)

    async def search(self, query: str, limit: int = 10) -> tuple[MemoryHit, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        require_recall_safe_text("query", query)
        return tuple(
            _require_safe_hit(hit)
            for hit in await self._index.search(query, limit=limit)
        )

    async def delete(self, document_id_or_value: str | SemanticDocId) -> None:
        document_id = SemanticDocId.parse(document_id_or_value)
        await self._index.delete(str(document_id))


def semantic_document_from_topic(topic: TopicRecord) -> SemanticMemoryDocument:
    if topic.status != "finalized":
        raise ValueError("semantic topic document requires finalized topic")
    if topic.summary is None:
        raise ValueError("semantic topic document requires summary")
    source_range = topic_entry_range(topic)
    return SemanticMemoryDocument(
        memory_id=SemanticDocId.for_topic(topic),
        text=_summary_document_text(topic.title, topic.summary),
        metadata={
            "kind": "topic_summary",
            "topic_id": topic.topic_id,
            "tape_id": topic.tape_id,
            "session_id": topic.session_id,
            "topic_kind": topic.kind,
            "topic_status": topic.status,
            "source_start_seq": source_range.start_seq,
            "source_end_seq": source_range.end_seq,
        },
        source_refs=(SemanticSourceRef.for_topic(topic),),
    )


def semantic_document_from_reviewed_memory(
    record: ReviewedMemoryRecord,
) -> SemanticMemoryDocument:
    if record.status != "accepted":
        raise ValueError("semantic reviewed memory document requires accepted status")
    candidate = record.candidate
    candidate_id = candidate.candidate_id
    if candidate_id is None:
        raise ValueError("reviewed memory candidate is missing candidate_id")
    return SemanticMemoryDocument(
        memory_id=SemanticDocId.for_reviewed_memory(record),
        text=_summary_document_text(candidate.title, candidate.summary),
        metadata={
            "kind": "accepted_reviewed_memory",
            "memory_kind": candidate.kind,
            "candidate_id": candidate_id,
            "memory_status": record.status,
            "scope": candidate.scope,
            "tags": list(candidate.tags),
        },
        source_refs=(SemanticSourceRef.for_reviewed_memory(record),),
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
    document_id = SemanticDocId.parse(hit.memory_id)
    source_refs = _canonical_source_refs(
        document_id,
        tuple(SemanticSourceRef.parse(source_ref) for source_ref in hit.source_refs),
    )
    for identity in topic_identities:
        if identity in source_refs:
            return identity
    return str(_primary_source_ref(document_id))


def _require_safe_hit(hit: MemoryHit) -> MemoryHit:
    document_id = SemanticDocId.parse(hit.memory_id)
    require_recall_safe_text("text", hit.text)
    _canonical_source_refs(
        document_id,
        tuple(SemanticSourceRef.parse(source_ref) for source_ref in hit.source_refs),
    )
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


def _hybrid_sort_key(hit: HybridRecallHit) -> tuple[int, int, int, str]:
    deterministic_rank = (
        hit.deterministic_rank if hit.deterministic_rank is not None else 1_000_000
    )
    semantic_rank = hit.semantic_rank if hit.semantic_rank is not None else 1_000_000
    deterministic_bucket = 0 if hit.deterministic_rank is not None else 1
    return (deterministic_bucket, deterministic_rank, semantic_rank, hit.identity)


def _summary_document_text(title: str | None, summary: str) -> str:
    if title is None:
        require_recall_safe_text("summary", summary)
        return summary
    require_recall_safe_text("title", title)
    require_recall_safe_text("summary", summary)
    return f"{title}\n\n{summary}"


def _primary_source_ref(document_id: SemanticDocId) -> SemanticSourceRef:
    if document_id.kind is SemanticDocKind.TOPIC_SUMMARY:
        return SemanticSourceRef(
            kind=SemanticSourceKind.TOPIC,
            source_id=document_id.source_id,
        )
    if document_id.kind is SemanticDocKind.ACCEPTED_REVIEWED_MEMORY:
        return SemanticSourceRef(
            kind=SemanticSourceKind.ACCEPTED_MEMORY,
            source_id=document_id.source_id,
        )
    raise ValueError("semantic document kind is not supported")


def _canonical_source_refs(
    document_id: SemanticDocId,
    source_refs: tuple[SemanticSourceRef, ...],
) -> tuple[str, ...]:
    primary = _primary_source_ref(document_id)
    unique_refs = tuple(sorted({str(source_ref) for source_ref in source_refs}))
    if str(primary) not in unique_refs:
        raise ValueError(
            "semantic source refs must include the document's primary source ref"
        )
    return unique_refs


def _require_safe_id_part(field_name: str, value: str) -> None:
    require_recall_safe_text(field_name, value)
    if _SEMANTIC_ID_PART_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must contain only letters, numbers, dot, underscore, or dash"
        )


def _require_doc_source_version(
    kind: SemanticDocKind,
    source_version: str,
) -> None:
    require_recall_safe_text("semantic document source version", source_version)
    if kind is SemanticDocKind.TOPIC_SUMMARY:
        if _TOPIC_SOURCE_VERSION_RE.fullmatch(source_version) is None:
            raise ValueError(
                "semantic topic summary document version must be start-end"
            )
        return
    if (
        kind is SemanticDocKind.ACCEPTED_REVIEWED_MEMORY
        and source_version == "accepted"
    ):
        return
    raise ValueError("semantic document source version is not supported")
