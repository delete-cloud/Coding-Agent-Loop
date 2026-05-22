# Bee Launch Goal Progress

This ledger tracks G118-G126 for the Bee Launch / Scheduled Bee Task Integration phase.

## G118_BEE_LAUNCH_CURRENT_STATE_MAP

### Before

- Goal id: G118_BEE_LAUNCH_CURRENT_STATE_MAP
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/bee_launch/CURRENT_STATE.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - Current state map exists.
  - It documents Bee task creation, workspace templates, task.json sync, command bridge, schedule/proactive planners, topic policy, console, observability, later edit points, tests to preserve, and out-of-scope work.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/bee_launch/CURRENT_STATE.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `git diff --check -- .`
- Results:
  - Current state map exists and covers Bee runtime, workspace templates, task.json sync, command bridge, schedule/proactive planning, topic policy, console, observability, candidate edit points, preserved tests, and out-of-scope work.
  - Bee command bridge, Bee runtime, Bee workspace, and scheduled run baselines passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - G119 still needs ADR-backed launch boundaries.
  - No launch model, store, manual launch, scheduled launch, proactive launch, lifecycle controls, console launch view, or launch metrics exist yet.

## G119_BEE_LAUNCH_ADR

### Before

- Goal id: G119_BEE_LAUNCH_ADR
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and is accepted.
  - It defines Bee launch request/plan/source/policy/template/input/topic/workspace/result boundaries.
  - It states launch creates or continues Topic, creates durable BeeTask and task.json when enabled, does not execute arbitrary commands, schedules/signals cannot bypass safety policy, and external executors are deferred.
  - No production code changes are made.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `git diff --check -- .`
- Results:
  - ADR-0044 exists and is accepted.
  - It defines Bee launch sources, request, template resolution, input binding, topic/workspace/launch policy, launch plan, launch result, durable records, console/observability boundaries, and deferred work.
  - Bee command bridge and scheduled run baselines passed.
  - Whitespace diff check passed.
  - Production code unchanged.
- Remaining risks:
  - G120 still needs durable launch records and store APIs.
  - Launch planning, manual/scheduled/proactive launch flows, lifecycle controls, console launch views, and launch metrics remain pending.

## G120_BEE_LAUNCH_MODEL_AND_STORE

### Before

- Goal id: G120_BEE_LAUNCH_MODEL_AND_STORE
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Stop criteria:
  - Durable Bee launch record model exists with manual, schedule, and proactive signal sources.
  - Store can create, load, list, update status, attach task/topic/session, and link schedule/signal identifiers.
  - Schema initialization is idempotent and does not mutate existing Bee task, schedule, or topic semantics.
  - Store APIs are tested with deterministic fake/local storage.
  - Launch records do not create tasks or execute nodes.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_scheduled_runs.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Added `BeeLaunchRecord` with manual, schedule, and proactive signal sources plus planned, launching, launched, failed, and cancelled statuses.
  - Added `PGBeeLaunchStore` with idempotent schema initialization, create/load/list/status-update/result-attach APIs, and schedule/signal linkage fields.
  - Added deterministic fake-pool tests for schema idempotency, CRUD/list behavior, status updates, task/topic/session attach, schedule/signal links, no-leak validation, and missing-row failures.
  - Bee runtime and scheduled run regression tests passed.
  - Scoped format/lint and whitespace diff checks passed.
- Remaining risks:
  - G121 still needs template resolution and input binding.
  - Manual launch, scheduled launch, proactive launch, lifecycle controls, console launch views, and launch metrics remain pending.
