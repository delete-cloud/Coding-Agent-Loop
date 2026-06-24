"""Semantic memory backend contracts and local test implementations."""

from __future__ import annotations

from .base import (
    SemanticBackendScope,
    SemanticIndexSchema,
    SemanticMemoryBackend,
    SemanticSchemaMismatch,
)
from .fake import FAKE_SEMANTIC_INDEX_SCHEMA, FakeSemanticMemoryBackend

__all__ = [
    "FAKE_SEMANTIC_INDEX_SCHEMA",
    "FakeSemanticMemoryBackend",
    "SemanticBackendScope",
    "SemanticIndexSchema",
    "SemanticMemoryBackend",
    "SemanticSchemaMismatch",
]
