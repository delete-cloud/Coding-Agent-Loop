from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentkit.tape.tape import Tape

from coding_agent.kb import KB, KBSearchResult


class KBPlugin:
    state_key = "kb"

    def __init__(
        self,
        db_path: Path,
        embedding_model: str = "text-embedding-3-small",
        embedding_dim: int = 1536,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
        top_k: int = 5,
        text_extensions: set[str] | None = None,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._top_k = top_k
        self._text_extensions = text_extensions
        self._embedding_fn = embedding_fn

        self._kb: KB | None = None
        self._has_table = False
        self._last_user_message: str | None = None
        self._last_grounding: list[dict[str, Any]] = []

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "build_context": self.build_context,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        self._kb = KB(
            db_path=self._db_path,
            embedding_model=self._embedding_model,
            embedding_dim=self._embedding_dim,
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            embedding_fn=self._embedding_fn,
            text_extensions=self._text_extensions,
        )
        self._has_table = self._kb.has_table()
        return {"kb": self._kb, "has_table": self._has_table}

    def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        if tape is None or self._kb is None or not self._has_table:
            return []

        user_message = self._latest_user_message(tape)
        if not user_message:
            return []

        if user_message == self._last_user_message:
            return self._last_grounding

        results = self._kb.search_sync(user_message, k=self._top_k)
        grounding = self._format_grounding(results)
        self._last_user_message = user_message
        self._last_grounding = grounding
        return grounding

    def _latest_user_message(self, tape: Tape) -> str | None:
        for entry in reversed(list(tape)):
            if entry.kind != "message":
                continue
            if entry.payload.get("role") != "user":
                continue
            content = entry.payload.get("content")
            if isinstance(content, str) and content.strip():
                return content
        return None

    def _format_grounding(self, results: list[KBSearchResult]) -> list[dict[str, Any]]:
        if not results:
            return []

        lines = ["[KB] The following code/documentation snippets may be relevant:", ""]
        for result in results:
            content = result.chunk.content
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"--- {result.chunk.source} ---")
            lines.append(content)
            lines.append("")

        return [{"role": "system", "content": "\n".join(lines).rstrip()}]
