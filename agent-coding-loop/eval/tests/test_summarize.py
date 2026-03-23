"""Tests for eval/k8s/summarize.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.k8s.summarize import main  # noqa: E402


def _make_row(**overrides: object) -> dict:
    """Return a minimal valid result row, with optional overrides."""
    base = {
        "experiment": "rag",
        "task_id": "t1",
        "status": "completed",
        "duration_sec": 10.0,
        "run_id": "r1",
        "summary": "ok",
        "requires_kb": True,
        "kb_signal": True,
        "citation_recall": 1.0,
        "expected_citation_count": 1,
        "found_citation_count": 1,
        "strict_mode": True,
        "strict_reasons": [],
        "fallback_used": False,
        "structured_citations": ["docs/a.md"],
        "kb_search_calls": 1,
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
    base.update(overrides)
    return base


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class TestSummarizeSingleFile:
    def test_produces_markdown(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "results.jsonl"
        _write_jsonl(jsonl, [_make_row(), _make_row(task_id="t2", run_id="r2")])

        out = tmp_path / "report.md"
        rc = main(["--results", str(jsonl), "--output", str(out)])

        assert rc == 0
        md = out.read_text(encoding="utf-8")
        assert "# A/B Report" in md
        assert len(md) > 100  # sanity: non-trivial content


class TestSummarizeMultipleFiles:
    def test_concatenates_rows(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        f2 = tmp_path / "b.jsonl"
        _write_jsonl(f1, [_make_row(task_id="t1", experiment="rag")])
        _write_jsonl(f2, [_make_row(task_id="t2", experiment="no_rag")])

        out = tmp_path / "report.md"
        rc = main(["--results", str(f1), str(f2), "--output", str(out)])

        assert rc == 0
        md = out.read_text(encoding="utf-8")
        # Both experiment arms should appear in the metrics table.
        assert "rag" in md
        assert "no_rag" in md
        assert len(md) > 100


class TestSummarizeEmptyFile:
    def test_handles_empty_gracefully(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")

        out = tmp_path / "report.md"
        rc = main(["--results", str(empty), "--output", str(out)])

        assert rc == 0
        md = out.read_text(encoding="utf-8")
        # Should still produce a valid report header, even with zero data.
        assert "# A/B Report" in md


class TestSummarizeMissingFile:
    def test_missing_file_produces_empty_report(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.jsonl"
        out = tmp_path / "report.md"
        rc = main(["--results", str(missing), "--output", str(out)])

        assert rc == 0
        md = out.read_text(encoding="utf-8")
        assert "# A/B Report" in md


class TestSummarizeBadJson:
    def test_skips_bad_lines(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "mixed.jsonl"
        good = _make_row(task_id="good1")
        jsonl.write_text(
            json.dumps(good) + "\n"
            "NOT VALID JSON\n"
            + json.dumps(_make_row(task_id="good2", run_id="r2")) + "\n",
            encoding="utf-8",
        )

        out = tmp_path / "report.md"
        rc = main(["--results", str(jsonl), "--output", str(out)])

        assert rc == 0
        md = out.read_text(encoding="utf-8")
        assert "# A/B Report" in md
        assert len(md) > 100


class TestSummarizeOutputFile:
    def test_writes_to_file(self, tmp_path: Path) -> None:
        jsonl = tmp_path / "results.jsonl"
        _write_jsonl(jsonl, [_make_row()])

        out = tmp_path / "sub" / "report.md"
        rc = main(["--results", str(jsonl), "--output", str(out)])

        assert rc == 0
        assert out.exists()
        assert out.read_text(encoding="utf-8").startswith("#")

    def test_stdout_when_no_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        jsonl = tmp_path / "results.jsonl"
        _write_jsonl(jsonl, [_make_row()])

        rc = main(["--results", str(jsonl)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "# A/B Report" in captured.out
