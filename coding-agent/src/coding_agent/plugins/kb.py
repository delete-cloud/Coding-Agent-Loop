from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentkit.observability import ObservationSink, record_span
from agentkit.tape.tape import Tape

from coding_agent.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackRenderer,
    ContextPackSection,
    EvidenceRef,
)
from coding_agent.kb import KB, KBSearchResult

logger = logging.getLogger(__name__)

_CHUNK_TRUNCATE = 500
_CONTEXT_PACK_RENDERER = ContextPackRenderer(max_item_chars=_CHUNK_TRUNCATE)


@dataclass
class _SearchSnapshot:
    last_user_msg: str
    grounding_messages: list[dict[str, Any]]
    retrieval_attributes: dict[str, Any]


class KBPlugin:
    state_key = "kb"

    def __init__(
        self,
        *,
        db_path: Path,
        embedding_model: str = KB.DEFAULT_EMBEDDING_MODEL,
        embedding_dim: int = KB.DEFAULT_EMBEDDING_DIM,
        chunk_size: int = KB.DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = KB.DEFAULT_CHUNK_OVERLAP,
        top_k: int = 5,
        index_extensions: list[str] | None = None,
        text_extensions: list[str] | set[str] | None = None,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._top_k = top_k
        normalized_extensions = index_extensions
        if normalized_extensions is None and text_extensions is not None:
            normalized_extensions = list(text_extensions)

        self._index_extensions = normalized_extensions or [
            ".md",
            ".txt",
            ".rst",
            ".yaml",
            ".yml",
            ".toml",
        ]
        self._embedding_fn = embedding_fn
        self._kb: KB | None = None
        self._has_table = False
        self._snapshot: _SearchSnapshot | None = None
        self._observation_sink: ObservationSink | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "build_context": self.build_context,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        self._observation_sink = _observation_sink_from_context(kwargs.get("ctx"))
        self._kb = KB(
            db_path=self._db_path,
            embedding_model=self._embedding_model,
            embedding_dim=self._embedding_dim,
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            embedding_fn=self._embedding_fn,
            text_extensions=set(self._index_extensions),
        )
        self._has_table = self._kb.has_table()
        logger.info(
            "KBPlugin mounted: db_path=%s, has_table=%s",
            self._db_path,
            self._has_table,
        )
        return {"kb": self._kb, "has_table": self._has_table}

    def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        del kwargs
        if tape is None or not self._has_table or self._kb is None:
            return []

        user_message = _latest_user_message(tape)
        if user_message is None:
            return []

        if self._snapshot is not None and self._snapshot.last_user_msg == user_message:
            _record_retrieval_cache_hit(
                self._observation_sink,
                self._snapshot.retrieval_attributes,
            )
            return self._snapshot.grounding_messages

        with record_span(
            "retrieval.kb.search",
            sink=self._observation_sink,
            attributes=_retrieval_base_attributes(
                cache_hit=False,
                top_k=self._top_k,
            ),
        ) as span:
            results = self._kb.search_sync(user_message, k=self._top_k)
            retrieval_attributes = _retrieval_result_attributes(
                results,
                cache_hit=False,
                top_k=self._top_k,
            )
            for key, value in retrieval_attributes.items():
                span.set_attribute(key, value)

        pack = _context_pack_from_search_results(results)
        with record_span(
            "context_pack.render",
            sink=self._observation_sink,
            attributes=_context_pack_attributes(pack),
        ):
            grounding = _CONTEXT_PACK_RENDERER.render_messages(pack)

        self._snapshot = _SearchSnapshot(
            last_user_msg=user_message,
            grounding_messages=grounding,
            retrieval_attributes=retrieval_attributes,
        )
        return grounding


def _observation_sink_from_context(ctx: Any) -> ObservationSink | None:
    if ctx is None:
        return None
    config = getattr(ctx, "config", None)
    if not isinstance(config, Mapping):
        return None
    sink = config.get("observation_sink")
    if sink is None:
        return None
    if not isinstance(sink, ObservationSink):
        raise TypeError("observation_sink must implement ObservationSink")
    return sink


def _latest_user_message(tape: Tape) -> str | None:
    entries = (
        tape.windowed_entries() if hasattr(tape, "windowed_entries") else list(tape)
    )
    for entry in reversed(entries):
        if entry.kind != "message":
            continue
        role = entry.payload.get("role")
        content = entry.payload.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            return content
    return None


def _format_grounding_messages(results: list[KBSearchResult]) -> list[dict[str, Any]]:
    if not results:
        return []

    pack = _context_pack_from_search_results(results)
    return _CONTEXT_PACK_RENDERER.render_messages(pack)


def _record_retrieval_cache_hit(
    sink: ObservationSink | None,
    previous_attributes: dict[str, Any],
) -> None:
    attributes = dict(previous_attributes)
    attributes["retrieval.cache_hit"] = True
    with record_span(
        "retrieval.kb.search",
        sink=sink,
        attributes=attributes,
    ):
        return


def _retrieval_base_attributes(*, cache_hit: bool, top_k: int) -> dict[str, Any]:
    return {
        "retrieval.cache_hit": cache_hit,
        "retrieval.query_present": True,
        "retrieval.source_kind": "kb",
        "retrieval.top_k": top_k,
    }


def _retrieval_result_attributes(
    results: list[KBSearchResult],
    *,
    cache_hit: bool,
    top_k: int,
) -> dict[str, Any]:
    attributes = _retrieval_base_attributes(cache_hit=cache_hit, top_k=top_k)
    attributes["retrieval.candidate_count"] = len(results)
    attributes["retrieval.selected_count"] = len(results)
    attributes.update(_source_kind_count_attributes("retrieval", results))
    return attributes


def _source_kind_count_attributes(
    prefix: str,
    results: list[KBSearchResult],
) -> dict[str, int]:
    repo_file_count = 0
    test_failure_count = 0
    kb_chunk_count = 0
    other_source_count = 0
    for result in results:
        source_kind = _metadata_str(result.chunk.metadata, "source_kind") or "kb_chunk"
        if source_kind == "repo_file":
            repo_file_count += 1
        elif source_kind == "test_failure":
            test_failure_count += 1
        elif source_kind == "kb_chunk":
            kb_chunk_count += 1
        else:
            other_source_count += 1

    attributes = {
        f"{prefix}.repo_file_count": repo_file_count,
        f"{prefix}.test_failure_count": test_failure_count,
        f"{prefix}.kb_chunk_count": kb_chunk_count,
    }
    if other_source_count:
        attributes[f"{prefix}.other_source_count"] = other_source_count
    return attributes


def _context_pack_attributes(pack: ContextPack) -> dict[str, int]:
    attributes = {
        "pack.section_count": len(pack.sections),
        "pack.item_count": sum(len(section.items) for section in pack.sections),
        "pack.repo_file_count": 0,
        "pack.test_failure_count": 0,
        "pack.kb_chunk_count": 0,
    }
    other_source_count = 0
    for section in pack.sections:
        for item in section.items:
            if item.source_kind == "repo_file":
                attributes["pack.repo_file_count"] += 1
            elif item.source_kind == "test_failure":
                attributes["pack.test_failure_count"] += 1
            elif item.source_kind == "kb_chunk":
                attributes["pack.kb_chunk_count"] += 1
            else:
                other_source_count += 1
    if other_source_count:
        attributes["pack.other_source_count"] = other_source_count
    return attributes


def _context_pack_from_search_results(results: list[KBSearchResult]) -> ContextPack:
    repo_items: list[ContextPackItem] = []
    failure_items: list[ContextPackItem] = []
    kb_items: list[ContextPackItem] = []

    for rank, result in enumerate(results, start=1):
        item = _context_pack_item_from_search_result(result, rank=rank)
        if item.source_kind == "repo_file":
            repo_items.append(item)
        elif item.source_kind == "test_failure":
            failure_items.append(item)
        else:
            kb_items.append(item)

    sections: list[ContextPackSection] = []
    if repo_items:
        sections.append(
            ContextPackSection(title="Repo references", items=tuple(repo_items))
        )
    if failure_items:
        sections.append(
            ContextPackSection(title="Test failures", items=tuple(failure_items))
        )
    if kb_items:
        sections.append(
            ContextPackSection(title="KB references", items=tuple(kb_items))
        )
    return ContextPack(sections=tuple(sections))


def _context_pack_item_from_search_result(
    result: KBSearchResult,
    *,
    rank: int,
) -> ContextPackItem:
    metadata = result.chunk.metadata
    source_kind = _metadata_str(metadata, "source_kind") or "kb_chunk"
    source_id = _metadata_str(metadata, "source_id") or result.chunk.id
    label = _item_label(result, source_kind)
    return ContextPackItem(
        source_kind=source_kind,
        source_id=source_id,
        label=label,
        body=result.chunk.content.strip(),
        rank=rank,
        score=result.score,
        repo_path=_metadata_str(metadata, "repo_path"),
        line_start=_metadata_int(metadata, "line_start"),
        line_end=_metadata_int(metadata, "line_end"),
        evidence=(
            _evidence_ref_from_search_result(
                result,
                source_kind=source_kind,
                source_id=source_id,
            ),
        ),
    )


def _item_label(result: KBSearchResult, source_kind: str) -> str:
    metadata = result.chunk.metadata
    if source_kind == "test_failure":
        return _metadata_str(metadata, "test_node_id") or result.chunk.source
    return _metadata_str(metadata, "repo_path") or result.chunk.source


def _evidence_ref_from_search_result(
    result: KBSearchResult,
    *,
    source_kind: str,
    source_id: str,
) -> EvidenceRef:
    metadata = result.chunk.metadata
    label = (
        result.chunk.id
        if source_kind == "repo_file"
        else _item_label(
            result,
            source_kind,
        )
    )
    return EvidenceRef(
        kind=source_kind,
        source_id=source_id,
        label=label,
        repo_path=_metadata_str(metadata, "repo_path"),
        line_start=_metadata_int(metadata, "line_start"),
        line_end=_metadata_int(metadata, "line_end"),
        chunk_id=result.chunk.id if source_kind == "repo_file" else None,
        test_node_id=_metadata_str(metadata, "test_node_id"),
        command_label=_metadata_str(metadata, "command_label"),
    )


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    return value if isinstance(value, int) else None
