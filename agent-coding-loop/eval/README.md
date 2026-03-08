# Eval Template for RAG + Embedding + Agent

This folder gives you a simple, interview-friendly evaluation harness for:

- Retrieval quality (`Recall@k`, `HitRate@k`, `MRR@k`)
- RAG answer quality (`Exact Match`, `Token F1`, `Citation Recall`, `Faithfulness Rate`)
- Coding-agent outcomes (`Pass Rate`, average latency, average cost)

## Data format

All inputs are JSONL.

- `data/qrels.jsonl`
  - `{"query_id":"q1","relevant_ids":["doc_a","doc_b"]}`
- `data/retrieval_predictions.jsonl`
  - `{"query_id":"q1","retrieved_ids":["doc_x","doc_a","doc_y"]}`
- `data/qa_gold.jsonl`
  - `{"question_id":"qa1","answers":["gold answer"],"required_citations":["doc_a"]}`
- `data/qa_predictions.jsonl`
  - `{"question_id":"qa1","answer":"model answer","citations":["doc_a"],"is_faithful":true}`
- `data/coding_results.jsonl`
  - `{"task_id":"t1","pass":true,"latency_sec":15.2,"cost_usd":0.19}`

## Run

```bash
python3 eval/evaluate.py \
  --qrels eval/data/qrels.jsonl \
  --retrieval eval/data/retrieval_predictions.jsonl \
  --qa-gold eval/data/qa_gold.jsonl \
  --qa-pred eval/data/qa_predictions.jsonl \
  --coding eval/data/coding_results.jsonl \
  --k 5 \
  --out eval/reports/report.json
```

## Test

```bash
python3 -m unittest discover -s eval/tests -p 'test_*.py'
```

## Suggested interview A/B setup

Compare 3 runs with same question/task set:

1. `No-RAG` (baseline)
2. `Vector-RAG`
3. `Hybrid-RAG + Rerank`

Then report metric deltas and latency/cost trade-offs from `report.json`.
