# Targeted Repair Eval Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a focused repair-evaluation workflow that answers one narrow question cleanly: when repair is actually triggered on real repair-eligible tasks, does it generate a non-empty incremental patch that turns command failures into accepted completions?

**Architecture:** Reuse the existing `eval/ab/run_ab.py` runner instead of building a second harness. Add a small curated JSONL taskset of real repair-eligible benchmark tasks, extend `run_ab.py` to extract repair telemetry from `state.db` (`repair_start`, `repair_meta`, command/tool outcomes), and document a two-run workflow (`repair_mode=off` vs `repair_mode=on`) for the same targeted taskset. Do not add new agent behavior, new DB tables, or new benchmark semantics.

**Tech Stack:** Python 3, existing `eval/ab/run_ab.py`, SQLite `state.db`, JSONL task files, markdown docs.

---

### Task 1: Create the targeted repair taskset

**Files:**
- Create: `eval/ab/repair_targeted_tasks.jsonl`
- Test/Inspect: `eval/ab/benchmark_tasks.jsonl`

**Step 1: Curate 6-8 real repair-oriented tasks**

Select only tasks that already showed one of these patterns in prior runs:
- `patch applied + command failed`
- repair improved status (`failed -> needs_changes` or `completed`)
- repair regressed via `empty_patch`

Prefer a mix like:
- `repo_only_002`
- `repo_only_005`
- `repo_only_008`
- `repo_only_009`
- `kb_code_002`
- `kb_mixed_001`
- `kb_mixed_002`

Keep the original task schema so `run_ab.py` can read the file unchanged:

```json
{"task_id":"repo_only_009","category":"repo_only","requires_kb":false,"goal":"...","expected_citations":[],"test_cmd":"go test ./internal/model/..."}
```

Do not invent synthetic toy tasks in v1. Reuse exact benchmark tasks so results stay comparable to the main benchmark.

**Step 2: Add optional selection metadata only if it stays runner-compatible**

If you want audit context, add extra fields that `run_ab.py` will safely ignore:

```json
"repair_bucket":"empty_patch_regression",
"selection_reason":"patch applied + go test failed in prior benchmark"
```

Do not make the runner depend on those fields in v1.

**Step 3: Sanity-check the file**

Run:

```bash
python3 - <<'PY'
from eval.ab.run_ab import load_jsonl
rows = load_jsonl("eval/ab/repair_targeted_tasks.jsonl")
print(len(rows))
print([r["task_id"] for r in rows])
PY
```

Expected:
- loads without JSON errors
- contains only the curated task IDs

**Step 4: Commit**

```bash
git add eval/ab/repair_targeted_tasks.jsonl
git commit -m "test: add targeted repair evaluation taskset"
```

---

### Task 2: Extract repair telemetry from `state.db`

**Files:**
- Modify: `eval/ab/run_ab.py`
- Test: `eval/tests/test_run_ab.py`

**Step 1: Write failing tests for repair trace extraction**

Add focused tests around `read_run_context()` using a temp SQLite DB with `tool_calls` rows.

Cover at least:
- `TestReadRunContextCapturesRepairTriggered`
- `TestReadRunContextCapturesRepairEmptyPatch`
- `TestReadRunContextCapturesRepairError`
- `TestReadRunContextCapturesCommandFailures`

Model the tool rows with the existing schema:

```python
("repair_start", "", "starting repair generate", "started")
("repair_meta", "", '{"patch_empty": true}', "empty_patch")
("run_command", "go test ./internal/model/...", "undefined: Repo", "error")
```

**Step 2: Extend the `trace` shape in `read_run_context()`**

Add these fields with safe defaults:

```python
{
    "repair_triggered": False,
    "repair_empty_patch": False,
    "repair_error": False,
    "repair_stage_count": 0,
    "repair_failed_commands": [],
    "command_fail_count": 0,
}
```

Parse `tool_calls` like this:
- `repair_start` with any status -> `repair_triggered = True`
- `repair_stage` -> increment `repair_stage_count`
- `repair_meta` with status `empty_patch` -> `repair_empty_patch = True`
- `repair_meta` with status `error` -> `repair_error = True`
- `run_command` with status `error` -> increment `command_fail_count` and append the command text to `repair_failed_commands`

Keep the existing `coder_meta` / `reviewer_meta` / `kb_search` extraction unchanged.

**Step 3: Run the targeted test slice and make it pass**

Run:

```bash
python3 -m unittest eval.tests.test_run_ab
```

Expected:
- new tests pass
- old run_ab tests stay green

**Step 4: Commit**

```bash
git add eval/ab/run_ab.py eval/tests/test_run_ab.py
git commit -m "feat: extract repair telemetry in ab runner"
```

---

### Task 3: Surface repair telemetry in rows and aggregate metrics

**Files:**
- Modify: `eval/ab/run_ab.py`
- Test: `eval/tests/test_run_ab.py`

