#!/usr/bin/env python3
"""Collect eval results from K8S Job state.db files and produce JSONL for build_report().

Usage:
    python3 eval/k8s/collect_results.py \
        --results-dir ./results/ \
        --tasks eval/ab/benchmark_tasks.jsonl \
        --experiment rag \
        --output results.jsonl \
        [--strict-mode] \
        [--trial 1] [--trial-count 1]

The --results-dir directory should point at one experiment/trial partition and
contain subdirectories named by task_id, each containing a state.db file from
the completed K8S Job.

    results/
      rag/
        trial-1/
          task-alpha/
            state.db
          task-beta/
            state.db
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so that ``eval.ab.run_ab`` is
# importable regardless of the working directory.
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.ab.run_ab import (  # noqa: E402
    evaluate_expectations,
    evaluate_strict_reasons,
    load_jsonl,
    normalize_citations,
    read_run_context,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_run_id(db_path: str) -> str:
    """Return the run_id of the most recent run in *db_path*, or ``""``."""
    if not os.path.exists(db_path):
        return ""
    conn = sqlite3.connect(db_path)
    conn.text_factory = lambda b: b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row["id"]).strip() if row else ""
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return ""
    finally:
        conn.close()


def _is_terminal(status: str) -> bool:
    return str(status or "").strip() in {"completed", "failed", "needs_changes", "blocked"}


def _build_task_index(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map task_id -> task dict for fast lookup."""
    idx: dict[str, dict[str, Any]] = {}
    for t in tasks:
        tid = str(t.get("task_id", "")).strip()
        if tid:
            idx[tid] = t
    return idx


# ---------------------------------------------------------------------------
# Core: collect one result
# ---------------------------------------------------------------------------

def collect_one(
    *,
    task: dict[str, Any],
    db_path: str,
    experiment: str,
    strict_mode: bool,
    trial: int,
    trial_count: int,
) -> dict[str, Any]:
    """Extract a single result row from *db_path* for the given *task*."""

    task_id = str(task.get("task_id", "")).strip()
    trap = bool(task.get("trap", False))
    difficulty = str(task.get("difficulty", "")) or ""

    run_id = _latest_run_id(db_path)
    duration, corpus, trace = read_run_context(db_path=db_path, run_id=run_id)

    db_status = str(trace.get("run_status", "")).strip()
    status = db_status if _is_terminal(db_status) else "failed"
    summary = str(trace.get("run_summary", "")).strip()

    checks = evaluate_expectations(task, corpus_text=corpus + "\n" + summary, trace=trace)
    strict_reasons = evaluate_strict_reasons(
        strict_mode=strict_mode,
        status=status,
        checks=checks,
        summary_text=summary,
        corpus_text=corpus,
        trace=trace,
        trap=trap,
    )
    if strict_reasons and status == "completed":
        status = "failed"

    row: dict[str, Any] = {
        "experiment": experiment,
        "task_id": task_id,
        "status": status,
        "duration_sec": duration,
        "run_id": run_id,
        "summary": summary,
        "requires_kb": checks["requires_kb"],
        "kb_signal": checks["kb_signal"],
        "citation_recall": checks["citation_recall"],
        "expected_citation_count": checks["expected_citation_count"],
        "found_citation_count": checks["found_citation_count"],
        "strict_mode": strict_mode,
        "strict_reasons": strict_reasons,
        "fallback_used": bool(trace.get("fallback_used", False)),
        "structured_citations": normalize_citations(list(trace.get("citations", []))),
        "kb_search_calls": int(trace.get("kb_search_calls", 0) or 0),
        "repair_triggered": bool(trace.get("repair_triggered", False)),
        "repair_empty_patch": bool(trace.get("repair_empty_patch", False)),
        "repair_error": bool(trace.get("repair_error", False)),
        "repair_stage_count": int(trace.get("repair_stage_count", 0) or 0),
        "failed_commands": list(trace.get("failed_commands", [])),
        "command_fail_count": int(trace.get("command_fail_count", 0) or 0),
        "trial": trial,
        "trial_count": trial_count,
    }

    # Optional fields -- present only when the task defines them.
    if difficulty:
        row["difficulty"] = difficulty

    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Collect K8S eval results from state.db files into JSONL.",
    )
    p.add_argument(
        "--results-dir",
        required=True,
        help="Directory for one experiment/trial partition, containing task_id subdirs with state.db",
    )
    p.add_argument(
        "--tasks",
        required=True,
        help="Benchmark tasks JSONL file",
    )
    p.add_argument(
        "--experiment",
        required=True,
        help="Experiment arm name (e.g. 'rag', 'no_rag')",
    )
    p.add_argument(
        "--output",
        required=True,
        help="Output JSONL path",
    )
    p.add_argument(
        "--strict-mode",
        action="store_true",
        help="Enable strict evaluation",
    )
    p.add_argument(
        "--trial",
        type=int,
        default=1,
        help="Trial number (default: 1)",
    )
    p.add_argument(
        "--trial-count",
        type=int,
        default=1,
        help="Total trial count (default: 1)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args(argv)

    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        log.error("--results-dir %s is not a directory", results_dir)
        return 1

    tasks = load_jsonl(args.tasks)
    task_index = _build_task_index(tasks)
    if not task_index:
        log.error("no tasks loaded from %s", args.tasks)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    collected = 0
    skipped = 0

    for entry in sorted(results_dir.iterdir()):
        if not entry.is_dir():
            continue
        task_id = entry.name
        db_path = entry / "state.db"
        if not db_path.exists():
            log.warning("skip %s: no state.db found", task_id)
            skipped += 1
            continue
        task = task_index.get(task_id)
        if task is None:
            log.warning("skip %s: not found in tasks file", task_id)
            skipped += 1
            continue

        try:
            row = collect_one(
                task=task,
                db_path=str(db_path),
                experiment=args.experiment,
                strict_mode=bool(args.strict_mode),
                trial=args.trial,
                trial_count=args.trial_count,
            )
            rows.append(row)
            collected += 1
            log.info("collected %s status=%s", task_id, row["status"])
        except Exception:
            log.exception("error collecting %s", task_id)
            skipped += 1

    with output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("wrote %d rows to %s (skipped %d)", collected, output_path, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
