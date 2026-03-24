"""Tests for eval/k8s/collect_results.py."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------
import sys

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.k8s.collect_results import (
    _build_task_index,
    _is_terminal,
    _latest_run_id,
    collect_one,
    main,
    parse_args,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_state_db(path: str, *, run_id: str, status: str, summary: str) -> None:
    """Create a minimal state.db sufficient for _latest_run_id tests.

    This is NOT the full production schema (which includes reviews, metadata,
    etc.).  Integration tests that exercise collect_one/main mock
    read_run_context to avoid needing the real schema.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            status TEXT,
            summary TEXT,
            created_at REAL,
            updated_at REAL
        )"""
    )
    conn.execute(
        "INSERT INTO runs (id, status, summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, status, summary, 1000000.0, 1060000.0),
    )
    conn.commit()
    conn.close()


def _minimal_task(
    task_id: str,
    *,
    requires_kb: bool = False,
    trap: bool = False,
    difficulty: str = "",
    expected_citations: list[str] | None = None,
) -> dict[str, Any]:
    t: dict[str, Any] = {"task_id": task_id}
    if requires_kb:
        t["requires_kb"] = True
    if trap:
        t["trap"] = True
    if difficulty:
        t["difficulty"] = difficulty
    if expected_citations:
        t["expected_citations"] = expected_citations
    return t


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestIsTerminal:
    def test_terminal_statuses(self):
        for s in ("completed", "failed", "needs_changes", "blocked"):
            assert _is_terminal(s) is True

    def test_non_terminal(self):
        for s in ("running", "pending", "", "unknown"):
            assert _is_terminal(s) is False

    def test_none_input(self):
        assert _is_terminal(None) is False


class TestBuildTaskIndex:
    def test_builds_index(self):
        tasks = [
            {"task_id": "alpha", "goal": "a"},
            {"task_id": "beta", "goal": "b"},
        ]
        idx = _build_task_index(tasks)
        assert set(idx.keys()) == {"alpha", "beta"}
        assert idx["alpha"]["goal"] == "a"

    def test_skips_empty_task_id(self):
        tasks = [{"task_id": ""}, {"goal": "no id"}]
        idx = _build_task_index(tasks)
        assert len(idx) == 0


class TestLatestRunId:
    def test_returns_latest(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _create_state_db(db_path, run_id="run-001", status="completed", summary="ok")
            assert _latest_run_id(db_path) == "run-001"
        finally:
            os.unlink(db_path)

    def test_missing_file(self):
        assert _latest_run_id("/nonexistent/path.db") == ""

    def test_empty_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE runs (id TEXT, status TEXT, summary TEXT, created_at REAL, updated_at REAL)"
            )
            conn.commit()
            conn.close()
            assert _latest_run_id(db_path) == ""
        finally:
            os.unlink(db_path)

    def test_corrupt_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False, mode="w") as f:
            f.write("not a sqlite database")
            db_path = f.name
        try:
            # Should not raise, returns ""
            assert _latest_run_id(db_path) == ""
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Unit tests: collect_one
# ---------------------------------------------------------------------------


class TestCollectOne:
    """Test collect_one with mocked run_ab functions."""

    def _mock_read_run_context(self, db_path, run_id):
        return (
            60.0,
            "corpus text",
            {
                "run_status": "completed",
                "run_summary": "task done",
                "meta_present": True,
                "fallback_used": False,
                "reviewer_used_fallback": False,
                "reviewer_decision": "approve",
                "citations": ["doc.md"],
                "kb_search_calls": 2,
                "repair_triggered": False,
                "repair_empty_patch": False,
                "repair_error": False,
                "repair_stage_count": 0,
                "failed_commands": [],
                "command_fail_count": 0,
            },
        )

    @mock.patch("eval.k8s.collect_results.read_run_context")
    @mock.patch("eval.k8s.collect_results._latest_run_id", return_value="run-001")
    def test_basic_collect(self, mock_run_id, mock_ctx):
        mock_ctx.side_effect = self._mock_read_run_context
        task = _minimal_task("task-alpha", requires_kb=True, expected_citations=["doc.md"])
        row = collect_one(
            task=task,
            db_path="/fake/state.db",
            experiment="rag",
            strict_mode=True,
            trial=1,
            trial_count=3,
        )
        assert row["task_id"] == "task-alpha"
        assert row["experiment"] == "rag"
        assert row["status"] == "completed"
        assert row["trial"] == 1
        assert row["trial_count"] == 3
        assert row["duration_sec"] == 60.0
        assert row["run_id"] == "run-001"
        assert row["kb_search_calls"] == 2

    @mock.patch("eval.k8s.collect_results.read_run_context")
    @mock.patch("eval.k8s.collect_results._latest_run_id", return_value="run-002")
    def test_difficulty_field_passthrough(self, mock_run_id, mock_ctx):
        mock_ctx.side_effect = self._mock_read_run_context
        task = _minimal_task("task-beta", difficulty="hard")
        row = collect_one(
            task=task,
            db_path="/fake/state.db",
            experiment="no_rag",
            strict_mode=False,
            trial=2,
            trial_count=3,
        )
        assert row["difficulty"] == "hard"

    @mock.patch("eval.k8s.collect_results.read_run_context")
    @mock.patch("eval.k8s.collect_results._latest_run_id", return_value="run-003")
    def test_no_difficulty_when_absent(self, mock_run_id, mock_ctx):
        mock_ctx.side_effect = self._mock_read_run_context
        task = _minimal_task("task-gamma")
        row = collect_one(
            task=task,
            db_path="/fake/state.db",
            experiment="rag",
            strict_mode=False,
            trial=1,
            trial_count=1,
        )
        assert "difficulty" not in row

    @mock.patch("eval.k8s.collect_results.read_run_context")
    @mock.patch("eval.k8s.collect_results._latest_run_id", return_value="run-004")
    def test_non_terminal_status_becomes_failed(self, mock_run_id, mock_ctx):
        mock_ctx.return_value = (
            30.0,
            "",
            {
                "run_status": "running",
                "run_summary": "",
                "meta_present": False,
                "fallback_used": False,
                "reviewer_used_fallback": False,
                "reviewer_decision": "",
                "citations": [],
                "kb_search_calls": 0,
                "repair_triggered": False,
                "repair_empty_patch": False,
                "repair_error": False,
                "repair_stage_count": 0,
                "failed_commands": [],
                "command_fail_count": 0,
            },
        )
        task = _minimal_task("task-stuck")
        row = collect_one(
            task=task,
            db_path="/fake/state.db",
            experiment="rag",
            strict_mode=False,
            trial=1,
            trial_count=1,
        )
        assert row["status"] == "failed"

    @mock.patch("eval.k8s.collect_results.read_run_context")
    @mock.patch("eval.k8s.collect_results._latest_run_id", return_value="run-005")
    def test_trap_field_passthrough(self, mock_run_id, mock_ctx):
        mock_ctx.side_effect = self._mock_read_run_context
        task = _minimal_task("task-trap", trap=True)
        row = collect_one(
            task=task,
            db_path="/fake/state.db",
            experiment="rag",
            strict_mode=True,
            trial=1,
            trial_count=1,
        )
        # Trap tasks should still produce a row
        assert row["task_id"] == "task-trap"
        assert row["strict_mode"] is True


# ---------------------------------------------------------------------------
# Integration tests: CLI (main)
# ---------------------------------------------------------------------------


def _mock_read_run_context(db_path, run_id):
    """Shared mock for read_run_context used by integration tests."""
    return (
        60.0,
        "corpus text",
        {
            "run_status": "completed",
            "run_summary": "task done",
            "meta_present": True,
            "fallback_used": False,
            "reviewer_used_fallback": False,
            "reviewer_decision": "approve",
            "citations": [],
            "kb_search_calls": 0,
            "repair_triggered": False,
            "repair_empty_patch": False,
            "repair_error": False,
            "repair_stage_count": 0,
            "failed_commands": [],
            "command_fail_count": 0,
        },
    )


class TestMain:
    """Test the main() CLI with real filesystem and mocked run_ab functions."""

    def _setup_results_dir(self, tmpdir: Path, tasks: list[dict[str, Any]]) -> Path:
        """Create results dir with state.db files for each task."""
        results_dir = tmpdir / "results"
        results_dir.mkdir()
        for task in tasks:
            tid = task["task_id"]
            task_dir = results_dir / tid
            task_dir.mkdir()
            _create_state_db(
                str(task_dir / "state.db"),
                run_id=f"run-{tid}",
                status="completed",
                summary=f"done {tid}",
            )
        return results_dir

    def _write_tasks_jsonl(self, tmpdir: Path, tasks: list[dict[str, Any]]) -> Path:
        tasks_file = tmpdir / "tasks.jsonl"
        with tasks_file.open("w") as f:
            for t in tasks:
                f.write(json.dumps(t) + "\n")
        return tasks_file

    @mock.patch("eval.k8s.collect_results.read_run_context", side_effect=_mock_read_run_context)
    @mock.patch("eval.k8s.collect_results.evaluate_strict_reasons", return_value=[])
    @mock.patch(
        "eval.k8s.collect_results.evaluate_expectations",
        return_value={
            "requires_kb": False,
            "expected_citation_count": 0,
            "found_citation_count": 0,
            "citation_recall": 0.0,
            "kb_signal": False,
        },
    )
    @mock.patch("eval.k8s.collect_results.normalize_citations", return_value=[])
    def test_full_pipeline(self, mock_cit, mock_eval, mock_strict, mock_ctx):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tasks = [_minimal_task("alpha"), _minimal_task("beta")]
            results_dir = self._setup_results_dir(tmpdir, tasks)
            tasks_file = self._write_tasks_jsonl(tmpdir, tasks)
            output_file = tmpdir / "out.jsonl"

            rc = main([
                "--results-dir", str(results_dir),
                "--tasks", str(tasks_file),
                "--experiment", "test_exp",
                "--output", str(output_file),
                "--trial", "1",
                "--trial-count", "1",
            ])

            assert rc == 0
            assert output_file.exists()
            rows = [json.loads(line) for line in output_file.read_text().strip().split("\n")]
            assert len(rows) == 2
            task_ids = {r["task_id"] for r in rows}
            assert task_ids == {"alpha", "beta"}

    def test_missing_results_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc = main([
                "--results-dir", os.path.join(tmpdir, "nonexistent"),
                "--tasks", os.path.join(tmpdir, "tasks.jsonl"),
                "--experiment", "test",
                "--output", os.path.join(tmpdir, "out.jsonl"),
            ])
            assert rc == 1

    @mock.patch("eval.k8s.collect_results.read_run_context", side_effect=_mock_read_run_context)
    @mock.patch("eval.k8s.collect_results.evaluate_strict_reasons", return_value=[])
    @mock.patch(
        "eval.k8s.collect_results.evaluate_expectations",
        return_value={
            "requires_kb": False,
            "expected_citation_count": 0,
            "found_citation_count": 0,
            "citation_recall": 0.0,
            "kb_signal": False,
        },
    )
    @mock.patch("eval.k8s.collect_results.normalize_citations", return_value=[])
    def test_skips_dir_without_state_db(self, mock_cit, mock_eval, mock_strict, mock_ctx):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tasks = [_minimal_task("alpha"), _minimal_task("beta")]
            results_dir = tmpdir / "results"
            results_dir.mkdir()

            # Only create state.db for alpha
            alpha_dir = results_dir / "alpha"
            alpha_dir.mkdir()
            _create_state_db(
                str(alpha_dir / "state.db"),
                run_id="run-alpha",
                status="completed",
                summary="done",
            )
            # beta dir exists but has no state.db
            (results_dir / "beta").mkdir()

            tasks_file = self._write_tasks_jsonl(tmpdir, tasks)
            output_file = tmpdir / "out.jsonl"

            rc = main([
                "--results-dir", str(results_dir),
                "--tasks", str(tasks_file),
                "--experiment", "test",
                "--output", str(output_file),
            ])

            assert rc == 0
            rows = [json.loads(line) for line in output_file.read_text().strip().split("\n")]
            assert len(rows) == 1
            assert rows[0]["task_id"] == "alpha"

    @mock.patch("eval.k8s.collect_results.read_run_context", side_effect=_mock_read_run_context)
    @mock.patch("eval.k8s.collect_results.evaluate_strict_reasons", return_value=[])
    @mock.patch(
        "eval.k8s.collect_results.evaluate_expectations",
        return_value={
            "requires_kb": False,
            "expected_citation_count": 0,
            "found_citation_count": 0,
            "citation_recall": 0.0,
            "kb_signal": False,
        },
    )
    @mock.patch("eval.k8s.collect_results.normalize_citations", return_value=[])
    def test_skips_unknown_task_id(self, mock_cit, mock_eval, mock_strict, mock_ctx):
        """Task dir exists in results but task_id not in tasks JSONL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tasks = [_minimal_task("alpha")]
            results_dir = tmpdir / "results"
            results_dir.mkdir()

            for tid in ("alpha", "unknown"):
                d = results_dir / tid
                d.mkdir()
                _create_state_db(str(d / "state.db"), run_id=f"run-{tid}", status="completed", summary="ok")

            tasks_file = self._write_tasks_jsonl(tmpdir, tasks)
            output_file = tmpdir / "out.jsonl"

            rc = main([
                "--results-dir", str(results_dir),
                "--tasks", str(tasks_file),
                "--experiment", "test",
                "--output", str(output_file),
            ])

            assert rc == 0
            rows = [json.loads(line) for line in output_file.read_text().strip().split("\n")]
            assert len(rows) == 1
            assert rows[0]["task_id"] == "alpha"


class TestParseArgs:
    def test_required_args(self):
        args = parse_args([
            "--results-dir", "/tmp/r",
            "--tasks", "tasks.jsonl",
            "--experiment", "rag",
            "--output", "out.jsonl",
        ])
        assert args.results_dir == "/tmp/r"
        assert args.experiment == "rag"
        assert args.trial == 1
        assert args.trial_count == 1
        assert args.strict_mode is False

    def test_optional_args(self):
        args = parse_args([
            "--results-dir", "/tmp/r",
            "--tasks", "tasks.jsonl",
            "--experiment", "rag",
            "--output", "out.jsonl",
            "--strict-mode",
            "--trial", "2",
            "--trial-count", "3",
        ])
        assert args.strict_mode is True
        assert args.trial == 2
        assert args.trial_count == 3
