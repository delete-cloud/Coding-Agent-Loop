# Scheduled Runs Goal Progress

This ledger tracks G85-G92 for the Topic-aware Scheduled Runs / Proactive Signals phase.

## G85_SCHEDULED_RUNS_CURRENT_STATE_MAP

### Before

- Goal id: G85_SCHEDULED_RUNS_CURRENT_STATE_MAP
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/CURRENT_STATE.md`
- Verification commands:
  - `test -f docs/scheduled_runs/CURRENT_STATE.md`
  - `rg -n "Topic|scheduled|proactive|SessionManager|approval|Prometheus|Developer Console" docs/scheduled_runs/CURRENT_STATE.md`
  - `git diff --check -- .`
- Stop criteria:
  - `docs/scheduled_runs/CURRENT_STATE.md` exists and maps existing runtime, topic, safety, workspace, console, observability, and release surfaces for later scheduled/proactive work.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: merged via PR #294.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/CURRENT_STATE.md`
- Tests run:
  - `test -f docs/scheduled_runs/CURRENT_STATE.md`
  - `rg -n "Topic|scheduled|proactive|SessionManager|approval|Prometheus|Developer Console" docs/scheduled_runs/CURRENT_STATE.md`
  - `git diff --check -- .`
- Results:
  - current-state document exists.
  - required current-state terms are present.
  - whitespace diff check: passed.
- Remaining risks:
  - G85 is a state map only. The ADR, durable schedule/proactive signal schema, bounded trigger planning, topic-aware run launch, console/observability integration, and final smoke tests are deferred to G86-G92.

## G86_SCHEDULED_RUNS_ADR

### Before

- Goal id: G86_SCHEDULED_RUNS_ADR
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
- Verification commands:
  - `test -f docs/adr/0040-topic-aware-scheduled-runs.md`
  - `rg -n "Scheduled Run|Proactive Signal|Topic|AgentKit Core|approval|Prometheus|Bee workflow" docs/adr/0040-topic-aware-scheduled-runs.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and defines topic-aware scheduled run/proactive signal ownership, durable record boundaries, safety policy, bounded trigger behavior, observability/cardinality rules, and out-of-scope Bee/desktop/bridge/multi-agent work.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: merged via PR #295.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
- Tests run:
  - `test -f docs/adr/0040-topic-aware-scheduled-runs.md`
  - `rg -n "Scheduled Run|Proactive Signal|Topic|AgentKit Core|approval|Prometheus|Bee workflow" docs/adr/0040-topic-aware-scheduled-runs.md`
  - `git diff --check -- .`
- Results:
  - ADR exists and includes the required schedule, proactive signal, topic, AgentKit Core, safety, Prometheus, and Bee workflow boundary terms.
  - whitespace diff check: passed.
- Remaining risks:
  - G86 is ADR-only. Durable schema/store, trigger planning, topic-aware launch intents, proactive signal dedupe/cooldown, console/observability integration, and final smoke tests are deferred to G87-G92.

## G87_SCHEDULED_RUNS_STORE_AND_SCHEMA

### Before

- Goal id: G87_SCHEDULED_RUNS_STORE_AND_SCHEMA
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Stop criteria:
  - Durable schedule/proactive signal schema initialization is idempotent.
  - Store APIs cover schedule create/load/list/status updates, trigger records, signal record/load/list/status updates, and signal deduplication.
  - Records reject unsafe metadata and raw sensitive display fields.
  - No trigger planner, run execution, Bee workflow, desktop, bridge, external executor, or AgentKit Core changes are implemented.

### After

- Status: merged via PR #296.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Results:
  - scheduled run store tests: 4 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G87 adds durable records and store APIs only. Trigger planning, topic-aware launch intents, proactive cooldown planning, console/observability integration, and final smoke tests are deferred to G88-G92.

## G88_SCHEDULED_TRIGGER_PLANNER

### Before

- Goal id: G88_SCHEDULED_TRIGGER_PLANNER
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Stop criteria:
  - Planner uses an explicit fakeable clock value and deterministic store calls.
  - Planner returns bounded launch intents and trigger records without executing runs.
  - Planner skips inactive/not-due schedules and advances due schedules safely.
  - No Bee workflow, external executor, autonomous loop, or AgentKit Core change is implemented.

### After

- Status: merged via PR #297.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Results:
  - scheduled run tests: 7 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G88 only plans due schedules. Topic-aware launch preparation, proactive signal cooldown planning, console/observability integration, and final smoke tests are deferred to G89-G92.

## G89_TOPIC_AWARE_LAUNCH_PREPARATION

### Before

- Goal id: G89_TOPIC_AWARE_LAUNCH_PREPARATION
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Stop criteria:
  - Launch preparation continues an existing open topic when present.
  - Launch preparation creates a safe new topic when no topic is provided or loadable.
  - Launch preparation returns safe run metadata and does not execute runs or bypass approval/workspace/action policy.
  - Existing topic lifecycle tests still pass.

### After

- Status: merged via PR #298.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Results:
  - scheduled run and topic lifecycle tests: 20 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G89 prepares topic-aware launch metadata only. Proactive signal cooldown planning, console/observability integration, and final smoke tests are deferred to G90-G92.

## G90_PROACTIVE_SIGNAL_PLANNER

### Before

- Goal id: G90_PROACTIVE_SIGNAL_PLANNER
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Stop criteria:
  - Proactive signal planner consumes only bounded `new` signals.
  - Planner skips signals in cooldown and does not create unbounded loops.
  - Planner marks planned/skipped signals deterministically and returns launch intents without executing runs.
  - Signal metadata and launch intent metadata remain safe and bounded.

### After

- Status: merged via PR #299.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/scheduled_runs.py`
  - `tests/coding_agent/test_scheduled_runs.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check src/coding_agent/scheduled_runs.py tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Results:
  - scheduled run tests: 15 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G90 only plans proactive signals into launch intents. Console/observability integration and final smoke tests are deferred to G91-G92.

## G91_SCHEDULED_CONSOLE_AND_OBSERVABILITY

### Before

- Goal id: G91_SCHEDULED_CONSOLE_AND_OBSERVABILITY
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/observability.py`
  - `tests/ui/test_developer_console.py`
  - `tests/coding_agent/test_observability.py`
