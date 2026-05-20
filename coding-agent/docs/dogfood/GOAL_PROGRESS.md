# Dogfood + Demo Readiness Goal Progress

Date started: 2026-05-20

## Phase Scope

This phase validates the completed Coding-Agent-Loop platform on local,
repo-scoped dogfood tasks and produces a repeatable demo path. It must not
rewrite AgentKit Core, change G00-G63 contracts, add schedule/sandbox/desktop
or multi-agent work, require production credentials, or depend on external
hosted services.

Real dogfood completion requires run_id-level evidence. If real agent execution
cannot be performed with local configuration, the relevant goal must record a
blocker report explaining why.

## Planned Goals

| Goal | Scope | Expected result |
| --- | --- | --- |
| G64 | Current-state map and dogfood plan. | Document available local surfaces, evidence requirements, and deterministic demo plan. |
| G65 | Real dogfood execution evidence. | Execute one or more local repo tasks and record run_id-level evidence or a blocker report. |
| G66 | Repeatable demo readiness. | Add a deterministic demo guide/checklist that exercises runtime, console, observability, and release surfaces. |
| G67 | Final smoke and report. | Run practical regression checks and publish the implementation report. |

## G64_DOGFOOD_CURRENT_STATE_AND_PLAN

### Before

- Goal id: G64_DOGFOOD_CURRENT_STATE_AND_PLAN
- Intended files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/CURRENT_STATE.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/GOAL_PROGRESS.md`
  - `test -f docs/dogfood/CURRENT_STATE.md`
  - `rg -n "G64|G65|G66|G67|run_id-level evidence|repeatable demo path" docs/dogfood`
  - `git diff --check -- .`
- Stop criteria:
  - The repository has no deterministic local route to define a demo path.
  - The dogfood evidence requirement cannot be expressed without leaking raw
    prompt, content, result text, command output, stdout, stderr, env, or
    secrets.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/CURRENT_STATE.md`
- Dogfood evidence:
  - G64 is documentation-only and does not claim real dogfood execution.
  - Real run_id-level evidence is explicitly deferred to G65.
- Tests run:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/GOAL_PROGRESS.md`
  - `test -f docs/dogfood/CURRENT_STATE.md`
  - `rg -n "G64|G65|G66|G67|run_id-level evidence|repeatable demo path" docs/dogfood`
  - `git diff --check -- .`
- Results:
  - All commands passed.
- Remaining risks:
  - G65 still needs to prove whether real local agent execution can produce
    run_id-level evidence without production credentials or external hosted
    services.

## G65_DOGFOOD_REAL_RUN_EVIDENCE

### Before

- Goal id: G65_DOGFOOD_REAL_RUN_EVIDENCE
- Intended files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/RUN_EVIDENCE.md`
  - `tests/dogfood/test_local_dogfood_run.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `git diff --check -- .`
- Stop criteria:
  - A local run cannot produce a non-empty `session_id` and `run_id`.
  - A local run requires production credentials or external hosted services.
  - Evidence would require storing raw prompt, message, model result text,
    command output, stdout, stderr, env, or secrets.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/RUN_EVIDENCE.md`
  - `tests/dogfood/test_local_dogfood_run.py`
- Dogfood evidence:
  - Recorded in `docs/dogfood/RUN_EVIDENCE.md`.
  - `session_id`: `3bfed77d-4d2c-4f03-977f-f7c56a2e9a04`
  - `run_id`: `5c716d8294c84d99848dba9aeab0d0b5`
  - run status: `completed`
  - `/healthz`, `/readyz`, `/console/sessions`, `/console/runs`,
    `/console/runs/{run_id}`, `/console/observability?run_id={run_id}`, and
    `/console/release` returned `200`.
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run ruff format --check tests/dogfood/test_local_dogfood_run.py`
  - `uv run ruff check tests/dogfood/test_local_dogfood_run.py`
  - `git diff --check -- .`
- Results:
  - `tests/dogfood/test_local_dogfood_run.py`: `1 passed`
  - `tests/ui/test_developer_console.py`: `27 passed`
  - scoped ruff format/check passed after formatting the new test file.
  - `git diff --check -- .` passed.
  - Local evidence harness produced a completed run with message snapshot and
    console route evidence.
- Fix iterations:
  - 1. Adjusted the new test to accept the actual no-tool local turn
    `steps_taken` value instead of assuming it would be `1`.
- Remaining risks:
  - G65 uses the existing local `MockProvider` because production credentials
    and hosted LLM calls are out of scope for this phase.
  - G66 still needs to turn the validated path into a repeatable demo checklist.

## G66_DOGFOOD_REPEATABLE_DEMO_PATH

### Before

- Goal id: G66_DOGFOOD_REPEATABLE_DEMO_PATH
- Intended files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/DEMO_PATH.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/DEMO_PATH.md`
  - `rg -n "Demo Checklist|G65 evidence replay|/console/runs/\\{run_id\\}|No production credentials" docs/dogfood/DEMO_PATH.md`
  - `git diff --check -- .`
