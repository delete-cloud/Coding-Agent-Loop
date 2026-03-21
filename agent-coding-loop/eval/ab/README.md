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

`ab_report.json` now includes `paired_analysis`, which joins `no_rag` and `rag` rows by `task_id`. Paired outcomes use the final row status after strict-mode normalization: `completed` counts as pass, while `failed`, `needs_changes`, and `blocked` count as fail. Blank `task_id` rows are excluded before pairing and counted separately from task-level integrity issues. Task-level exclusions use a single precedence order (`duplicate_pair` before `missing_pair` before `non_terminal`) so the integrity counters remain stable. If one experiment arm is absent or exclusions leave zero valid pairs, `paired_analysis` remains present but is marked `available=false` with a machine-readable reason instead of being conflated with `no_discordant_pairs`.

`ab_report.md` now renders the same paired-analysis section for humans, including integrity counters and an explicit `Paired analysis unavailable: <reason>` note for `--only`, `--dry-run`, or otherwise unpaired outputs.

## 4) Dry-run sanity check

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/minimal_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state.db \
  --dry-run
```

## 5) Targeted Repair Eval

Use `eval/ab/repair_targeted_tasks.jsonl` when you want to validate repair behavior itself instead of headline benchmark pass rate. The targeted file is built from real benchmark tasks that previously showed one of these patterns: `patch applied + command failed`, repair improved status, or repair regressed via `empty_patch`.

Run the same taskset twice with only `--repair-mode` changed.
Use `--only rag` because this targeted file mixes KB-required and repo-only tasks. The repo-only tasks remain valid in the `rag` arm because their goals already forbid `kb_search`, while running the `no_rag` arm would make the KB-required tasks fail for retrieval-control reasons unrelated to repair.
Use `--plan-mode off` so the experiment isolates repair behavior instead of adding the planner as a second variable.

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/repair_targeted_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state-repair-off.db \
  --output-dir eval/reports/repair-targeted-off \
  --max-iterations 2 \
  --plan-mode off \
  --repair-mode off \
  --only rag \
  --strict-mode
```

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/repair_targeted_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state-repair-on.db \
  --output-dir eval/reports/repair-targeted-on \
  --max-iterations 2 \
  --plan-mode off \
  --repair-mode on \
  --only rag \
  --strict-mode
```

Inspect these report fields first:

- `pass_rate`
- `repair_trigger_count`
- `repair_empty_patch_count`
- per-task `status`
- per-task `repair_triggered`
- per-task `command_fail_count`

Ignore `paired_analysis` for this workflow.
Inside each single-arm report it is expected to be unavailable, because the repair comparison is across the two output directories (`repair-targeted-off` vs `repair-targeted-on`), not inside one `ab_report.json`.

Interpretation guide:

- If repair triggers and usually produces non-empty patches but task status still does not improve, the repair prompt or constraints need work.
- If repair rarely triggers even on this targeted taskset, the gate is still too narrow.
- If repair triggers and recovers tasks here, the next step is not to redesign repair but to revisit the trigger gate or benchmark setup.

## Note

`kb_signal_rate` and `citation_recall_avg` prefer structured `coder_meta` / `reviewer_meta` tool records from `state.db`.
Text matching is kept only as backward-compatible fallback for old runs.
