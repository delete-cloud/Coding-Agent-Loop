#!/usr/bin/env python3
"""Summarize K8S eval results from JSONL into a markdown report.

Usage:
    python3 eval/k8s/summarize.py \
        --results results.jsonl \
        [--output report.md] \
        [--strict-mode]

Reads one or more JSONL files produced by collect_results.py, feeds them
into build_report() / render_markdown(), and writes the final markdown
report to stdout or the specified file.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
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

from eval.ab.run_ab import build_report, render_markdown  # noqa: E402

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Load rows from a JSONL file, skipping blank lines."""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        log.warning("file does not exist, skipping: %s", path)
        return rows
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                log.warning("skip bad JSON at %s:%d: %s", path, lineno, exc)
    if not rows:
        log.warning("no rows loaded from %s", path)
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Summarize K8S eval JSONL results into a markdown report.",
    )
    p.add_argument(
        "--results",
        nargs="+",
        required=True,
        help="One or more JSONL result files to summarize",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Output markdown file (default: stdout)",
    )
    p.add_argument(
        "--strict-mode",
        action="store_true",
        help="Flag strict mode in report metadata",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args(argv)

    # Collect rows from all input files.
    rows: list[dict[str, Any]] = []
    input_files: list[str] = []
    for path_str in args.results:
        p = Path(path_str)
        input_files.append(str(p.resolve()))
        rows.extend(load_jsonl_rows(p))

    log.info("loaded %d rows from %d file(s)", len(rows), len(input_files))

    meta: dict[str, Any] = {
        "source": "k8s",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_files": input_files,
        "strict_mode": bool(args.strict_mode),
        "row_count": len(rows),
    }

    report = build_report(meta=meta, rows=rows)
    md = render_markdown(report)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        log.info("wrote report to %s", out)
    else:
        sys.stdout.write(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
