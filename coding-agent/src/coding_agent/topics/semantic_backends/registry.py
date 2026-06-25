"""Registry for Coding Agent semantic memory backend factories."""

from __future__ import annotations

from collections.abc import Callable

from .base import SemanticIndexSchema, SemanticMemoryBackend
from .fake import FakeSemanticMemoryBackend

_BackendFactory = Callable[[SemanticIndexSchema], SemanticMemoryBackend]


def _create_fake_backend(schema: SemanticIndexSchema) -> SemanticMemoryBackend:
    return FakeSemanticMemoryBackend(schema=schema)


_BACKEND_FACTORIES: dict[str, _BackendFactory] = {
    "fake": _create_fake_backend,
}


def available_semantic_memory_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKEND_FACTORIES))


def create_semantic_memory_backend(
    backend: str,
    *,
    schema: SemanticIndexSchema,
) -> SemanticMemoryBackend:
    try:
        factory = _BACKEND_FACTORIES[backend]
    except KeyError as exc:
        raise ValueError(f"unknown semantic memory backend: {backend}") from exc
    return factory(schema)
