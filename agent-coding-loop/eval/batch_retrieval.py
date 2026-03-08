#!/usr/bin/env python3
"""Batch-call KB sidecar /search for all queries in qrels.jsonl and produce retrieval_predictions.jsonl.

Usage:
    python3 eval/batch_retrieval.py \
        --qrels eval/data/qrels.jsonl \
        --out eval/data/retrieval_predictions.jsonl \
        --kb-url http://127.0.0.1:8788 \
        --top-k 10 \
        [--evaluate --k 5]
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def search_kb(base_url: str, query: str, top_k: int) -> list[dict]:
    """Call KB sidecar /search and return hits."""
    url = f"{base_url.rstrip('/')}/search"
    payload = json.dumps({"query": query, "top_k": top_k}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("hits", [])
    except Exception as e:
        print(f"  ERROR searching for query: {e}", file=sys.stderr)
        return []


def hit_to_chunk_id(hit: dict) -> str:
    """Convert a search hit to a chunk ID in path:start:end format."""
    path = hit.get("path", "").strip()
    start = hit.get("start", 0)
    end = hit.get("end", 0)
    if not path:
        return ""
    return f"{path}:{start}:{end}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch retrieval evaluation against KB sidecar.")
    parser.add_argument("--qrels", required=True, help="Path to qrels.jsonl")
    parser.add_argument("--out", required=True, help="Output path for retrieval_predictions.jsonl")
    parser.add_argument("--kb-url", default="http://127.0.0.1:8788", help="KB sidecar base URL")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results to retrieve")
    parser.add_argument("--evaluate", action="store_true", help="Run evaluate.py after collecting predictions")
    parser.add_argument("--k", type=int, default=5, help="k for evaluation metrics (used with --evaluate)")
    args = parser.parse_args()

    qrels_path = Path(args.qrels)
    if not qrels_path.exists():
        print(f"qrels file not found: {qrels_path}", file=sys.stderr)
        return 1

    queries: list[dict] = []
    with qrels_path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            qid = row.get("query_id", "").strip()
            query = row.get("query", "").strip()
            if qid and query:
                queries.append({"query_id": qid, "query": query})

    if not queries:
        print("No queries found in qrels file.", file=sys.stderr)
        return 1

    print(f"Running {len(queries)} queries against {args.kb_url} (top_k={args.top_k})...")

    predictions: list[dict] = []
    for i, q in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {q['query_id']}: {q['query'][:80]}...")
        hits = search_kb(args.kb_url, q["query"], args.top_k)
        retrieved_ids = [cid for h in hits if (cid := hit_to_chunk_id(h))]
        predictions.append({"query_id": q["query_id"], "retrieved_ids": retrieved_ids})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")

    print(f"Wrote {len(predictions)} predictions to {out_path}")

    if args.evaluate:
        import subprocess
        eval_cmd = [
            sys.executable, "eval/evaluate.py",
            "--qrels", str(args.qrels),
            "--retrieval", str(args.out),
            "--k", str(args.k),
            "--out", "eval/reports/retrieval_report.json",
        ]
        print(f"Running evaluator: {' '.join(eval_cmd)}")
        result = subprocess.run(eval_cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
