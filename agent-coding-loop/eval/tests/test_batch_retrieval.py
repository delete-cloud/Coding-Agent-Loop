import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eval import batch_retrieval


class BatchRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.qrels = self.root / "qrels.jsonl"
        self.out = self.root / "retrieval_predictions.jsonl"
        self.candidates = self.root / "retrieval_candidates.jsonl"
        with self.qrels.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"query_id": "q1", "query": "What is DBPath?", "relevant_ids": ["eval/ab/kb/config_validation.md:11:16"]}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_hit_to_chunk_id_uses_path_start_end(self):
        hit = {"path": "eval/ab/kb/config_validation.md", "start": 11, "end": 16}
        self.assertEqual(batch_retrieval.hit_to_chunk_id(hit), "eval/ab/kb/config_validation.md:11:16")

    def test_main_writes_predictions_and_candidates(self):
        hits = [
            {
                "path": "eval/ab/kb/config_validation.md",
                "start": 11,
                "end": 16,
                "heading": "DBPath",
                "score": 0.91,
                "text": "DBPath must be absolute and must not escape the repository root.",
            },
            {
                "path": "eval/ab/kb/testing_standards.md",
                "start": 6,
                "end": 9,
                "heading": "Coverage",
                "score": 0.73,
                "text": "Exported Go functions should have focused tests.",
            },
        ]
        with mock.patch.object(batch_retrieval, "search_kb", return_value=hits):
            rc = batch_retrieval.main([
                "--qrels",
                str(self.qrels),
                "--out",
                str(self.out),
                "--candidates-out",
                str(self.candidates),
                "--kb-url",
                "http://127.0.0.1:8788",
                "--top-k",
                "2",
            ])
        self.assertEqual(rc, 0)

        preds = [json.loads(line) for line in self.out.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(
            preds,
            [{"query_id": "q1", "retrieved_ids": ["eval/ab/kb/config_validation.md:11:16", "eval/ab/kb/testing_standards.md:6:9"]}],
        )

        candidate_rows = [json.loads(line) for line in self.candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(candidate_rows), 1)
        self.assertEqual(candidate_rows[0]["query_id"], "q1")
        self.assertEqual(candidate_rows[0]["query"], "What is DBPath?")
        self.assertEqual(candidate_rows[0]["hits"][0]["chunk_id"], "eval/ab/kb/config_validation.md:11:16")
        self.assertEqual(candidate_rows[0]["hits"][0]["path"], "eval/ab/kb/config_validation.md")
        self.assertEqual(candidate_rows[0]["hits"][0]["heading"], "DBPath")
        self.assertIn("DBPath must be absolute", candidate_rows[0]["hits"][0]["text_excerpt"])


if __name__ == "__main__":
    unittest.main()
