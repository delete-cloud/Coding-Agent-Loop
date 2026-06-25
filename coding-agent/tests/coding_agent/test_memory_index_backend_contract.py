from __future__ import annotations

import pytest
import lancedb

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.semantic_backends import (
    FakeSemanticMemoryBackend,
    LanceDBSemanticMemoryBackend,
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
    assert available_semantic_memory_backends() == ("fake", "lancedb")


def test_create_semantic_memory_backend_builds_fake_backend_with_schema() -> None:
    schema = _schema(embedding_model="custom-fake-model")

    backend = create_semantic_memory_backend("fake", schema=schema)

    assert isinstance(backend, FakeSemanticMemoryBackend)
    assert backend.schema == schema


def test_create_semantic_memory_backend_builds_lancedb_backend_with_config(
    tmp_path,
) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )

    backend = create_semantic_memory_backend(
        "lancedb",
        schema=schema,
        data_dir=tmp_path,
        db_path="semantic-memory",
        table_name="semantic_documents",
        embedding_fn=_embed,
    )

    assert isinstance(backend, LanceDBSemanticMemoryBackend)
    assert backend.schema == schema
    assert backend.db_path == tmp_path / "semantic-memory"
    assert backend.table_name == "semantic_documents"


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


@pytest.mark.asyncio
async def test_lancedb_backend_satisfies_memory_index_contract(tmp_path) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
        distance_metric="l2",
        score_normalization="l2_distance_to_similarity_v1",
    )
    backend = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=schema,
        embedding_fn=_embed,
    )
    await backend.ensure_schema(schema)
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

    assert [hit.memory_id for hit in hits] == [
        "topic-summary:topic-auth:1-3",
        "topic-summary:topic-cache:1-3",
    ]
    assert hits[0].score > hits[1].score
    assert hits[0].metadata == {
        "source_refs": ["topic:topic-auth"],
        "session_id": "session-1",
        "profile": "ops",
    }
    assert hits[0].source_refs == ("topic:topic-auth",)
    assert await backend.list_ids() == [
        "topic-summary:topic-auth:1-3",
        "topic-summary:topic-cache:1-3",
    ]

    await backend.delete("topic-summary:topic-cache:1-3")

    assert await backend.list_ids() == ["topic-summary:topic-auth:1-3"]


@pytest.mark.asyncio
async def test_lancedb_schema_identity_mismatch_persists_when_empty(tmp_path) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )
    first = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=schema,
        embedding_fn=_embed,
    )
    await first.ensure_schema(schema)

    second = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=_schema(
            embedding_provider_id="test",
            embedding_model="deterministic-v1",
            backend_adapter_id="lancedb",
        ),
        embedding_fn=_embed,
    )

    with pytest.raises(SemanticSchemaMismatch, match="semantic memory schema mismatch"):
        await second.ensure_schema(second.schema)

    with pytest.raises(SemanticSchemaMismatch, match="semantic memory schema mismatch"):
        await second.search("jwt authentication", limit=2)

    with pytest.raises(SemanticSchemaMismatch, match="semantic memory schema mismatch"):
        await second.search("   ", limit=2)


@pytest.mark.asyncio
async def test_lancedb_search_checks_persisted_schema_before_embedding(
    tmp_path,
) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )
    first = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=schema,
        embedding_fn=_embed,
    )
    await first.ensure_schema(schema)
    await first.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-auth",)},
    )

    def unexpected_embed(texts: list[str]) -> list[list[float]]:
        raise AssertionError("search embedded before checking persisted schema")

    second = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=_schema(
            embedding_provider_id="test",
            embedding_model="deterministic-v1",
            embedding_dim=16,
            backend_adapter_id="lancedb",
        ),
        embedding_fn=unexpected_embed,
    )

    with pytest.raises(SemanticSchemaMismatch, match="semantic memory schema mismatch"):
        await second.search("jwt authentication", limit=2)