**Step 1: Add per-row repair fields in `run_one()`**

When building the returned `row`, include:

```python
"repair_triggered": bool(trace.get("repair_triggered", False)),
"repair_empty_patch": bool(trace.get("repair_empty_patch", False)),
"repair_error": bool(trace.get("repair_error", False)),
"repair_stage_count": int(trace.get("repair_stage_count", 0) or 0),
"repair_failed_commands": list(trace.get("repair_failed_commands", [])),
"command_fail_count": int(trace.get("command_fail_count", 0) or 0),
```

Add them on all relevant exit paths:
- normal completion
- timeout path
- isolate-worktree failure path
- dry-run path

Use zero/false defaults for paths that never reached execution.

**Step 2: Extend `aggregate_metrics()` with repair-focused counters**

Add metrics that answer whether repair is useful on the targeted taskset:

```python
"repair_trigger_count": ...,
"repair_trigger_rate": ...,
"repair_empty_patch_count": ...,
"repair_empty_patch_rate": ...,
"repair_error_count": ...,
"command_failure_task_count": ...,
```

Keep them experiment-local, just like `pass_rate` and `avg_duration_sec`.

Do not add speculative metrics like "repair_success_rate" unless they can be computed from one run without comparing two reports.

**Step 3: Extend markdown rendering**

Append a short section after the main metrics table:

```md
## Repair Telemetry

| Experiment | Repair Triggered | Empty Patch | Repair Error | Command Failure Tasks |
|---|---:|---:|---:|---:|
```

This keeps the report human-scannable without changing the existing summary table.

**Step 4: Add tests for row/report fields**

Cover at least:
- per-row defaults when no repair occurs
- aggregate counts when one row has `repair_triggered=True`
- markdown includes the `Repair Telemetry` section

**Step 5: Run tests**

Run:

```bash
python3 -m unittest eval.tests.test_run_ab
python3 -m py_compile eval/ab/run_ab.py
```

Expected:
- all tests pass
- runner still compiles

**Step 6: Commit**

```bash
git add eval/ab/run_ab.py eval/tests/test_run_ab.py
git commit -m "feat: add repair telemetry to ab reports"
```

---

### Task 4: Document the targeted repair-eval workflow

**Files:**
- Modify: `eval/ab/README.md`

**Step 1: Add a new README section**

Document a narrow workflow named `Targeted Repair Eval`.

Include the exact commands for the two-run comparison:

```bash
python3 eval/ab/run_ab.py \
  --tasks eval/ab/repair_targeted_tasks.jsonl \
  --agent-loop-bin ./agent-loop \
  --repo "$(git rev-parse --show-toplevel)" \
  --db-path .agent-loop-artifacts/state-repair-off.db \
  --output-dir eval/reports/repair-targeted-off \
  --max-iterations 2 \
  --repair-mode off \
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
  --repair-mode on \
  --strict-mode
```

State explicitly what to inspect:
- `pass_rate`
- `repair_trigger_count`
- `repair_empty_patch_count`
- per-task `status`
- per-task `repair_triggered`

**Step 2: Add an interpretation guide**

Document the expected decision rules:
- If repair triggers and often yields non-empty patches but still does not improve status, the prompt/constraints need work.
- If repair almost never triggers even on this targeted taskset, the gate is too narrow.
- If repair frequently triggers and improves task status, then it is worth revisiting broader benchmark rollout.

**Step 3: Verify docs format**

Run:

```bash
python3 -m py_compile eval/ab/run_ab.py
```

Expected:
- no syntax regressions from README edits touching command examples only

**Step 4: Commit**

```bash
git add eval/ab/README.md
git commit -m "docs: add targeted repair evaluation workflow"
```

---

### Task 5: Run the first targeted repair eval and summarize

**Files:**
- Use: `eval/ab/repair_targeted_tasks.jsonl`
- Use: `eval/reports/repair-targeted-off/ab_report.json`
- Use: `eval/reports/repair-targeted-on/ab_report.json`

**Step 1: Build the binary**

Run:

```bash
go build -o ./agent-loop ./cmd/agent-loop
```

**Step 2: Run repair-off and repair-on**

Use the two README commands from Task 4.

Keep all settings fixed except `--repair-mode`.

**Step 3: Write the first summary**

Summarize only:
- task IDs where repair triggered
- task IDs where repair returned `empty_patch`
- off vs on status diff for each targeted task
- whether repair changed any task from `failed` or `needs_changes` to `completed`

**Step 4: Commit only if the runner/docs changed**

Do not commit generated reports unless the repo already tracks benchmark output snapshots.

---

### Notes

- YAGNI: do not add a second runner such as `run_repair_eval.py` in v1.
- YAGNI: do not add cross-report comparison code in v1; manual diff across 6-8 tasks is fine.
- Keep `run_ab.py` backward-compatible with the current 24-task benchmark.
- The main success criterion is not headline pass rate. It is whether repair is demonstrably useful once triggered.
