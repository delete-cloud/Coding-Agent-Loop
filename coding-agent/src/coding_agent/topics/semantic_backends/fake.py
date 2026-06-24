"""In-memory semantic memory backend for contract tests and local config wiring."""

from __future__ import annotations

from dataclasses import dataclass
import re

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.semantic_index import SemanticDocId

from .base import (
    SemanticBackendScope,
    SemanticIndexSchema,
    SemanticSchemaMismatch,
    memory_hit_from_document,
    normalize_source_refs,
)

FAKE_SEMANTIC_INDEX_SCHEMA = SemanticIndexSchema(
    schema_version=1,
    embedding_provider_id="fake",
    embedding_model="fake-semantic-memory-v0",
    embedding_dim=8,
    backend_adapter_id="fake",
    backend_schema_version=1,
    distance_metric="cosine",
    score_normalization="overlap_high_is_better_v1",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True, slots=True)
class _StoredSemanticDocument:
    memory_id: str
    text: str
    metadata: dict[str, object]
    source_refs: tuple[str, ...]


class FakeSemanticMemoryBackend:
    """Deterministic in-memory backend implementing the semantic backend contract."""

    def __init__(self, schema: SemanticIndexSchema | None = None) -> None:
        self._schema = schema
        self._documents: dict[str, _StoredSemanticDocument] = {}

    @property
    def schema(self) -> SemanticIndexSchema | None:
        return self._schema

    async def ensure_schema(
        self,
        schema: SemanticIndexSchema,
        *,
        allow_rebuild: bool = False,
    ) -> None:
        if self._schema is None:
            self._schema = schema
            return
        if self._schema == schema:
            return
        if not allow_rebuild:
            raise SemanticSchemaMismatch(existing=self._schema, requested=schema)
        self._schema = schema
        self._documents.clear()

    async def upsert(
        self,
        memory_id: str,
        text: str,
        metadata: dict[str, object],
    ) -> None:
        document_id = SemanticDocId.parse(memory_id)
        stored_metadata = dict(metadata)
        self._documents[str(document_id)] = _StoredSemanticDocument(
            memory_id=str(document_id),
            text=text,
            metadata=stored_metadata,
            source_refs=normalize_source_refs(str(document_id), stored_metadata),
        )

    async def search(self, query: str, limit: int = 10) -> list[MemoryHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        scored_hits = [
            memory_hit_from_document(
                memory_id=document.memory_id,
                text=document.text,
                score=_overlap_score(query, document.text),
                metadata=document.metadata,
            )
            for document in self._documents.values()
        ]
        return sorted(
            scored_hits,
            key=lambda hit: (-hit.score, hit.memory_id),
        )[:limit]

    async def delete(self, memory_id: str) -> None:
        document_id = SemanticDocId.parse(memory_id)
        self._documents.pop(str(document_id), None)

    async def list_ids(
        self,
        *,
        scope: SemanticBackendScope | None = None,
    ) -> list[str]:
        if scope is None:
            return sorted(self._documents)
        return sorted(
            document.memory_id
            for document in self._documents.values()
            if scope.matches(
                memory_id=document.memory_id,
                metadata=document.metadata,
                source_refs=document.source_refs,
            )
        )

    async def delete_scope(self, scope: SemanticBackendScope) -> int:
        matching_ids = await self.list_ids(scope=scope)
        for memory_id in matching_ids:
            del self._documents[memory_id]
        return len(matching_ids)


def _overlap_score(query: str, text: str) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokens(text))
    return len(query_tokens & text_tokens) / len(query_tokens)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _TOKEN_RE.finditer(value))
