#!/usr/bin/env python3
"""Validate retrieval eval inputs before computing metrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CHUNK_ID_RE = re.compile(r"^.+:\d+:\d+$")


class EvalValidationError(ValueError):
    """Raised when eval inputs are inconsistent or malformed."""


def load_jsonl(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


def infer_data_tier(path: str | None) -> str | None:
    if not path:
        return None
    parts = Path(path).parts
    if "sample" in parts:
        return "sample"
    if "bench" in parts:
        return "bench"
    return None


def _validate_chunk_ids(ids: list[Any], label: str) -> None:
    for raw in ids:
        chunk_id = str(raw).strip()
        if not CHUNK_ID_RE.match(chunk_id):
            raise EvalValidationError(f"{label} contains invalid chunk id '{chunk_id}'; expected path:start:end")


def _validate_unique_ids(ids: list[str], label: str) -> None:
    dupes = sorted({qid for qid in ids if ids.count(qid) > 1})
    if dupes:
        raise EvalValidationError(f"{label} contains duplicate query_id values: {', '.join(dupes)}")


def _validate_qrels(rows: list[dict[str, Any]], path: str) -> set[str]:
    query_ids: list[str] = []
    for index, row in enumerate(rows, start=1):
        qid = str(row.get("query_id", "")).strip()
        if not qid:
            raise EvalValidationError(f"{path}: qrels row {index} missing query_id")
        query = str(row.get("query", "")).strip()
        if not query:
            raise EvalValidationError(f"{path}: qrels row {index} missing query")
        relevant_ids = row.get("relevant_ids", [])
        if not isinstance(relevant_ids, list):
            raise EvalValidationError(f"{path}: qrels row {index} relevant_ids must be a list")
        _validate_chunk_ids(relevant_ids, f"{path}: qrels row {index} relevant_ids")
        query_ids.append(qid)
    _validate_unique_ids(query_ids, f"{path}: qrels")
    return set(query_ids)


def _validate_retrieval_predictions(rows: list[dict[str, Any]], path: str) -> set[str]:
    query_ids: list[str] = []
    for index, row in enumerate(rows, start=1):
        qid = str(row.get("query_id", "")).strip()
        if not qid:
            raise EvalValidationError(f"{path}: retrieval row {index} missing query_id")
        retrieved_ids = row.get("retrieved_ids", [])
        if not isinstance(retrieved_ids, list):
            raise EvalValidationError(f"{path}: retrieval row {index} retrieved_ids must be a list")
        _validate_chunk_ids(retrieved_ids, f"{path}: retrieval row {index} retrieved_ids")
        query_ids.append(qid)
    _validate_unique_ids(query_ids, f"{path}: retrieval")
    return set(query_ids)


def _validate_tiers(paths: dict[str, str | None]) -> None:
    tiers = {name: tier for name, path in paths.items() if (tier := infer_data_tier(path))}
    distinct = sorted(set(tiers.values()))
    if len(distinct) > 1:
        details = ", ".join(f"{name}={tier}" for name, tier in sorted(tiers.items()))
        raise EvalValidationError(
            f"sample and bench data must not be mixed in one evaluation run ({details})"
        )


def validate_eval_inputs(
    *,
    qrels: str | None = None,
    retrieval: str | None = None,
    qa_gold: str | None = None,
    qa_pred: str | None = None,
    coding: str | None = None,
) -> None:
    _validate_tiers(
        {
            "qrels": qrels,
            "retrieval": retrieval,
            "qa_gold": qa_gold,
            "qa_pred": qa_pred,
            "coding": coding,
        }
    )

    qrel_ids: set[str] = set()
    pred_ids: set[str] = set()

    if qrels:
        qrel_ids = _validate_qrels(load_jsonl(qrels), qrels)
    if retrieval:
        pred_ids = _validate_retrieval_predictions(load_jsonl(retrieval), retrieval)

    if qrels and retrieval and qrel_ids != pred_ids:
        missing = sorted(qrel_ids - pred_ids)
        unexpected = sorted(pred_ids - qrel_ids)
        details: list[str] = []
        if missing:
            details.append(f"missing predictions for query_id: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected predictions for query_id: {', '.join(unexpected)}")
        raise EvalValidationError("retrieval query_id mismatch: " + "; ".join(details))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate eval JSONL inputs before scoring.")
    parser.add_argument("--qrels", help="Path to retrieval qrels JSONL")
    parser.add_argument("--retrieval", help="Path to retrieval predictions JSONL")
    parser.add_argument("--qa-gold", help="Path to QA gold JSONL")
    parser.add_argument("--qa-pred", help="Path to QA predictions JSONL")
    parser.add_argument("--coding", help="Path to coding results JSONL")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
