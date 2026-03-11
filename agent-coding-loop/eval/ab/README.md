# Minimal A/B Taskset (No-RAG vs RAG)

This folder provides a minimal 6-task benchmark for the Eino coding loop.

Task mix:

- 2 `KB-only` tasks
- 2 `KB-guided` tasks
- 2 `Repo-only` tasks

## 1) Prepare KB sidecar (for RAG arm)

```bash
cd "$(git rev-parse --show-toplevel)"
pip install -r kb/requirements-local-embedding.txt
export KB_EMBEDDING_PROVIDER="local"
export KB_LOCAL_EMBED_MODEL="Qwen/Qwen3-Embedding-0.6B"
# 国内/网络受限建议开启：export KB_EMBEDDING_SOURCE="modelscope"
python3 kb/server.py --listen 127.0.0.1:8788
```

In another terminal, index docs including this folder:

```bash
curl -sS -X POST http://127.0.0.1:8788/index \
  -H 'Content-Type: application/json' \
  -d '{
    "roots": [
      "docs",
      "eval/ab/kb"
    ],
    "exts": ["md"],
    "chunk_size": 900,
    "overlap": 120
  }'
```

## 2) Run A/B

```bash
cd "$(git rev-parse --show-toplevel)"
python3 eval/ab/run_ab.py \
  --tasks eval/ab/minimal_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state.db \
  --kb-url http://127.0.0.1:8788 \
  --output-dir eval/reports/ab \
  --max-iterations 2
```

No-RAG arm is run by forcing `AGENT_LOOP_KB_URL=http://127.0.0.1:0`.
RAG arm uses `--kb-url`.

### Strict mode (recommended for interview metrics)

Use strict mode to avoid false positives from fallback approvals:

- Forbid fallback reviewer approve as a pass.
- Require at least one expected citation hit for KB-required tasks.
- Require structured `coder_meta` / `reviewer_meta` records in `state.db` for completed runs.

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/minimal_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state.db \
  --kb-url http://127.0.0.1:8788 \
  --output-dir eval/reports/ab-strict \
  --max-iterations 2 \
  --task-timeout-sec 420 \
  --strict-mode
```

## 3) Outputs

- `eval/reports/ab/ab_raw_runs.jsonl`
- `eval/reports/ab/ab_report.json`
- `eval/reports/ab/ab_report.md`

## 4) Dry-run sanity check

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/minimal_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state.db \
  --dry-run
```

## Note

`kb_signal_rate` and `citation_recall_avg` prefer structured `coder_meta` / `reviewer_meta` tool records from `state.db`.
Text matching is kept only as backward-compatible fallback for old runs.
