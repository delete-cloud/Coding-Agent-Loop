"""LanceDB semantic memory backend for tape-native memory."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
from typing import Any

import lancedb
import pyarrow as pa

from agentkit.storage.protocols import MemoryHit
from coding_agent.topics.semantic_index import SemanticDocId

from .base import (
    SemanticBackendScope,
    SemanticEmbeddingFn,
    SemanticIndexSchema,
    SemanticSchemaMismatch,
    memory_hit_from_document,
    normalize_source_refs,
)

LANCEDB_SEMANTIC_INDEX_SCHEMA = SemanticIndexSchema(
    schema_version=1,
    embedding_provider_id="openai",
    embedding_model="text-embedding-3-small",
    embedding_dim=1536,
    backend_adapter_id="lancedb",
    backend_schema_version=1,
    distance_metric="l2",
    score_normalization="l2_distance_to_similarity_v1",
)

_TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SCHEMA_KEY = "semantic-memory-schema"


class LanceDBSemanticMemoryBackend:
    """LanceDB-backed semantic memory index.

    The adapter owns embedding and vector storage, while authoritative memory
    text and review/topic state stay in Coding Agent stores.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        schema: SemanticIndexSchema = LANCEDB_SEMANTIC_INDEX_SCHEMA,
        table_name: str = "semantic_memory",
        embedding_base_url: str | None = None,
        embedding_fn: SemanticEmbeddingFn | None = None,
    ) -> None:
        _require_table_name(table_name)
        self._db_path = Path(db_path).expanduser()
        self._schema = schema
        self._table_name = table_name
        self._schema_table_name = f"{table_name}_schema"
        self._embedding_base_url = embedding_base_url
        self._embedding_fn = embedding_fn
        self._openai_client: Any | None = None
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))
        self._table: Any | None = None
        self._schema_table: Any | None = None

    @property
    def schema(self) -> SemanticIndexSchema:
        return self._schema

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def table_name(self) -> str:
        return self._table_name

    async def ensure_schema(
        self,
        schema: SemanticIndexSchema,
        *,
        allow_rebuild: bool = False,
    ) -> None:
        existing = self._load_schema()
        if existing is None:
            if self._has_document_table():
                if not allow_rebuild:
                    raise _missing_schema_metadata_error(self._table_name)
                self._drop_document_table()
            self._write_schema(schema)
            self._schema = schema
            return
        if existing == schema:
            self._schema = schema
            return
        if not allow_rebuild:
            raise SemanticSchemaMismatch(existing=existing, requested=schema)
        self._drop_document_table()
        self._write_schema(schema)
        self._schema = schema

    async def upsert(
        self,
        memory_id: str,
        text: str,
        metadata: dict[str, object],
    ) -> None:
        self._ensure_schema_for_write()
        document_id = SemanticDocId.parse(memory_id)
        metadata_json = _metadata_json(memory_id=str(document_id), metadata=metadata)
        vector = (await self._embed([text]))[0]
        row = {
            "memory_id": str(document_id),
            "text": text,
            "metadata": metadata_json,
            "vector": vector,
        }
        table = self._get_table()
        (
            table.merge_insert("memory_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )

    async def search(self, query: str, limit: int = 10) -> list[MemoryHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._ensure_current_schema()
        if not query.strip():
            return []
        if self._table_name not in self._table_names():
            return []
        query_vector = (await self._embed([query]))[0]
        rows = self._get_table().search(query_vector).limit(limit).to_list()
        return sorted(
            (_hit_from_row(row) for row in rows),
            key=lambda hit: (-hit.score, hit.memory_id),
        )

    async def delete(self, memory_id: str) -> None:
        document_id = SemanticDocId.parse(memory_id)
        self._ensure_current_schema()
        if self._table_name not in self._table_names():
            return
        self._get_table().delete(f"memory_id = {_sql_quote(str(document_id))}")

    async def list_ids(
        self,
        *,
        scope: SemanticBackendScope | None = None,
    ) -> list[str]:
        self._ensure_current_schema()
        if self._table_name not in self._table_names():
            return []
        rows = self._get_table().search().select(["memory_id", "metadata"]).to_list()
        memory_ids: list[str] = []
        for row in rows:
            memory_id = row.get("memory_id")
            if not isinstance(memory_id, str):
                continue
            if scope is None:
                memory_ids.append(memory_id)
                continue
            metadata = _metadata_from_row(row)
            if scope.matches(
                memory_id=memory_id,
                metadata=metadata,
                source_refs=normalize_source_refs(memory_id, metadata),
            ):
                memory_ids.append(memory_id)
        return sorted(memory_ids)

    async def delete_scope(self, scope: SemanticBackendScope) -> int:
        memory_ids = await self.list_ids(scope=scope)
        for memory_id in memory_ids:
            await self.delete(memory_id)
        return len(memory_ids)

    def _table_names(self) -> set[str]:
        listed = self._db.list_tables()
        if isinstance(listed, list):
            return {str(name) for name in listed}
        tables = getattr(listed, "tables", None)
        if isinstance(tables, list):
            return {str(name) for name in tables}
        raise TypeError(f"unsupported list_tables() result: {type(listed)!r}")

    def _get_table(self) -> Any:
        if self._table is not None:
            return self._table
        if self._table_name in self._table_names():
            self._table = self._db.open_table(self._table_name)
            return self._table
        self._table = self._db.create_table(
            self._table_name,
            schema=pa.schema(
                [
                    ("memory_id", pa.string()),
                    ("text", pa.string()),
                    ("metadata", pa.string()),
                    ("vector", pa.list_(pa.float64(), self._schema.embedding_dim)),
                ]
            ),
        )
        return self._table

    def _get_schema_table(self) -> Any:
        if self._schema_table is not None:
            return self._schema_table
        if self._schema_table_name in self._table_names():
            self._schema_table = self._db.open_table(self._schema_table_name)
            return self._schema_table
        self._schema_table = self._db.create_table(
            self._schema_table_name,
            schema=pa.schema(
                [
                    ("key", pa.string()),
                    ("payload", pa.string()),
                ]
            ),
        )
        return self._schema_table

    def _load_schema(self) -> SemanticIndexSchema | None:
        if self._schema_table_name not in self._table_names():
            return None
        rows = (
            self._get_schema_table()
            .search()
            .where(f"key = {_sql_quote(_SCHEMA_KEY)}")
            .limit(1)
            .to_list()
        )
        if not rows:
            return None
        payload = rows[0].get("payload")
        if not isinstance(payload, str):
            raise ValueError("semantic lancedb schema payload must be a string")
        return SemanticIndexSchema(**json.loads(payload))

    def _write_schema(self, schema: SemanticIndexSchema) -> None:
        payload = json.dumps(
            asdict(schema),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        table = self._get_schema_table()
        (
            table.merge_insert("key")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([{"key": _SCHEMA_KEY, "payload": payload}])
        )

    def _drop_document_table(self) -> None:
        if self._table_name not in self._table_names():
            return
        self._db.drop_table(self._table_name)
        self._table = None

    def _has_document_table(self) -> bool:
        return self._table_name in self._table_names()

    def _ensure_schema_for_write(self) -> None:
        existing = self._load_schema()
        if existing is None:
            if self._has_document_table():
                raise _missing_schema_metadata_error(self._table_name)
            self._write_schema(self._schema)
            return
        self._raise_if_schema_mismatch(existing)

    def _ensure_current_schema(self) -> None:
        existing = self._load_schema()
        if existing is None:
            if self._has_document_table():
                raise _missing_schema_metadata_error(self._table_name)
            return
        self._raise_if_schema_mismatch(existing)

    def _raise_if_schema_mismatch(self, existing: SemanticIndexSchema) -> None:
        if existing != self._schema:
            raise SemanticSchemaMismatch(existing=existing, requested=self._schema)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedding_fn is not None:
            vectors = self._embedding_fn(texts)
        else:
            client = self._get_openai_client()
            response = await client.embeddings.create(
                model=self._schema.embedding_model,
                input=texts,
            )
            vectors = [item.embedding for item in response.data]
        _validate_embeddings(
            vectors, expected_count=len(texts), dim=self._schema.embedding_dim
        )
        return vectors

    def _get_openai_client(self) -> Any:
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ImportError(
                    "OpenAI package is required for semantic memory embeddings"
                ) from exc
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is required for "
                    "semantic memory embeddings when embedding_fn is not provided"
                )
            kwargs: dict[str, object] = {"api_key": api_key}
            if self._embedding_base_url is not None:
                kwargs["base_url"] = self._embedding_base_url
            self._openai_client = AsyncOpenAI(**kwargs)
        return self._openai_client


def _hit_from_row(row: dict[str, Any]) -> MemoryHit:
    memory_id = _required_str(row, "memory_id")
    text = _required_str(row, "text")
    metadata = _metadata_from_row(row)
    distance = row.get("_distance", 0.0)
    if not isinstance(distance, int | float):
        raise TypeError("semantic lancedb row _distance must be numeric")
    return memory_hit_from_document(
        memory_id=memory_id,
        text=text,
        score=_distance_to_score(float(distance)),
        metadata=metadata,
    )


def _metadata_from_row(row: dict[str, Any]) -> dict[str, object]:
    raw_metadata = _required_str(row, "metadata")
    metadata = json.loads(raw_metadata)
    if not isinstance(metadata, dict):
        raise TypeError("semantic lancedb row metadata must decode to an object")
    return metadata


def _metadata_json(
    *,
    memory_id: str,
    metadata: dict[str, object],
) -> str:
    stored_metadata = dict(metadata)
    stored_metadata["source_refs"] = list(normalize_source_refs(memory_id, metadata))
    return json.dumps(
        stored_metadata,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _distance_to_score(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


def _validate_embeddings(
    vectors: list[list[float]],
    *,
    expected_count: int,
    dim: int,
) -> None:
    if len(vectors) != expected_count:
        raise ValueError("embedding function returned the wrong number of vectors")
    for index, vector in enumerate(vectors):
        if len(vector) != dim:
            raise ValueError(
                f"embedding vector {index} has dimension {len(vector)}, expected {dim}"
            )


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"semantic lancedb row {key} must be a string")
    return value


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _require_table_name(value: str) -> None:
    if _TABLE_NAME_RE.fullmatch(value) is None:
        raise ValueError(
            "semantic lancedb table name must contain only letters, numbers, "
            "or underscores, and start with a letter"
        )


def _missing_schema_metadata_error(table_name: str) -> ValueError:
    return ValueError(
        "semantic lancedb schema metadata is missing for existing table "
        f"{table_name!r}; run semantic index rebuild explicitly"
    )
