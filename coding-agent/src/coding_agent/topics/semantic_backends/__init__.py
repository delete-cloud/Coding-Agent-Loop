"""Semantic memory backend contracts and local test implementations."""

from __future__ import annotations

from .base import (
    SemanticBackendScope,
    SemanticIndexSchema,
    SemanticMemoryBackend,
    SemanticSchemaMismatch,
)
from .fake import FAKE_SEMANTIC_INDEX_SCHEMA, FakeSemanticMemoryBackend
from .registry import (
    available_semantic_memory_backends,
    create_semantic_memory_backend,
)

__all__ = [
    "FAKE_SEMANTIC_INDEX_SCHEMA",
    "FakeSemanticMemoryBackend",
    "SemanticBackendScope",
    "SemanticIndexSchema",
    "SemanticMemoryBackend",
    "SemanticSchemaMismatch",
    "available_semantic_memory_backends",
    "create_semantic_memory_backend",
]
