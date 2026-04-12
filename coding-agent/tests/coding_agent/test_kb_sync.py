from __future__ import annotations

import asyncio
from pathlib import Path

from coding_agent.kb import KB, KBSearchResult


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class TestSearchSync:
    def test_search_sync_returns_results(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)

        asyncio.run(
            kb.index_file(
                Path("doc.md"),
                "Hello world this is a test document about Python programming",
            )
        )

        results = kb.search_sync("Python", k=3)

        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(r, KBSearchResult) for r in results)

    def test_search_sync_empty_query_returns_empty(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)

        results = kb.search_sync("", k=5)

        assert results == []

    def test_search_sync_no_table_returns_empty(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)

        results = kb.search_sync("anything", k=5)

        assert results == []


class TestKBTextExtensions:
    def test_index_directory_respects_constructor_text_extensions(self, tmp_path: Path):
        (tmp_path / "keep.custom").write_text("custom indexed content")
        (tmp_path / "skip.py").write_text("python content")

        kb = KB(
            db_path=tmp_path / "test_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
            text_extensions={".custom"},
        )

        asyncio.run(kb.index_directory(tmp_path, show_progress=False))

        results = kb.search_sync("custom", k=5)

        assert len(results) > 0
        assert all(r.chunk.source.endswith("keep.custom") for r in results)
