from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentkit.tape.tape import Tape

from coding_agent.kb import KB, KBSearchResult

logger = logging.getLogger(__name__)

_CHUNK_TRUNCATE = 500


@dataclass
class _SearchSnapshot:
    last_user_msg: str
    grounding_messages: list[dict[str, Any]]


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

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "build_context": self.build_context,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
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
            return self._snapshot.grounding_messages

        results = self._kb.search_sync(user_message, k=self._top_k)
        grounding = _format_grounding_messages(results)
        self._snapshot = _SearchSnapshot(
            last_user_msg=user_message,
            grounding_messages=grounding,
        )
        return grounding


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

    lines = ["[KB] Relevant context:"]
    for result in results:
        content = result.chunk.content.strip()
        if len(content) > _CHUNK_TRUNCATE:
            content = f"{content[:_CHUNK_TRUNCATE]}..."
        lines.append(f"- {result.chunk.source}: {content}")
    return [{"role": "system", "content": "\n".join(lines)}]
