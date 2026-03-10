import json
import tempfile
import unittest
from pathlib import Path

from eval.validate_eval_inputs import EvalValidationError, validate_eval_inputs


class ValidateEvalInputsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_jsonl(self, path: Path, rows: list[dict]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_rejects_qrels_without_query(self):
        qrels = self.root / "qrels.jsonl"
        retrieval = self.root / "retrieval_predictions.jsonl"
        self._write_jsonl(qrels, [{"query_id": "q1", "relevant_ids": ["docs/a.md:1:2"]}])
        self._write_jsonl(retrieval, [{"query_id": "q1", "retrieved_ids": ["docs/a.md:1:2"]}])

        with self.assertRaises(EvalValidationError):
            validate_eval_inputs(qrels=str(qrels), retrieval=str(retrieval))

    def test_rejects_query_id_mismatch(self):
        qrels = self.root / "qrels.jsonl"
        retrieval = self.root / "retrieval_predictions.jsonl"
        self._write_jsonl(qrels, [{"query_id": "q1", "query": "demo", "relevant_ids": ["docs/a.md:1:2"]}])
        self._write_jsonl(retrieval, [{"query_id": "q2", "retrieved_ids": ["docs/a.md:1:2"]}])

        with self.assertRaises(EvalValidationError):
            validate_eval_inputs(qrels=str(qrels), retrieval=str(retrieval))

    def test_rejects_invalid_chunk_id_format(self):
        qrels = self.root / "qrels.jsonl"
        retrieval = self.root / "retrieval_predictions.jsonl"
        self._write_jsonl(qrels, [{"query_id": "q1", "query": "demo", "relevant_ids": ["docs/a.md:1:2"]}])
        self._write_jsonl(retrieval, [{"query_id": "q1", "retrieved_ids": ["docs-a-md"]}])

        with self.assertRaises(EvalValidationError):
            validate_eval_inputs(qrels=str(qrels), retrieval=str(retrieval))

    def test_rejects_mixed_sample_and_bench_paths(self):
        sample_qrels = self.root / "eval" / "data" / "sample" / "qrels.jsonl"
        bench_retrieval = self.root / "eval" / "data" / "bench" / "retrieval_predictions.jsonl"
        self._write_jsonl(sample_qrels, [{"query_id": "q1", "query": "demo", "relevant_ids": ["docs/a.md:1:2"]}])
        self._write_jsonl(bench_retrieval, [{"query_id": "q1", "retrieved_ids": ["docs/a.md:1:2"]}])

        with self.assertRaises(EvalValidationError):
            validate_eval_inputs(qrels=str(sample_qrels), retrieval=str(bench_retrieval))


if __name__ == "__main__":
    unittest.main()
