"""Semantic memory backend contracts and local test implementations."""

from __future__ import annotations

from .base import (
    SemanticBackendFactoryConfig,
    SemanticBackendScope,
    SemanticEmbeddingFn,
    SemanticIndexSchema,
    SemanticMemoryBackend,
    SemanticSchemaMismatch,
)
from .fake import FAKE_SEMANTIC_INDEX_SCHEMA, FakeSemanticMemoryBackend
from .lancedb import LANCEDB_SEMANTIC_INDEX_SCHEMA, LanceDBSemanticMemoryBackend
from .registry import (
    available_semantic_memory_backends,
    create_semantic_memory_backend,
)

__all__ = [
    "FAKE_SEMANTIC_INDEX_SCHEMA",
    "LANCEDB_SEMANTIC_INDEX_SCHEMA",
    "FakeSemanticMemoryBackend",
    "LanceDBSemanticMemoryBackend",
    "SemanticBackendFactoryConfig",
    "SemanticBackendScope",
    "SemanticEmbeddingFn",
    "SemanticIndexSchema",
    "SemanticMemoryBackend",
    "SemanticSchemaMismatch",
    "available_semantic_memory_backends",
    "create_semantic_memory_backend",
]
