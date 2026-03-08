import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class EvaluateTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.qrels = self.root / "qrels.jsonl"
        self.retrieval = self.root / "retrieval_predictions.jsonl"
        self.qa_gold = self.root / "qa_gold.jsonl"
        self.qa_pred = self.root / "qa_predictions.jsonl"
        self.coding = self.root / "coding_results.jsonl"
        self.out = self.root / "report.json"

        self._write_jsonl(
            self.qrels,
            [
                {"query_id": "q1", "relevant_ids": ["d1", "d2"]},
                {"query_id": "q2", "relevant_ids": ["d3"]},
            ],
        )
        self._write_jsonl(
            self.retrieval,
            [
                {"query_id": "q1", "retrieved_ids": ["d0", "d2", "d9"]},
                {"query_id": "q2", "retrieved_ids": ["d8", "d7", "d3"]},
            ],
        )
        self._write_jsonl(
            self.qa_gold,
            [
                {
                    "question_id": "qa1",
                    "answers": ["Go supports goroutines"],
                    "required_citations": ["d2", "d3"],
                },
                {
                    "question_id": "qa2",
                    "answers": ["LanceDB supports hybrid search"],
                    "required_citations": ["d1"],
                },
            ],
        )
        self._write_jsonl(
            self.qa_pred,
            [
                {
                    "question_id": "qa1",
                    "answer": "Go supports goroutines",
                    "citations": ["d2"],
                    "is_faithful": True,
                },
                {
                    "question_id": "qa2",
                    "answer": "LanceDB supports vector and keyword search",
                    "citations": ["d1"],
                    "is_faithful": True,
                },
            ],
        )
        self._write_jsonl(
            self.coding,
            [
                {"task_id": "t1", "pass": True, "latency_sec": 14.2, "cost_usd": 0.18},
                {"task_id": "t2", "pass": False, "latency_sec": 20.8, "cost_usd": 0.23},
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _write_jsonl(self, path, rows):
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_cli_generates_report(self):
        script = Path(__file__).resolve().parents[1] / "evaluate.py"
        cmd = [
            "python3",
            str(script),
            "--qrels",
            str(self.qrels),
            "--retrieval",
            str(self.retrieval),
            "--qa-gold",
            str(self.qa_gold),
            "--qa-pred",
            str(self.qa_pred),
            "--coding",
            str(self.coding),
            "--k",
            "3",
            "--out",
            str(self.out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(self.out.exists())

        report = json.loads(self.out.read_text(encoding="utf-8"))

        retrieval = report["retrieval"]
        self.assertAlmostEqual(retrieval["recall_at_k"], 0.75)
        self.assertAlmostEqual(retrieval["hit_rate_at_k"], 1.0)
        self.assertAlmostEqual(retrieval["mrr_at_k"], 0.4166666667, places=6)

        qa = report["qa"]
        self.assertAlmostEqual(qa["exact_match"], 0.5)
        self.assertAlmostEqual(qa["token_f1"], 0.8, places=6)
        self.assertAlmostEqual(qa["citation_recall"], 2.0 / 3.0, places=6)
        self.assertAlmostEqual(qa["faithfulness_rate"], 1.0)

        coding = report["coding"]
        self.assertAlmostEqual(coding["pass_rate"], 0.5)
        self.assertAlmostEqual(coding["avg_latency_sec"], 17.5)
        self.assertAlmostEqual(coding["avg_cost_usd"], 0.205)


if __name__ == "__main__":
    unittest.main()
