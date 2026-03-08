import tempfile
import unittest
from pathlib import Path

from eval.ab.run_ab import (
    aggregate_metrics,
    build_goal,
    collect_overlay_paths,
    evaluate_expectations,
    extract_goal_target_files,
    retrieval_mode_for_task,
    should_copy_overlay_path,
)


class RunABTests(unittest.TestCase):
    def test_build_goal_modes(self):
        base = "修复 config 校验"
        rag_goal = build_goal(base, rag_enabled=True, requires_kb=True)
        self.assertIn("必须先调用 kb_search", rag_goal)

        no_rag_goal = build_goal(base, rag_enabled=False, requires_kb=True)
        self.assertIn("禁止调用 kb_search", no_rag_goal)

        rag_repo_goal = build_goal(base, rag_enabled=True, requires_kb=False)
        self.assertIn("禁止调用 kb_search", rag_repo_goal)

    def test_expectations(self):
        task = {
            "task_id": "kb_qa_001",
            "requires_kb": True,
            "expected_citations": ["kb/config_validation.md", "kb/rag_pipeline.md"],
        }
        texts = "used kb_search and cited kb/config_validation.md"
        out = evaluate_expectations(task, texts)
        self.assertTrue(out["kb_signal"])
        self.assertAlmostEqual(out["citation_recall"], 0.5)

    def test_expectations_prefer_structured_citations(self):
        task = {
            "task_id": "kb_qa_001",
            "requires_kb": True,
            "expected_citations": ["eval/ab/kb/rag_pipeline.md"],
        }
        out = evaluate_expectations(
            task,
            corpus_text="",
            trace={
                "meta_present": True,
                "citations": ["eval/ab/kb/rag_pipeline.md"],
                "kb_search_calls": 1,
            },
        )
        self.assertTrue(out["kb_signal"])
        self.assertAlmostEqual(out["citation_recall"], 1.0)

    def test_aggregate_metrics(self):
        rows = [
            {
                "experiment": "rag",
                "status": "completed",
                "duration_sec": 10.0,
                "requires_kb": True,
                "kb_signal": True,
                "citation_recall": 1.0,
            },
            {
                "experiment": "rag",
                "status": "failed",
                "duration_sec": 20.0,
                "requires_kb": True,
                "kb_signal": False,
                "citation_recall": 0.0,
            },
            {
                "experiment": "rag",
                "status": "completed",
                "duration_sec": 30.0,
                "requires_kb": False,
                "kb_signal": False,
                "citation_recall": 0.0,
            },
        ]
        got = aggregate_metrics(rows)
        rag = got["rag"]
        self.assertEqual(rag["total_tasks"], 3)
        self.assertAlmostEqual(rag["pass_rate"], 2.0 / 3.0)
        self.assertAlmostEqual(rag["avg_duration_sec"], 20.0)
        self.assertAlmostEqual(rag["kb_signal_rate"], 0.5)
        self.assertAlmostEqual(rag["citation_recall_avg"], 0.5)

    def test_extract_goal_target_files(self):
        goal = "更新 docs/eino-agent-loop.md，并补充 README.md，同时忽略 pkg/xxx.go。"
        got = extract_goal_target_files(goal)
        self.assertIn("docs/eino-agent-loop.md", got)
        self.assertIn("README.md", got)
        self.assertNotIn("pkg/xxx.go", got)

    def test_collect_overlay_paths(self):
        task = {
            "requires_kb": True,
            "goal": "在 docs/eino-agent-loop.md 新增段落。",
            "expected_citations": ["eval/ab/kb/rag_pipeline.md", "eval/ab/kb/rag_pipeline.md"],
        }
        got = collect_overlay_paths(task)
        self.assertIn("docs/eino-agent-loop.md", got)
        self.assertIn("eval/ab/kb/rag_pipeline.md", got)
        self.assertIn("eval/ab/kb", got)

    def test_collect_overlay_paths_repo_only_without_goal_targets(self):
        task = {
            "requires_kb": False,
            "goal": "修改 internal/loop/processor.go 并更新 README.md",
            "expected_citations": [],
        }
        got = collect_overlay_paths(task, include_goal_targets=False)
        self.assertEqual([], got)

    def test_should_copy_overlay_path_skips_existing_goal_target(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            target = repo / "internal" / "config" / "config.go"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("package config\n", encoding="utf-8")

            self.assertFalse(
                should_copy_overlay_path(
                    str(repo),
                    "internal/config/config.go",
                    {"internal/config/config.go"},
                )
            )

    def test_should_copy_overlay_path_copies_missing_goal_target(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            self.assertTrue(
                should_copy_overlay_path(
                    str(repo),
                    "docs/eino-agent-loop.md",
                    {"docs/eino-agent-loop.md"},
                )
            )

    def test_retrieval_mode_for_task(self):
        self.assertEqual("prefetch", retrieval_mode_for_task(rag_enabled=True, requires_kb=True))
        self.assertEqual("off", retrieval_mode_for_task(rag_enabled=True, requires_kb=False))
        self.assertEqual("off", retrieval_mode_for_task(rag_enabled=False, requires_kb=True))


if __name__ == "__main__":
    unittest.main()
