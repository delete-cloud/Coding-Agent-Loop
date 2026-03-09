#!/usr/bin/env python3
"""Minimal evaluation runner for Agentic RAG interview demos.

This script scores three layers:
1) Retrieval: Recall@k, HitRate@k, MRR@k
2) QA: Exact Match, Token F1, Citation Recall, Faithfulness Rate
3) Coding tasks: Pass Rate, Avg Latency, Avg Cost
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from eval.validate_eval_inputs import EvalValidationError, load_jsonl, validate_eval_inputs
except ModuleNotFoundError:  # pragma: no cover - script entrypoint fallback
    from validate_eval_inputs import EvalValidationError, load_jsonl, validate_eval_inputs


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    norm = normalize_text(text)
    return norm.split() if norm else []


def exact_match_score(pred: str, golds: list[str]) -> float:
    pred_n = normalize_text(pred)
    if not golds:
        return 0.0
    for gold in golds:
        if pred_n == normalize_text(gold):
            return 1.0
    return 0.0


def token_f1_single(pred: str, gold: str) -> float:
    pred_tokens = tokenize(pred)
    gold_tokens = tokenize(gold)
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0

    common = Counter(pred_tokens) & Counter(gold_tokens)
    overlap = float(sum(common.values()))
    if overlap <= 0:
        return 0.0
    precision = overlap / float(len(pred_tokens))
    recall = overlap / float(len(gold_tokens))
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def token_f1_score(pred: str, golds: list[str]) -> float:
    if not golds:
        return 0.0
    return max(token_f1_single(pred, g) for g in golds)


def eval_retrieval(qrels_rows: list[dict[str, Any]], pred_rows: list[dict[str, Any]], k: int) -> dict[str, Any]:
    qrels: dict[str, set[str]] = {}
    for row in qrels_rows:
        qid = str(row.get("query_id", "")).strip()
        if not qid:
            continue
        rel = {str(x) for x in row.get("relevant_ids", []) if str(x).strip()}
        qrels[qid] = rel

    preds: dict[str, list[str]] = {}
    for row in pred_rows:
        qid = str(row.get("query_id", "")).strip()
        if not qid:
            continue
        preds[qid] = [str(x) for x in row.get("retrieved_ids", []) if str(x).strip()]

    recalls: list[float] = []
    hits: list[float] = []
    mrrs: list[float] = []

    for qid, rel in qrels.items():
        retrieved = preds.get(qid, [])[:k]
        if not rel:
            recalls.append(1.0)
            hits.append(1.0)
            mrrs.append(1.0)
            continue

        retrieved_set = set(retrieved)
        overlap = len(rel & retrieved_set)
        recalls.append(float(overlap) / float(len(rel)))
        hits.append(1.0 if overlap > 0 else 0.0)

        rr = 0.0
        for idx, doc_id in enumerate(retrieved, start=1):
            if doc_id in rel:
                rr = 1.0 / float(idx)
                break
        mrrs.append(rr)

    return {
        "queries": len(qrels),
        "k": k,
        "recall_at_k": mean(recalls),
        "hit_rate_at_k": mean(hits),
        "mrr_at_k": mean(mrrs),
    }


def eval_qa(gold_rows: list[dict[str, Any]], pred_rows: list[dict[str, Any]]) -> dict[str, Any]:
    gold: dict[str, dict[str, Any]] = {}
    for row in gold_rows:
        qid = str(row.get("question_id", "")).strip()
        if qid:
            gold[qid] = row

    pred: dict[str, dict[str, Any]] = {}
    for row in pred_rows:
        qid = str(row.get("question_id", "")).strip()
        if qid:
            pred[qid] = row

    em_scores: list[float] = []
    f1_scores: list[float] = []
    citation_hits = 0
    citation_total = 0
    faithful: list[float] = []

    for qid, g in gold.items():
        answers = [str(x) for x in g.get("answers", []) if str(x).strip()]
        required_citations = {str(x) for x in g.get("required_citations", []) if str(x).strip()}

        p = pred.get(qid, {})
        answer = str(p.get("answer", ""))
        citations = {str(x) for x in p.get("citations", []) if str(x).strip()}

        em_scores.append(exact_match_score(answer, answers))
        f1_scores.append(token_f1_score(answer, answers))

        if required_citations:
            citation_hits += len(required_citations & citations)
            citation_total += len(required_citations)

        flag = p.get("is_faithful", None)
        if isinstance(flag, bool):
            faithful.append(1.0 if flag else 0.0)

    citation_recall = 0.0
    if citation_total > 0:
        citation_recall = float(citation_hits) / float(citation_total)

    return {
        "questions": len(gold),
        "exact_match": mean(em_scores),
        "token_f1": mean(f1_scores),
        "citation_recall": citation_recall,
        "faithfulness_rate": mean(faithful),
    }


def eval_coding(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "tasks": 0,
            "pass_rate": 0.0,
            "avg_latency_sec": 0.0,
            "avg_cost_usd": 0.0,
        }

    passes = 0
    latencies: list[float] = []
    costs: list[float] = []

    for row in rows:
        if bool(row.get("pass", False)):
            passes += 1
        if isinstance(row.get("latency_sec"), (int, float)):
            latencies.append(float(row["latency_sec"]))
        if isinstance(row.get("cost_usd"), (int, float)):
            costs.append(float(row["cost_usd"]))

    return {
        "tasks": len(rows),
        "pass_rate": float(passes) / float(len(rows)),
        "avg_latency_sec": mean(latencies),
        "avg_cost_usd": mean(costs),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate RAG + Agent metrics from JSONL inputs.")
    p.add_argument("--qrels", help="Path to retrieval ground-truth JSONL")
    p.add_argument("--retrieval", help="Path to retrieval prediction JSONL")
    p.add_argument("--qa-gold", help="Path to QA gold JSONL")
    p.add_argument("--qa-pred", help="Path to QA prediction JSONL")
    p.add_argument("--coding", help="Path to coding results JSONL")
    p.add_argument("--k", type=int, default=5, help="Top-k used for retrieval metrics")
    p.add_argument("--out", required=True, help="Output report path (JSON)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    k = max(1, int(args.k))

    try:
        validate_eval_inputs(
            qrels=args.qrels,
            retrieval=args.retrieval,
            qa_gold=args.qa_gold,
            qa_pred=args.qa_pred,
            coding=args.coding,
        )
    except EvalValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    retrieval = eval_retrieval(
        qrels_rows=load_jsonl(args.qrels),
        pred_rows=load_jsonl(args.retrieval),
        k=k,
    )
    qa = eval_qa(
        gold_rows=load_jsonl(args.qa_gold),
        pred_rows=load_jsonl(args.qa_pred),
    )
    coding = eval_coding(load_jsonl(args.coding))

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "k": k,
        },
        "retrieval": retrieval,
        "qa": qa,
        "coding": coding,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
