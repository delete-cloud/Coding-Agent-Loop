"""Registry for Coding Agent semantic memory backend factories."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .base import (
    SemanticBackendFactoryConfig,
    SemanticEmbeddingFn,
    SemanticIndexSchema,
    SemanticMemoryBackend,
)
from .fake import FakeSemanticMemoryBackend
from .lancedb import LanceDBSemanticMemoryBackend

_BackendFactory = Callable[[SemanticBackendFactoryConfig], SemanticMemoryBackend]


def _create_fake_backend(config: SemanticBackendFactoryConfig) -> SemanticMemoryBackend:
    return FakeSemanticMemoryBackend(schema=config.schema)


def _create_lancedb_backend(
    config: SemanticBackendFactoryConfig,
) -> SemanticMemoryBackend:
    return LanceDBSemanticMemoryBackend(
        db_path=_resolve_backend_path(config.data_dir, config.db_path),
        schema=config.schema,
        table_name=config.table_name,
        embedding_base_url=config.embedding_base_url,
        embedding_fn=config.embedding_fn,
    )


_BACKEND_FACTORIES: dict[str, _BackendFactory] = {
    "fake": _create_fake_backend,
    "lancedb": _create_lancedb_backend,
}


def available_semantic_memory_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKEND_FACTORIES))


def create_semantic_memory_backend(
    backend: str,
    *,
    schema: SemanticIndexSchema,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    table_name: str = "semantic_memory",
    embedding_base_url: str | None = None,
    embedding_fn: SemanticEmbeddingFn | None = None,
) -> SemanticMemoryBackend:
    try:
        factory = _BACKEND_FACTORIES[backend]
    except KeyError as exc:
        raise ValueError(f"unknown semantic memory backend: {backend}") from exc
    return factory(
        SemanticBackendFactoryConfig(
            schema=schema,
            data_dir=Path("." if data_dir is None else data_dir),
            db_path=db_path,
            table_name=table_name,
            embedding_base_url=embedding_base_url,
            embedding_fn=embedding_fn,
        )
    )


def _resolve_backend_path(data_dir: Path, db_path: str | Path | None) -> Path:
    if db_path is None:
        return data_dir / "semantic-memory"
    path = Path(db_path).expanduser()
    if path.is_absolute():
        return path
    return data_dir / path
