import asyncio
from pathlib import Path

from coding_agent.kb import KB, KBSearchResult


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[float(index)] * 8 for index, _ in enumerate(texts, start=1)]


class TestSearchSync:
    def test_search_sync_returns_results(self, tmp_path: Path):
        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
            text_extensions={".md"},
        )

        asyncio.run(
            kb.index_file(
                Path("doc.md"),
                "Hello world this is a test document about Python programming.",
            )
        )

        results = kb.search_sync("Python", k=3)

        assert len(results) == 1
        assert all(isinstance(result, KBSearchResult) for result in results)
        assert results[0].chunk.source == "doc.md"
        assert "Python programming" in results[0].chunk.content

    def test_search_sync_empty_query_returns_empty(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "kb_db", embedding_dim=8, embedding_fn=_fake_embed)

        results = kb.search_sync("", k=5)

        assert results == []

    def test_search_sync_no_table_returns_empty(self, tmp_path: Path):
        kb = KB(db_path=tmp_path / "kb_db", embedding_dim=8, embedding_fn=_fake_embed)

        results = kb.search_sync("anything", k=5)

        assert results == []


class TestIndexDirectoryExtensions:
    def test_index_directory_uses_constructor_extensions(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "keep.md").write_text("markdown content", encoding="utf-8")
        (docs / "skip.py").write_text("print('ignore me')", encoding="utf-8")

        kb = KB(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
            text_extensions={".md"},
        )

        asyncio.run(kb.index_directory(docs, show_progress=False))

        results = kb.search_sync("markdown", k=5)

        assert len(results) == 1
        assert results[0].chunk.source.endswith("keep.md")
        assert "markdown content" in results[0].chunk.content