@pytest.mark.asyncio
async def test_lancedb_unversioned_existing_table_requires_explicit_rebuild(
    tmp_path,
) -> None:
    db_path = tmp_path / "semantic-memory"
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )
    first = LanceDBSemanticMemoryBackend(
        db_path=db_path,
        schema=schema,
        embedding_fn=_embed,
    )
    await first.ensure_schema(schema)
    await first.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-auth",)},
    )
    _drop_lancedb_schema_table(db_path)

    def unexpected_embed(texts: list[str]) -> list[list[float]]:
        raise AssertionError("operation embedded before rejecting unversioned table")

    rebuilt_schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v1",
        embedding_dim=16,
        backend_adapter_id="lancedb",
    )
    second = LanceDBSemanticMemoryBackend(
        db_path=db_path,
        schema=rebuilt_schema,
        embedding_fn=unexpected_embed,
    )

    operations = (
        lambda: second.search("jwt authentication", limit=2),
        lambda: second.search("   ", limit=2),
        lambda: second.list_ids(),
        lambda: second.delete("topic-summary:topic-auth:1-3"),
        lambda: second.upsert(
            "topic-summary:topic-cache:1-3",
            "cache ttl redis",
            {"source_refs": ("topic:topic-cache",)},
        ),
        lambda: second.ensure_schema(rebuilt_schema),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="semantic lancedb schema metadata"):
            await operation()

    assert _lancedb_memory_ids(db_path) == ["topic-summary:topic-auth:1-3"]

    rebuilt = LanceDBSemanticMemoryBackend(
        db_path=db_path,
        schema=rebuilt_schema,
        embedding_fn=_embed16,
    )
    await rebuilt.ensure_schema(rebuilt_schema, allow_rebuild=True)
    await rebuilt.upsert(
        "topic-summary:topic-cache:1-3",
        "cache ttl redis",
        {"source_refs": ("topic:topic-cache",)},
    )

    assert await rebuilt.list_ids() == ["topic-summary:topic-cache:1-3"]


@pytest.mark.asyncio
async def test_lancedb_schema_rebuild_recreates_vector_table_for_new_dimension(
    tmp_path,
) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )
    backend = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=schema,
        embedding_fn=_embed,
    )
    await backend.ensure_schema(schema)
    await backend.upsert(
        "topic-summary:topic-auth:1-3",
        "authentication middleware jwt",
        {"source_refs": ("topic:topic-auth",)},
    )

    rebuilt_schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v1",
        embedding_dim=16,
        backend_adapter_id="lancedb",
    )
    rebuilt = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=rebuilt_schema,
        embedding_fn=_embed16,
    )
    await rebuilt.ensure_schema(rebuilt_schema, allow_rebuild=True)
    await rebuilt.upsert(
        "topic-summary:topic-cache:1-3",
        "cache ttl redis",
        {"source_refs": ("topic:topic-cache",)},
    )

    assert await rebuilt.list_ids() == ["topic-summary:topic-cache:1-3"]


@pytest.mark.asyncio
async def test_lancedb_scoped_delete_cannot_delete_across_scopes(tmp_path) -> None:
    schema = _schema(
        embedding_provider_id="test",
        embedding_model="deterministic-v0",
        backend_adapter_id="lancedb",
    )
    backend = LanceDBSemanticMemoryBackend(
        db_path=tmp_path / "semantic-memory",
        schema=schema,
        embedding_fn=_embed,
    )
    await backend.ensure_schema(schema)
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


def _drop_lancedb_schema_table(db_path) -> None:
    db = lancedb.connect(str(db_path))
    db.drop_table("semantic_memory_schema")


def _lancedb_memory_ids(db_path) -> list[str]:
    db = lancedb.connect(str(db_path))
    rows = db.open_table("semantic_memory").search().select(["memory_id"]).to_list()
    return sorted(row["memory_id"] for row in rows)


def _embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        vectors.append(
            [
                1.0 if token in lower else 0.0
                for token in (
                    "authentication",
                    "middleware",
                    "jwt",
                    "cache",
                    "ttl",
                    "redis",
                    "token",
                    "refresh",
                )
            ]
        )
    return vectors


def _embed16(texts: list[str]) -> list[list[float]]:
    return [vector + ([0.0] * 8) for vector in _embed(texts)]
