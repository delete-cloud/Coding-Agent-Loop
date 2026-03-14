import json
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest import mock
import tempfile
import unittest
from pathlib import Path

from eval.ab.run_ab import (
    aggregate_metrics,
    build_paired_analysis,
    build_goal,
    collect_overlay_paths,
    evaluate_expectations,
    extract_goal_target_files,
    retrieval_mode_for_task,
    read_run_context,
    run_one,
    should_copy_overlay_path,
    status_to_pair_outcome,
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

    def test_status_to_pair_outcome_maps_terminal_statuses(self):
        self.assertEqual("pass", status_to_pair_outcome("completed"))
        self.assertEqual("fail", status_to_pair_outcome("failed"))
        self.assertEqual("fail", status_to_pair_outcome("needs_changes"))
        self.assertEqual("fail", status_to_pair_outcome("blocked"))
        self.assertIsNone(status_to_pair_outcome("dry_run"))

    def test_build_paired_analysis_excludes_missing_duplicate_and_non_terminal_pairs(self):
        rows = [
            {"experiment": "no_rag", "task_id": "t1", "status": "completed"},
            {"experiment": "rag", "task_id": "t1", "status": "failed"},
            {"experiment": "no_rag", "task_id": "t2", "status": "completed"},
            {"experiment": "no_rag", "task_id": "t3", "status": "failed"},
            {"experiment": "rag", "task_id": "t3", "status": "completed"},
            {"experiment": "rag", "task_id": "t3", "status": "failed"},
            {"experiment": "no_rag", "task_id": "t4", "status": "dry_run"},
            {"experiment": "rag", "task_id": "t4", "status": "completed"},
        ]

        paired = build_paired_analysis(rows)

        self.assertEqual(1, paired["integrity"]["valid_pair_count"])
        self.assertEqual(1, paired["integrity"]["excluded_missing_pair_count"])
        self.assertEqual(1, paired["integrity"]["excluded_duplicate_pair_count"])
        self.assertEqual(1, paired["integrity"]["excluded_non_terminal_count"])
        self.assertEqual(["t1"], [x["task_id"] for x in paired["pairs"]])

    def test_build_paired_analysis_excludes_invalid_task_id_rows_before_grouping(self):
        rows = [
            {"experiment": "no_rag", "task_id": "", "status": "completed"},
            {"experiment": "rag", "task_id": "   ", "status": "failed"},
        ]

        paired = build_paired_analysis(rows)

        self.assertEqual(2, paired["integrity"]["excluded_invalid_task_id_count"])
        self.assertEqual(
            [
                {"row_index": 0, "experiment": "no_rag", "task_id": "", "status": "completed"},
                {"row_index": 1, "experiment": "rag", "task_id": "   ", "status": "failed"},
            ],
            paired["integrity"]["excluded_invalid_task_id_rows"],
        )
        self.assertEqual([], paired["pairs"])

    def test_build_paired_analysis_uses_single_bucket_exclusion_precedence(self):
        rows = [
            {"experiment": "no_rag", "task_id": "t5", "status": "completed"},
            {"experiment": "no_rag", "task_id": "t5", "status": "failed"},
            {"experiment": "rag", "task_id": "t5", "status": "dry_run"},
        ]

        paired = build_paired_analysis(rows)

        self.assertEqual(1, paired["integrity"]["excluded_duplicate_pair_count"])
        self.assertEqual(0, paired["integrity"]["excluded_missing_pair_count"])
        self.assertEqual(0, paired["integrity"]["excluded_non_terminal_count"])

    def test_build_paired_analysis_reports_exact_mcnemar_result(self):
        rows = [
            {"experiment": "no_rag", "task_id": "t1", "status": "failed"},
            {"experiment": "rag", "task_id": "t1", "status": "completed"},
            {"experiment": "no_rag", "task_id": "t2", "status": "failed"},
            {"experiment": "rag", "task_id": "t2", "status": "completed"},
            {"experiment": "no_rag", "task_id": "t3", "status": "failed"},
            {"experiment": "rag", "task_id": "t3", "status": "completed"},
        ]

        paired = build_paired_analysis(rows)

        self.assertEqual(0, paired["counts"]["baseline_only_pass"])
        self.assertEqual(3, paired["counts"]["candidate_only_pass"])
        self.assertTrue(paired["significance"]["applied"])
        self.assertEqual("exact_mcnemar", paired["significance"]["test"])
        self.assertAlmostEqual(0.25, paired["significance"]["p_value"])

    def test_build_paired_analysis_skips_significance_without_discordant_pairs(self):
        rows = [
            {"experiment": "no_rag", "task_id": "t1", "status": "completed"},
            {"experiment": "rag", "task_id": "t1", "status": "completed"},
            {"experiment": "no_rag", "task_id": "t2", "status": "failed"},
            {"experiment": "rag", "task_id": "t2", "status": "failed"},
        ]

        paired = build_paired_analysis(rows)

        self.assertFalse(paired["significance"]["applied"])
        self.assertEqual("no_discordant_pairs", paired["significance"]["reason"])

    def test_build_paired_analysis_marks_missing_experiment_arm_as_unavailable(self):
        rows = [
            {"experiment": "rag", "task_id": "t1", "status": "completed"},
        ]

        paired = build_paired_analysis(rows)

        self.assertFalse(paired["available"])
        self.assertEqual("missing_experiment_arm", paired["reason"])

    def test_build_paired_analysis_marks_no_valid_pairs_as_unavailable(self):
        rows = [
            {"experiment": "no_rag", "task_id": "t1", "status": "dry_run"},
            {"experiment": "rag", "task_id": "t1", "status": "dry_run"},
        ]

        paired = build_paired_analysis(rows)

        self.assertFalse(paired["available"])
        self.assertEqual("no_valid_pairs", paired["reason"])

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

    def test_read_run_context_tolerates_invalid_utf8_output_text(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table runs (id text primary key, summary text, created_at integer, updated_at integer);
                create table reviews (run_id text, summary text, findings_json text);
                create table tool_calls (run_id text, tool text, input_text text, output_text text, status text);
                """
            )
            conn.execute(
                "insert into runs(id, summary, created_at, updated_at) values (?, ?, ?, ?)",
                ("r1", "summary", 0, 1000),
            )
            conn.execute(
                "insert into tool_calls(run_id, tool, input_text, output_text, status) values (?, ?, ?, CAST(X'5b315d20ff' AS TEXT), ?)",
                ("r1", "kb_search", "", "completed"),
            )
            conn.commit()
            conn.close()

            duration, corpus, trace = read_run_context(str(db_path), "r1")

            self.assertAlmostEqual(duration, 1.0)
            self.assertIn("summary", corpus)
            self.assertIn("kb_search", corpus)
            self.assertEqual(trace["kb_search_calls"], 1)

    def test_read_run_context_counts_retrieval_preflight_as_real_kb_search(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table runs (id text primary key, summary text, created_at integer, updated_at integer);
                create table reviews (run_id text, summary text, findings_json text);
                create table tool_calls (run_id text, tool text, input_text text, output_text text, status text);
                """
            )
            conn.execute(
                "insert into runs(id, summary, created_at, updated_at) values (?, ?, ?, ?)",
                ("r1", "summary", 0, 1000),
            )
            conn.execute(
                "insert into tool_calls(run_id, tool, input_text, output_text, status) values (?, ?, ?, ?, ?)",
                ("r1", "retrieval_preflight", "", "[]", "completed"),
            )
            conn.commit()
            conn.close()

            _, _, trace = read_run_context(str(db_path), "r1")

            self.assertEqual(trace["kb_search_calls"], 1)

    @mock.patch("eval.ab.run_ab.subprocess.run")
    def test_run_one_sets_agent_loop_db_path_env(self, mock_run):
        mock_run.return_value = SimpleNamespace(stdout='{"run_id":"","status":"completed","summary":"ok"}', stderr='', returncode=0)

        row = run_one(
            experiment="rag",
            rag_enabled=True,
            task={"task_id": "repo_only_001", "goal": "fix", "requires_kb": False},
            agent_loop_bin="./agent-loop",
            repo="/tmp/repo",
            db_path="/tmp/state.db",
            pr_mode="dry-run",
            max_iterations=1,
            kb_url="http://127.0.0.1:8788",
            dry_run=False,
            task_timeout_sec=60,
            strict_mode=False,
            isolate_worktree=False,
        )

        self.assertEqual(row["status"], "completed")
        self.assertEqual(mock_run.call_args.kwargs["env"]["AGENT_LOOP_DB_PATH"], "/tmp/state.db")

    @mock.patch("eval.ab.run_ab.subprocess.run")
    def test_run_one_recovers_run_id_from_db_on_timeout_without_stdout_json(self, mock_run):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table runs (id text primary key, spec_json text, status text, branch text, commit_hash text, pr_url text, summary text, created_at integer, updated_at integer);
                create table reviews (run_id text, summary text, findings_json text);
                create table tool_calls (run_id text, tool text, input_text text, output_text text, status text);
                """
            )
            spec = '{"goal":' + json.dumps(build_goal("fix readme", rag_enabled=True, requires_kb=False)) + ',"repo":"/tmp/repo"}'
            conn.execute(
                "insert into runs(id, spec_json, status, branch, commit_hash, pr_url, summary, created_at, updated_at) values (?, ?, ?, '', '', '', ?, ?, ?)",
                ("run_123", spec, "running", "run started", 1000, 4000),
            )
            conn.execute(
                "insert into tool_calls(run_id, tool, input_text, output_text, status) values (?, ?, ?, ?, ?)",
                ("run_123", "coder_meta", "", '{"citations":[],"used_fallback":false}', "completed"),
            )
            conn.commit()
            conn.close()

            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["agent-loop"], timeout=1, output="", stderr="")

            row = run_one(
                experiment="rag",
                rag_enabled=True,
                task={"task_id": "repo_only_003", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin="./agent-loop",
                repo="/tmp/repo",
                db_path=str(db_path),
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:8788",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=False,
            )

            self.assertEqual(row["run_id"], "run_123")
            self.assertEqual(row["duration_sec"], 3.0)

    @mock.patch("eval.ab.run_ab.subprocess.run")
    def test_run_one_recovers_run_id_from_db_when_process_returns_without_result_json(self, mock_run):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table runs (id text primary key, spec_json text, status text, branch text, commit_hash text, pr_url text, summary text, created_at integer, updated_at integer);
                create table reviews (run_id text, summary text, findings_json text);
                create table tool_calls (run_id text, tool text, input_text text, output_text text, status text);
                """
            )
            spec = '{"goal":' + json.dumps(build_goal("fix readme", rag_enabled=False, requires_kb=False)) + ',"repo":"/tmp/repo"}'
            conn.execute(
                "insert into runs(id, spec_json, status, branch, commit_hash, pr_url, summary, created_at, updated_at) values (?, ?, ?, '', '', '', ?, ?, ?)",
                ("run_456", spec, "completed", "done", 1000, 5000),
            )
            conn.commit()
            conn.close()

            mock_run.return_value = SimpleNamespace(stdout='', stderr='', returncode=0)

            row = run_one(
                experiment="no_rag",
                rag_enabled=False,
                task={"task_id": "repo_only_003", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin="./agent-loop",
                repo="/tmp/repo",
                db_path=str(db_path),
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:8788",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=False,
            )

            self.assertEqual(row["run_id"], "run_456")
            self.assertEqual(row["duration_sec"], 4.0)
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["summary"], "done")

    @mock.patch("eval.ab.run_ab.subprocess.run")
    def test_run_one_timeout_prefers_terminal_db_state(self, mock_run):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table runs (id text primary key, spec_json text, status text, branch text, commit_hash text, pr_url text, summary text, created_at integer, updated_at integer);
                create table reviews (run_id text, summary text, findings_json text);
                create table tool_calls (run_id text, tool text, input_text text, output_text text, status text);
                """
            )
            spec = '{"goal":' + json.dumps(build_goal("fix readme", rag_enabled=True, requires_kb=False)) + ',"repo":"/tmp/repo"}'
            conn.execute(
                "insert into runs(id, spec_json, status, branch, commit_hash, pr_url, summary, created_at, updated_at) values (?, ?, ?, '', '', '', ?, ?, ?)",
                ("run_789", spec, "needs_changes", "review requested changes", 1000, 7000),
            )
            conn.execute(
                "insert into tool_calls(run_id, tool, input_text, output_text, status) values (?, ?, ?, ?, ?)",
                ("run_789", "reviewer_meta", "", '{"decision":"request_changes","used_fallback":false}', "completed"),
            )
            conn.commit()
            conn.close()

            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["agent-loop"], timeout=1, output="", stderr="")

            row = run_one(
                experiment="rag",
                rag_enabled=True,
                task={"task_id": "repo_only_003", "goal": "fix readme", "requires_kb": False},
                agent_loop_bin="./agent-loop",
                repo="/tmp/repo",
                db_path=str(db_path),
                pr_mode="dry-run",
                max_iterations=1,
                kb_url="http://127.0.0.1:8788",
                dry_run=False,
                task_timeout_sec=60,
                strict_mode=False,
                isolate_worktree=False,
            )

            self.assertEqual(row["run_id"], "run_789")
            self.assertEqual(row["status"], "needs_changes")
            self.assertEqual(row["summary"], "review requested changes")
            self.assertTrue(row["timed_out"])
            self.assertEqual(row["duration_sec"], 6.0)

    def test_retrieval_mode_for_task(self):
        self.assertEqual("prefetch", retrieval_mode_for_task(rag_enabled=True, requires_kb=True))
        self.assertEqual("off", retrieval_mode_for_task(rag_enabled=True, requires_kb=False))
        self.assertEqual("off", retrieval_mode_for_task(rag_enabled=False, requires_kb=True))


if __name__ == "__main__":
    unittest.main()
