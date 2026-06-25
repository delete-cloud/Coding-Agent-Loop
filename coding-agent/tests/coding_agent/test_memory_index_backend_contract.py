from __future__ import annotations

import pytest

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.semantic_backends import (
    FakeSemanticMemoryBackend,
    SemanticBackendScope,
    SemanticIndexSchema,
    SemanticSchemaMismatch,
    available_semantic_memory_backends,
    create_semantic_memory_backend,
)


def _schema(**overrides: object) -> SemanticIndexSchema:
    values = {
        "schema_version": 1,
        "embedding_provider_id": "fake",
        "embedding_model": "fake-memory-v0",
        "embedding_dim": 8,
        "backend_adapter_id": "fake",
        "backend_schema_version": 1,
        "distance_metric": "cosine",
        "score_normalization": "overlap_high_is_better_v1",
    }
    values.update(overrides)
    return SemanticIndexSchema(**values)


def test_available_semantic_memory_backends_lists_registered_backends() -> None:
    assert available_semantic_memory_backends() == ("fake",)


def test_create_semantic_memory_backend_builds_fake_backend_with_schema() -> None:
    schema = _schema(embedding_model="custom-fake-model")

    backend = create_semantic_memory_backend("fake", schema=schema)

    assert isinstance(backend, FakeSemanticMemoryBackend)
    assert backend.schema == schema


def test_create_semantic_memory_backend_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown semantic memory backend: unknown"):
        create_semantic_memory_backend("unknown", schema=_schema())


@pytest.mark.asyncio
async def test_fake_backend_satisfies_memory_index_contract() -> None:
    backend = FakeSemanticMemoryBackend()
    await backend.ensure_schema(_schema())
    await backend.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {
            "source_refs": ("topic:topic-auth",),
            "session_id": "session-1",
            "profile": "ops",
        },
    )
    await backend.upsert(
        "topic-summary:topic-cache:1-3",
        "cache ttl redis",
        {
            "source_refs": ("topic:topic-cache",),
            "session_id": "session-1",
            "profile": "ops",
        },
    )

    hits = await backend.search("jwt authentication", limit=2)

    assert hits == [
        MemoryHit(
            memory_id="topic-summary:topic-auth:1-3",
            text="authentication middleware jwt",
            score=1.0,
            metadata={
                "source_refs": ("topic:topic-auth",),
                "session_id": "session-1",
                "profile": "ops",
            },
            source_refs=("topic:topic-auth",),
        ),
        MemoryHit(
            memory_id="topic-summary:topic-cache:1-3",
            text="cache ttl redis",
            score=0.0,
            metadata={
                "source_refs": ("topic:topic-cache",),
                "session_id": "session-1",
                "profile": "ops",
            },
            source_refs=("topic:topic-cache",),
        ),
    ]
    assert await backend.list_ids() == [
        "topic-summary:topic-auth:1-3",
        "topic-summary:topic-cache:1-3",
    ]

    await backend.delete("topic-summary:topic-cache:1-3")

    assert await backend.list_ids() == ["topic-summary:topic-auth:1-3"]


@pytest.mark.asyncio
async def test_schema_identity_mismatch_fails_clearly() -> None:
    backend = FakeSemanticMemoryBackend()
    await backend.ensure_schema(_schema())
    await backend.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-auth",)},
    )

    with pytest.raises(SemanticSchemaMismatch, match="semantic memory schema mismatch"):
        await backend.ensure_schema(_schema(embedding_dim=16))

    assert await backend.list_ids() == ["topic-summary:topic-auth:1-3"]


@pytest.mark.asyncio
async def test_schema_identity_mismatch_requires_explicit_rebuild_and_clears_stale_docs() -> (
    None
):
    backend = FakeSemanticMemoryBackend()
    await backend.ensure_schema(_schema())
    await backend.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-auth",)},
    )

    await backend.ensure_schema(_schema(embedding_dim=16), allow_rebuild=True)

    assert await backend.list_ids() == []
    await backend.upsert(
        "topic-summary:topic-cache:1-3",
        "cache ttl redis",
        {"source_refs": ("topic:topic-cache",)},
    )
    assert await backend.list_ids() == ["topic-summary:topic-cache:1-3"]


def test_source_scope_grammar_rejects_invalid_scopes() -> None:
    with pytest.raises(ValueError, match="semantic backend scope source kind"):
        SemanticBackendScope(source_kind="run", source_id="topic-auth")

    with pytest.raises(ValueError, match="semantic backend scope source id"):
        SemanticBackendScope(source_kind="topic", source_id="topic:auth")

    with pytest.raises(ValueError, match="semantic backend scope session id"):
        SemanticBackendScope(
            source_kind="topic",
            source_id="topic-auth",
            session_id="../session",
        )


@pytest.mark.asyncio
async def test_scoped_delete_cannot_delete_across_scopes() -> None:
    backend = FakeSemanticMemoryBackend()
    await backend.ensure_schema(_schema())
    await backend.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {
            "source_refs": ("topic:topic-auth",),
            "session_id": "session-1",
            "profile": "ops",
        },
    )
    await backend.upsert(
        "topic-summary:topic-auth:4-9",
        "authentication token refresh",
        {
            "source_refs": ("topic:topic-auth",),
            "session_id": "session-2",
            "profile": "ops",
        },
    )
    await backend.upsert(
        "topic-summary:topic-cache:1-3",
        "cache ttl redis",
        {
            "source_refs": ("topic:topic-cache",),
            "session_id": "session-1",
            "profile": "ops",
        },
    )

    deleted = await backend.delete_scope(
        SemanticBackendScope(
            source_kind="topic",
            source_id="topic-auth",
            session_id="session-1",
            profile="ops",
        )
    )

    assert deleted == 1
    assert await backend.list_ids() == [
        "topic-summary:topic-auth:4-9",
        "topic-summary:topic-cache:1-3",
    ]
    assert await backend.list_ids(
        scope=SemanticBackendScope(source_kind="topic", source_id="topic-auth")
    ) == ["topic-summary:topic-auth:4-9"]


@pytest.mark.asyncio
async def test_memory_hit_score_is_normalized_high_is_better_by_result_ordering() -> (
    None
):
    backend = FakeSemanticMemoryBackend()
    await backend.ensure_schema(_schema())
    await backend.upsert(
        "topic-summary:topic-low:1-3",
        "authentication",
        {"source_refs": ("topic:topic-low",)},
    )
    await backend.upsert(
        "topic-summary:topic-high:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-high",)},
    )
    await backend.upsert(
        "topic-summary:topic-tie:1-3",
        "jwt middleware authentication",
        {"source_refs": ("topic:topic-tie",)},
    )

    hits = await backend.search("authentication middleware jwt", limit=3)

    assert [(hit.memory_id, hit.score) for hit in hits] == [
        ("topic-summary:topic-high:1-3", 1.0),
        ("topic-summary:topic-tie:1-3", 1.0),
        ("topic-summary:topic-low:1-3", pytest.approx(1 / 3)),
    ]