- Stop criteria:
  - A deterministic local demo cannot be described without production
    credentials, external hosted services, or real external LLM calls.
  - The demo path would require schedules, sandbox, desktop, bridge,
    proactive-agent, or multi-agent work.
  - The demo documentation would need raw prompt, message, model result text,
    command output, stdout, stderr, environment values, or secrets.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/DEMO_PATH.md`
- Dogfood evidence:
  - G66 does not create new run evidence.
  - It turns the G65 run evidence and existing console/observability/release
    surfaces into a repeatable demo checklist.
- Tests run:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/DEMO_PATH.md`
  - `rg -n "Demo Checklist|G65 evidence replay|/console/runs/\\{run_id\\}|No production credentials" docs/dogfood/DEMO_PATH.md`
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `git diff --check -- .`
- Results:
  - `tests/dogfood/test_local_dogfood_run.py`: `1 passed`
  - Documentation checks passed.
  - `git diff --check -- .` passed.
- Review notes:
  - Local review found the initial demo text implied a G65 in-memory `run_id`
    would be visible in a separately started server. The doc now separates G65
    same-process replay from manual server empty-state and persistent-store
    demos.
- Remaining risks:
  - G67 still needs to run final practical smoke checks and publish the phase
    implementation report.

## G67_DOGFOOD_FINAL_SMOKE_AND_REPORT

### Before

- Goal id: G67_DOGFOOD_FINAL_SMOKE_AND_REPORT
- Intended files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/IMPLEMENTATION_REPORT.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
  - `uv run pytest tests/coding_agent/evaluation/ -v`
  - release verification manifest commands from
    `docs/release_hardening/release-verification.yaml`
  - `uv run ruff format --check tests/dogfood/test_local_dogfood_run.py`
  - `uv run ruff check tests/dogfood/test_local_dogfood_run.py`
  - `git diff --check -- .`
- Stop criteria:
  - Final deterministic smoke checks cannot run.
  - Any check fails more than two fix iterations for the same reason.
  - The final report would need to include raw prompt, message, model result
    text, command output, stdout, stderr, environment values, or secrets.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/IMPLEMENTATION_REPORT.md`
- Dogfood evidence:
  - Final report references G65 evidence in `docs/dogfood/RUN_EVIDENCE.md`.
  - No new run_id evidence was required for G67.
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
  - `uv run pytest tests/coding_agent/evaluation/ -v`
  - `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
  - `uv run ruff format --check tests/dogfood/test_local_dogfood_run.py`
  - `uv run ruff check tests/dogfood/test_local_dogfood_run.py`
  - `git diff --check -- .`
- Results:
  - `tests/dogfood/test_local_dogfood_run.py`: `1 passed`
  - `tests/ui/test_developer_console.py`: `27 passed`
  - `tests/integration/test_durable_runtime_smoke.py`: `6 passed`
  - `tests/coding_agent/test_context_system_smoke.py`: `1 passed`
  - `tests/coding_agent/action_safety/test_safe_action_smoke.py`: `1 passed`
  - `tests/coding_agent/test_observability_platform_smoke.py`: `2 passed`
  - `tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans"`:
    `8 passed, 29 deselected`
  - `tests/coding_agent/evaluation/`: `20 passed`
  - observability/release contract suite: `32 passed`
  - scoped ruff format/check passed.
  - `git diff --check -- .` passed.
- Remaining risks:
  - Hosted LLM/provider dogfood remains outside this phase because production
    credentials and hosted services are out of scope.
  - Optional Prometheus/Grafana visual demo still requires starting the local
    stack separately.
