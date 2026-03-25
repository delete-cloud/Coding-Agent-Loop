"""Tests for eval/k8s/summarize.py."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
import io

import sys

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.k8s.summarize import load_jsonl_rows, main, parse_args


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ROW_1: dict[str, Any] = {
    "experiment": "rag",
    "task_id": "add-retry-logic",
    "status": "completed",
    "duration_sec": 42.5,
    "run_id": "run-001",
    "summary": "Added retry logic.",
    "requires_kb": True,
    "kb_signal": True,
    "citation_recall": 1.0,
    "expected_citation_count": 2,
    "found_citation_count": 2,
    "strict_mode": True,
    "strict_reasons": [],
    "fallback_used": False,
    "structured_citations": ["docs/retry-policy.md"],
    "kb_search_calls": 3,
    "repair_triggered": False,
    "repair_empty_patch": False,
    "repair_error": False,
    "repair_stage_count": 0,
    "failed_commands": [],
    "command_fail_count": 0,
    "trial": 1,
    "trial_count": 1,
    "difficulty": "medium",
}

SAMPLE_ROW_2: dict[str, Any] = {
    "experiment": "rag",
    "task_id": "fix-db-migration",
    "status": "failed",
    "duration_sec": 120.0,
    "run_id": "run-002",
    "summary": "Migration failed.",
    "requires_kb": True,
    "kb_signal": False,
    "citation_recall": 0.0,
    "expected_citation_count": 1,
    "found_citation_count": 0,
    "strict_mode": True,
    "strict_reasons": ["missing_citation"],
    "fallback_used": True,
    "structured_citations": [],
    "kb_search_calls": 0,
    "repair_triggered": True,
    "repair_empty_patch": True,
    "repair_error": False,
    "repair_stage_count": 2,
    "failed_commands": ["go test ./migrations/..."],
    "command_fail_count": 1,
    "trial": 1,
    "trial_count": 1,
    "difficulty": "hard",
}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# Unit tests: load_jsonl_rows
# ---------------------------------------------------------------------------


class TestLoadJsonlRows(unittest.TestCase):
    def test_loads_valid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write(json.dumps(SAMPLE_ROW_1) + "\n")
            f.write(json.dumps(SAMPLE_ROW_2) + "\n")
            path = Path(f.name)
        try:
            rows = load_jsonl_rows(path)
            assert len(rows) == 2
            assert rows[0]["task_id"] == "add-retry-logic"
            assert rows[1]["task_id"] == "fix-db-migration"
        finally:
            path.unlink()

    def test_skips_blank_lines(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write(json.dumps(SAMPLE_ROW_1) + "\n")
            f.write("\n")
            f.write("   \n")
            f.write(json.dumps(SAMPLE_ROW_2) + "\n")
            path = Path(f.name)
        try:
            rows = load_jsonl_rows(path)
            assert len(rows) == 2
        finally:
            path.unlink()

    def test_skips_bad_json(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write(json.dumps(SAMPLE_ROW_1) + "\n")
            f.write("{bad json\n")
            f.write(json.dumps(SAMPLE_ROW_2) + "\n")
            path = Path(f.name)
        try:
            rows = load_jsonl_rows(path)
            assert len(rows) == 2  # skipped the bad line
        finally:
            path.unlink()

    def test_nonexistent_file(self):
        rows = load_jsonl_rows(Path("/nonexistent/file.jsonl"))
        assert rows == []

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write("")
            path = Path(f.name)
        try:
            rows = load_jsonl_rows(path)
            assert rows == []
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Unit tests: parse_args
# ---------------------------------------------------------------------------


class TestParseArgs(unittest.TestCase):
    def test_single_results_file(self):
        args = parse_args(["--results", "a.jsonl"])
        assert args.results == ["a.jsonl"]
        assert args.output is None
        assert args.strict_mode is False

    def test_multiple_results_files(self):
        args = parse_args(["--results", "a.jsonl", "b.jsonl", "c.jsonl"])
        assert args.results == ["a.jsonl", "b.jsonl", "c.jsonl"]

    def test_with_output_and_strict(self):
        args = parse_args(["--results", "a.jsonl", "--output", "report.md", "--strict-mode"])
        assert args.output == "report.md"
        assert args.strict_mode is True


# ---------------------------------------------------------------------------
# Integration tests: main
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    @mock.patch("eval.k8s.summarize.render_markdown", return_value="# Report\nDone.\n")
    @mock.patch(
        "eval.k8s.summarize.build_report",
        return_value={"meta": {}, "metrics": {}, "rows": []},
    )
    def test_single_jsonl_to_file(self, mock_build, mock_render):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            jsonl_path = tmpdir / "results.jsonl"
            _write_jsonl(jsonl_path, [SAMPLE_ROW_1, SAMPLE_ROW_2])
            output_path = tmpdir / "report.md"

            rc = main(["--results", str(jsonl_path), "--output", str(output_path)])

            assert rc == 0
            assert output_path.exists()
            content = output_path.read_text()
            assert "Report" in content

            # Verify build_report was called with 2 rows
            call_args = mock_build.call_args
            assert len(call_args.kwargs["rows"]) == 2
            assert call_args.kwargs["meta"]["source"] == "k8s"

    @mock.patch("eval.k8s.summarize.render_markdown", return_value="# Multi\n")
    @mock.patch(
        "eval.k8s.summarize.build_report",
        return_value={"meta": {}, "metrics": {}, "rows": []},
    )
    def test_multiple_jsonl_aggregation(self, mock_build, mock_render):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            f1 = tmpdir / "arm1.jsonl"
            f2 = tmpdir / "arm2.jsonl"
            _write_jsonl(f1, [SAMPLE_ROW_1])
            _write_jsonl(f2, [SAMPLE_ROW_2])
            output_path = tmpdir / "combined.md"

            rc = main([
                "--results", str(f1), str(f2),
                "--output", str(output_path),
            ])

            assert rc == 0
            call_args = mock_build.call_args
            assert len(call_args.kwargs["rows"]) == 2
            assert len(call_args.kwargs["meta"]["input_files"]) == 2

    @mock.patch("eval.k8s.summarize.render_markdown", return_value="# Strict\n")
    @mock.patch(
        "eval.k8s.summarize.build_report",
        return_value={"meta": {}, "metrics": {}, "rows": []},
    )
    def test_strict_mode_flag(self, mock_build, mock_render):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            jsonl_path = tmpdir / "results.jsonl"
            _write_jsonl(jsonl_path, [SAMPLE_ROW_1])
            output_path = tmpdir / "report.md"

            rc = main([
                "--results", str(jsonl_path),
                "--output", str(output_path),
                "--strict-mode",
            ])

            assert rc == 0
            meta = mock_build.call_args.kwargs["meta"]
            assert meta["strict_mode"] is True

    @mock.patch("eval.k8s.summarize.render_markdown", return_value="# Stdout\n")
    @mock.patch(
        "eval.k8s.summarize.build_report",
        return_value={"meta": {}, "metrics": {}, "rows": []},
    )
    def test_stdout_output(self, mock_build, mock_render):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            jsonl_path = tmpdir / "results.jsonl"
            _write_jsonl(jsonl_path, [SAMPLE_ROW_1])

            with mock.patch("sys.stdout", new_callable=io.StringIO) as fake_out:
                rc = main(["--results", str(jsonl_path)])

            assert rc == 0
            assert "Stdout" in fake_out.getvalue()

    @mock.patch("eval.k8s.summarize.render_markdown", return_value="")
    @mock.patch(
        "eval.k8s.summarize.build_report",
        return_value={"meta": {}, "metrics": {}, "rows": []},
    )
    def test_empty_jsonl_still_succeeds(self, mock_build, mock_render):
        """An empty input file produces 0 rows but doesn't fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            jsonl_path = tmpdir / "empty.jsonl"
            jsonl_path.write_text("")
            output_path = tmpdir / "report.md"

            rc = main(["--results", str(jsonl_path), "--output", str(output_path)])

            assert rc == 0
            assert mock_build.call_args.kwargs["meta"]["row_count"] == 0