- Verification commands:
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Stop criteria:
  - Developer Console renders schedules, schedule triggers, and proactive signals from existing stores.
  - Console pages remain read-only and do not bypass approval, action, command, workspace, or HITL policy.
  - Prometheus allows only low-cardinality schedule/signal labels and rejects schedule_id/signal_id.
  - Console and observability tests remain deterministic and do not require external services.

### After

- Status: merged via PR #300.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/observability.py`
  - `tests/ui/test_developer_console.py`
  - `tests/coding_agent/test_observability.py`
- Tests run:
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Results:
  - Developer Console tests: 34 passed.
  - observability tests: 27 passed.
  - scheduled run tests: 15 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G91 exposes read-only console and metrics-label integration only. Final cross-phase smoke tests and docs are deferred to G92.

## G92_SCHEDULED_RUNS_E2E_SMOKE_AND_DOCS

### Before

- Goal id: G92_SCHEDULED_RUNS_E2E_SMOKE_AND_DOCS
- Intended files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/USAGE.md`
  - `docs/scheduled_runs/IMPLEMENTATION_REPORT.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
  - `tests/coding_agent/test_scheduled_runs.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run pytest tests/coding_agent/evaluation/ -v`
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py tests/dogfood/test_workspace_provider_demo.py -v`
  - `uv run ruff format --check tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Stop criteria:
  - Final scheduled run smoke covers topic creation, schedule trigger planning, proactive signal planning, console rendering, and metrics no-leak behavior.
  - `USAGE.md` and `IMPLEMENTATION_REPORT.md` exist.
  - Prior durable runtime, context system, action safety, observability, console, dogfood, workspace, and topic smoke tests pass where practical.
  - No Bee workflow runtime, DAG executor, desktop, bridge, proactive loop, external executor, or multi-agent task graph is introduced.

### After

- Status: merged via PR #301.
- Changed files:
  - `docs/scheduled_runs/GOAL_PROGRESS.md`
  - `docs/scheduled_runs/USAGE.md`
  - `docs/scheduled_runs/IMPLEMENTATION_REPORT.md`
  - `docs/adr/0040-topic-aware-scheduled-runs.md`
  - `tests/coding_agent/test_scheduled_runs.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run pytest tests/coding_agent/evaluation/ -v`
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py tests/dogfood/test_workspace_provider_demo.py -v`
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
  - `uv run ruff format --check tests/coding_agent/test_scheduled_runs.py`
  - `uv run ruff check tests/coding_agent/test_scheduled_runs.py`
  - `git diff --check -- .`
- Results:
  - scheduled run tests: 16 passed.
  - topic layer smoke: 1 passed.
  - Developer Console tests: 34 passed.
  - observability tests: 29 passed.
  - durable runtime smoke: 6 passed.
  - context system smoke: 1 passed.
  - action safety smoke: 1 passed.
  - evaluation tests: 20 passed.
  - dogfood/workspace demo tests: 2 passed.
  - AgentKit context pipeline release gate: 8 passed, 29 deselected.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - This phase intentionally stops at deterministic planning, topic launch preparation, console visibility, and smoke coverage. Production scheduler workers, Bee/DAG runtime, external executors, desktop, bridge, and multi-agent task graphs remain out of scope.
