# Eval Harness for Retrieval, RAG QA, and Coding Agents

This folder provides a small evaluation harness for three layers:

- Retrieval quality (`Recall@k`, `HitRate@k`, `MRR@k`)
- RAG answer quality (`Exact Match`, `Token F1`, `Citation Recall`, `Faithfulness Rate`)
- Coding-agent outcomes (`Pass Rate`, average latency, average cost)

## Data layout

`eval/data/` is intentionally split into two tiers.

- `eval/data/sample/`
  - Small fixtures for README examples, local smoke tests, and evaluator demos.
  - Safe to use when you want to verify script behavior without running the KB sidecar.
- `eval/data/bench/`
  - Real retrieval benchmark inputs.
  - `qrels.jsonl` here should only contain `relevant_ids` copied from live sidecar hits.
  - `retrieval_predictions.jsonl` and `retrieval_candidates.jsonl` should be generated from the current KB sidecar, not hand-written.

Do not mix `sample/` and `bench/` files in the same `evaluate.py` invocation. The validator will reject mixed runs.

## JSONL formats

### Retrieval qrels

```json
{"query_id":"q01","query":"What validation rules apply to DBPath in config?","relevant_ids":["eval/ab/kb/config_validation.md:11:16"]}
```

Rules:

- `query_id` is required and must be unique.
- `query` is required.
- `relevant_ids` must use `path:start:end` chunk IDs.
- For benchmark data, `relevant_ids` must come from live sidecar hits, not manual chunk-boundary estimates.

### Retrieval predictions

```json
{"query_id":"q01","retrieved_ids":["eval/ab/kb/config_validation.md:11:16","eval/ab/kb/testing_standards.md:6:9"]}
```

Rules:

- `query_id` must match the qrels set exactly.
- `retrieved_ids` must use `path:start:end` chunk IDs.

### Retrieval candidate export

```json
{"query_id":"q01","query":"What validation rules apply to DBPath in config?","hits":[{"rank":1,"chunk_id":"eval/ab/kb/config_validation.md:11:16","path":"eval/ab/kb/config_validation.md","start":11,"end":16,"heading":"DBPath","score":0.91,"text_excerpt":"DBPath must be absolute ..."}]}
```

Use this file for manual calibration. Review the live candidate hits, choose the relevant `chunk_id` values, then copy only those IDs into `bench/qrels.jsonl`.

Do not copy the current top-k output straight back into gold labels without manual review.

## Quick demo with sample data

```bash
python3 eval/validate_eval_inputs.py \
  --qrels eval/data/sample/qrels.jsonl \
  --retrieval eval/data/sample/retrieval_predictions.jsonl \
  --qa-gold eval/data/sample/qa_gold.jsonl \
  --qa-pred eval/data/sample/qa_predictions.jsonl \
  --coding eval/data/sample/coding_results.jsonl

python3 eval/evaluate.py \
  --qrels eval/data/sample/qrels.jsonl \
  --retrieval eval/data/sample/retrieval_predictions.jsonl \
  --qa-gold eval/data/sample/qa_gold.jsonl \
  --qa-pred eval/data/sample/qa_predictions.jsonl \
  --coding eval/data/sample/coding_results.jsonl \
  --k 5 \
  --out eval/reports/sample_report.json
```

## Recommended benchmark workflow

1. Start the KB sidecar against the target corpus.
2. Export live candidate hits for every benchmark query.
3. Manually calibrate `bench/qrels.jsonl` by selecting relevant `chunk_id` values from the candidate export.
4. Generate live `bench/retrieval_predictions.jsonl`.
5. Run `evaluate.py` on benchmark data only.

### Benchmark KB isolation

For benchmark runs, index only `eval/ab/kb` into a dedicated LanceDB path.

- Do not mix benchmark KB docs with project documentation under `docs/`.
- Do not reuse an already-populated local LanceDB that was built for exploratory repo QA.
- Use a benchmark-only DB path such as `/tmp/kb_lancedb_bench_<date>` so retrieval results stay reproducible across reruns.

Example benchmark-only indexing request:

```bash
curl -X POST http://127.0.0.1:8788/index \
  -H 'Content-Type: application/json' \
  --data @- <<'JSON'
{
  "roots": ["eval/ab/kb"],
  "exts": ["md"],
  "chunk_size": 900,
  "overlap": 120
}
JSON
```

### 1. Validate the benchmark qrels

```bash
python3 eval/validate_eval_inputs.py \
  --qrels eval/data/bench/qrels.jsonl
```

### 2. Export live candidate hits and retrieval predictions

```bash
python3 eval/batch_retrieval.py \
  --qrels eval/data/bench/qrels.jsonl \
  --out eval/data/bench/retrieval_predictions.jsonl \
  --candidates-out eval/data/bench/retrieval_candidates.jsonl \
  --kb-url http://127.0.0.1:8788 \
  --top-k 10
```

### 3. Run retrieval evaluation on benchmark data

```bash
python3 eval/validate_eval_inputs.py \
  --qrels eval/data/bench/qrels.jsonl \
  --retrieval eval/data/bench/retrieval_predictions.jsonl

python3 eval/evaluate.py \
  --qrels eval/data/bench/qrels.jsonl \
  --retrieval eval/data/bench/retrieval_predictions.jsonl \
  --k 5 \
  --out eval/reports/bench_retrieval_report.json
```

## Tests

```bash
python3 -m unittest discover -s eval/tests -p 'test_*.py'
```
