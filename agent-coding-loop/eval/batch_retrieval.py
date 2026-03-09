#!/usr/bin/env python3
"""Batch-call KB sidecar /search for all queries in qrels.jsonl.

Usage:
    python3 eval/batch_retrieval.py \
        --qrels eval/data/bench/qrels.jsonl \
        --out eval/data/bench/retrieval_predictions.jsonl \
        --candidates-out eval/data/bench/retrieval_candidates.jsonl \
        --kb-url http://127.0.0.1:8788 \
        --top-k 10 \
        [--evaluate --k 5]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

try:
    from eval.validate_eval_inputs import EvalValidationError, load_jsonl, validate_eval_inputs
except ModuleNotFoundError:  # pragma: no cover - script entrypoint fallback
    from validate_eval_inputs import EvalValidationError, load_jsonl, validate_eval_inputs


def search_kb(base_url: str, query: str, top_k: int) -> list[dict[str, Any]]:
    """Call KB sidecar /search and return hits."""
    url = f"{base_url.rstrip('/')}/search"
    payload = json.dumps({"query": query, "top_k": top_k}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("hits", [])
    except Exception as exc:
        print(f"  ERROR searching for query: {exc}", file=sys.stderr)
        return []


def hit_to_chunk_id(hit: dict[str, Any]) -> str:
    """Convert a search hit to a chunk ID in path:start:end format."""
    path = str(hit.get("path", "")).strip()
    start = hit.get("start", 0)
    end = hit.get("end", 0)
    if not path:
        return ""
    return f"{path}:{start}:{end}"


def compact_text_excerpt(text: str, limit: int = 280) -> str:
    raw = " ".join(str(text).split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3].rstrip() + "..."


def build_candidate_row(query_id: str, query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        chunk_id = hit_to_chunk_id(hit)
        if not chunk_id:
            continue
        items.append(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "path": str(hit.get("path", "")).strip(),
                "start": hit.get("start", 0),
                "end": hit.get("end", 0),
                "heading": str(hit.get("heading", "")).strip(),
                "score": hit.get("score"),
                "text_excerpt": compact_text_excerpt(str(hit.get("text", ""))),
            }
        )
    return {"query_id": query_id, "query": query, "hits": items}


def load_queries_from_qrels(path: Path) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    for row in load_jsonl(str(path)):
        qid = str(row.get("query_id", "")).strip()
        query = str(row.get("query", "")).strip()
        if qid and query:
            queries.append({"query_id": qid, "query": query})
    return queries


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch retrieval evaluation against KB sidecar.")
    parser.add_argument("--qrels", required=True, help="Path to qrels.jsonl")
    parser.add_argument("--out", required=True, help="Output path for retrieval_predictions.jsonl")
    parser.add_argument("--candidates-out", help="Optional output path for retrieval candidate hits JSONL")
    parser.add_argument("--kb-url", default="http://127.0.0.1:8788", help="KB sidecar base URL")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results to retrieve")
    parser.add_argument("--evaluate", action="store_true", help="Run evaluate.py after collecting predictions")
    parser.add_argument("--k", type=int, default=5, help="k for evaluation metrics (used with --evaluate)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    qrels_path = Path(args.qrels)
    if not qrels_path.exists():
        print(f"qrels file not found: {qrels_path}", file=sys.stderr)
        return 1

    try:
        validate_eval_inputs(qrels=str(qrels_path))
    except EvalValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    queries = load_queries_from_qrels(qrels_path)
    if not queries:
        print("No queries found in qrels file.", file=sys.stderr)
        return 1

    print(f"Running {len(queries)} queries against {args.kb_url} (top_k={args.top_k})...")

    predictions: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for index, item in enumerate(queries, start=1):
        print(f"  [{index}/{len(queries)}] {item['query_id']}: {item['query'][:80]}...")
        hits = search_kb(args.kb_url, item["query"], args.top_k)
        retrieved_ids = [chunk_id for hit in hits if (chunk_id := hit_to_chunk_id(hit))]
        predictions.append({"query_id": item["query_id"], "retrieved_ids": retrieved_ids})
        if args.candidates_out:
            candidate_rows.append(build_candidate_row(item["query_id"], item["query"], hits))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")
    print(f"Wrote {len(predictions)} predictions to {out_path}")

    if args.candidates_out:
        candidates_path = Path(args.candidates_out)
        candidates_path.parent.mkdir(parents=True, exist_ok=True)
        with candidates_path.open("w", encoding="utf-8") as f:
            for row in candidate_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"Wrote {len(candidate_rows)} candidate rows to {candidates_path}")

    if args.evaluate:
        eval_cmd = [
            sys.executable,
            "eval/evaluate.py",
            "--qrels",
            str(args.qrels),
            "--retrieval",
            str(args.out),
            "--k",
            str(args.k),
            "--out",
            "eval/reports/retrieval_report.json",
        ]
        print(f"Running evaluator: {' '.join(eval_cmd)}")
        result = subprocess.run(eval_cmd, capture_output=True, text=True, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
