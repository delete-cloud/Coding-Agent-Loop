import unittest

from kb.server import KB


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
    def __init__(self):
        self.calls = []

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


if __name__ == "__main__":
    unittest.main()
