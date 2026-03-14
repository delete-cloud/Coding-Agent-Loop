import os
import tempfile
import unittest
from pathlib import Path

from kb.server import KB, _load_text_file, _stable_doc_path


class _FakeArrow:
    def __init__(self, rows):
        self._rows = rows

    def to_pylist(self):
        return list(self._rows)


class _FakeSearcher:
    def __init__(self, *, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    def where(self, _where):
        return self

    def limit(self, _n):
        return self

    def to_arrow(self):
        if self._error is not None:
            raise self._error
        return _FakeArrow(self._rows)

    def to_pandas(self):
        if self._error is not None:
            raise self._error

        class _FakeDataFrame:
            def __init__(self, rows):
                self._rows = rows

            def to_dict(self, orient="records"):
                if orient != "records":
                    raise ValueError("unexpected orient")
                return list(self._rows)

        return _FakeDataFrame(self._rows)


class _FakeTable:
    def __init__(self, *, add_error=None):
        self.calls = []
        self.rows_added = []
        self._add_error = add_error

    def search(self, query, query_type=None):
        self.calls.append((query, query_type))
        if query_type == "hybrid":
            return _FakeSearcher(error=ValueError("No embedding function for vector"))
        if isinstance(query, list):
            return _FakeSearcher(
                rows=[
                    {
                        "id": "eval/ab/kb/rag_pipeline.md:0:466",
                        "path": "eval/ab/kb/rag_pipeline.md",
                        "heading": "RAG Pipeline",
                        "start": 0,
                        "end": 466,
                        "text": "pipeline text",
                        "_distance": 0.53,
                    }
                ]
            )
        return _FakeSearcher(rows=[])

    def add(self, rows):
        if self._add_error is not None:
            raise self._add_error
        self.rows_added.extend(rows)


class _FakeDB:
    def __init__(self):
        self.tables = {}
        self.drop_calls = []
        self.create_calls = []

    def open_table(self, name):
        if name not in self.tables:
            raise RuntimeError("missing table")
        return self.tables[name]

    def create_table(self, name, sample_rows, mode="create"):
        self.create_calls.append((name, list(sample_rows), mode))
        table = _FakeTable()
        self.tables[name] = table
        return table

    def drop_table(self, name):
        self.drop_calls.append(name)
        self.tables.pop(name, None)


class _FakeEmbedder:
    def embed(self, texts, timeout_s):
        _ = (texts, timeout_s)
        return [[0.1, 0.2, 0.3]]


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class KBSearchFallbackTests(unittest.TestCase):
    def test_index_preserves_existing_table_when_add_requires_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "guide.md").write_text("# Guide\nhello world\n", encoding="utf-8")

            kb = KB.__new__(KB)
            kb._db_path = "/tmp/kb"
            kb._table_name = "chunks"
            kb._lock = _FakeLock()
            kb._embedder = _FakeEmbedder()
            kb._db = _FakeDB()
            kb._db.tables["chunks"] = _FakeTable(add_error=ValueError("schema mismatch"))

            with self.assertRaises(ValueError):
                kb.index([str(docs)], ["md"], 50, 0, 4096, 30)

            self.assertEqual([], kb._db.drop_calls)
            self.assertIn("chunks", kb._db.tables)

    def test_auto_search_falls_back_to_vector_when_hybrid_execution_fails(self):
        kb = KB.__new__(KB)
        kb._embedder = _FakeEmbedder()
        kb._lock = _FakeLock()
        table = _FakeTable()
        kb._get_table = lambda: table

        out = kb.search("RAG pipeline", 3, "auto", "", 30)

        self.assertEqual(1, len(out["hits"]))
        self.assertEqual("eval/ab/kb/rag_pipeline.md", out["hits"][0]["path"])
        self.assertEqual(("RAG pipeline", "hybrid"), table.calls[0])
        self.assertIsInstance(table.calls[1][0], list)
        self.assertIsNone(table.calls[1][1])

    def test_stable_doc_path_uses_repo_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            target = repo / "eval" / "ab" / "kb" / "rules.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Rules\n", encoding="utf-8")

            old_cwd = Path.cwd()
            try:
                os.chdir(repo)
                got = _stable_doc_path(target, [str(repo / "eval" / "ab" / "kb")])
            finally:
                os.chdir(old_cwd)

            self.assertEqual("eval/ab/kb/rules.md", got)

    def test_stable_doc_path_uses_common_root_when_cwd_is_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            target = repo / "docs" / "guide.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Guide\n", encoding="utf-8")

            old_cwd = Path.cwd()
            outside = Path(tmp) / "outside"
            outside.mkdir(parents=True, exist_ok=True)
            try:
                os.chdir(outside)
                got = _stable_doc_path(target, [str(repo / "docs"), str(repo / "eval" / "ab" / "kb")])
            finally:
                os.chdir(old_cwd)

            self.assertEqual("docs/guide.md", got)

    def test_stable_doc_path_preserves_root_prefix_for_single_root_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
            target = repo / "eval" / "ab" / "kb" / "rules.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Rules\n", encoding="utf-8")

            old_cwd = Path.cwd()
            outside = Path(tmp) / "outside"
            outside.mkdir(parents=True, exist_ok=True)
            try:
                os.chdir(outside)
                got = _stable_doc_path(target, [str(repo / "eval" / "ab" / "kb")])
            finally:
                os.chdir(old_cwd)

            self.assertEqual("eval/ab/kb/rules.md", got)

    def test_load_text_file_respects_max_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.txt"
            path.write_text("abcdefg", encoding="utf-8")
            got = _load_text_file(path, 4)
            self.assertEqual("abcd", got)


if __name__ == "__main__":
    unittest.main()
