"""Coding Agent semantic memory backend contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable

from agentkit.storage.protocols import MemoryHit, MemoryIndex
from coding_agent.topics.semantic_index import (
    SemanticDocId,
    SemanticDocKind,
    SemanticSourceKind,
    SemanticSourceRef,
)

_SEMANTIC_SCOPE_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class SemanticIndexSchema:
    schema_version: int
    embedding_provider_id: str
    embedding_model: str
    embedding_dim: int
    backend_adapter_id: str
    backend_schema_version: int
    distance_metric: str
    score_normalization: str

    def __post_init__(self) -> None:
        _require_positive_int("semantic schema version", self.schema_version)
        _require_non_empty_token(
            "semantic embedding provider id", self.embedding_provider_id
        )
        _require_non_empty_token("semantic embedding model", self.embedding_model)
        _require_positive_int("semantic embedding dimension", self.embedding_dim)
        _require_non_empty_token("semantic backend adapter id", self.backend_adapter_id)
        _require_positive_int(
            "semantic backend schema version", self.backend_schema_version
        )
        _require_non_empty_token("semantic distance metric", self.distance_metric)
        _require_non_empty_token(
            "semantic score normalization", self.score_normalization
        )


class SemanticSchemaMismatch(ValueError):
    """Raised when a backend already contains a different semantic schema."""

    def __init__(
        self,
        *,
        existing: SemanticIndexSchema,
        requested: SemanticIndexSchema,
    ) -> None:
        super().__init__(
            "semantic memory schema mismatch: "
            f"existing={existing!r} requested={requested!r}"
        )
        self.existing = existing
        self.requested = requested


@dataclass(frozen=True, slots=True)
class SemanticBackendScope:
    source_kind: SemanticSourceKind | str
    source_id: str
    session_id: str | None = None
    profile: str | None = None

    def __post_init__(self) -> None:
        source_kind = _coerce_source_kind(self.source_kind)
        _require_safe_scope_part("semantic backend scope source id", self.source_id)
        if self.session_id is not None:
            _require_safe_scope_part(
                "semantic backend scope session id", self.session_id
            )
        if self.profile is not None:
            _require_safe_scope_part("semantic backend scope profile", self.profile)
        object.__setattr__(self, "source_kind", source_kind)

    @classmethod
    def for_source_ref(
        cls,
        source_ref: SemanticSourceRef | str,
        *,
        session_id: str | None = None,
        profile: str | None = None,
    ) -> SemanticBackendScope:
        ref = SemanticSourceRef.parse(source_ref)
        return cls(
            source_kind=ref.kind,
            source_id=ref.source_id,
            session_id=session_id,
            profile=profile,
        )

    @property
    def source_ref(self) -> str:
        if not isinstance(self.source_kind, SemanticSourceKind):
            raise TypeError("semantic backend scope source kind was not normalized")
        return str(SemanticSourceRef(kind=self.source_kind, source_id=self.source_id))

    def matches(
        self,
        *,
        memory_id: str,
        metadata: dict[str, object],
        source_refs: tuple[str, ...],
    ) -> bool:
        refs = source_refs or (_primary_source_ref_for_memory_id(memory_id),)
        if self.source_ref not in refs:
            return False
        if (
            self.session_id is not None
            and metadata.get("session_id") != self.session_id
        ):
            return False
        if self.profile is not None:
            metadata_profile = metadata.get("profile", metadata.get("profile_id"))
            if metadata_profile != self.profile:
                return False
        return True


@runtime_checkable
class SemanticMemoryBackend(MemoryIndex, Protocol):
    """Coding Agent operational extension for semantic memory backends."""

    async def ensure_schema(
        self,
        schema: SemanticIndexSchema,
        *,
        allow_rebuild: bool = False,
    ) -> None: ...

    async def list_ids(
        self,
        *,
        scope: SemanticBackendScope | None = None,
    ) -> list[str]: ...

    async def delete_scope(self, scope: SemanticBackendScope) -> int: ...


def normalize_source_refs(
    memory_id: str,
    metadata: dict[str, object],
) -> tuple[str, ...]:
    raw_refs = metadata.get("source_refs", ())
    if raw_refs is None:
        return (_primary_source_ref_for_memory_id(memory_id),)
    if isinstance(raw_refs, str):
        refs = (raw_refs,)
    elif isinstance(raw_refs, list | tuple):
        refs = tuple(raw_refs)
    else:
        raise TypeError("semantic memory metadata source_refs must be a string list")
    normalized = tuple(sorted({str(SemanticSourceRef.parse(ref)) for ref in refs}))
    if not normalized:
        return (_primary_source_ref_for_memory_id(memory_id),)
    return normalized


def memory_hit_from_document(
    *,
    memory_id: str,
    text: str,
    score: float,
    metadata: dict[str, object],
) -> MemoryHit:
    return MemoryHit(
        memory_id=memory_id,
        text=text,
        score=score,
        metadata=dict(metadata),
        source_refs=normalize_source_refs(memory_id, metadata),
    )


def _primary_source_ref_for_memory_id(memory_id: str) -> str:
    document_id = SemanticDocId.parse(memory_id)
    if document_id.kind is SemanticDocKind.TOPIC_SUMMARY:
        return str(
            SemanticSourceRef(
                kind=SemanticSourceKind.TOPIC,
                source_id=document_id.source_id,
            )
        )
    if document_id.kind is SemanticDocKind.ACCEPTED_REVIEWED_MEMORY:
        return str(
            SemanticSourceRef(
                kind=SemanticSourceKind.ACCEPTED_MEMORY,
                source_id=document_id.source_id,
            )
        )
    raise ValueError("semantic document kind is not supported")


def _coerce_source_kind(value: SemanticSourceKind | str) -> SemanticSourceKind:
    try:
        return SemanticSourceKind(value)
    except ValueError as exc:
        raise ValueError(
            f"semantic backend scope source kind is not supported: {value}"
        ) from exc


def _require_safe_scope_part(field_name: str, value: str) -> None:
    if _SEMANTIC_SCOPE_PART_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must contain only letters, numbers, dot, underscore, or dash"
        )


def _require_non_empty_token(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_positive_int(field_name: str, value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
