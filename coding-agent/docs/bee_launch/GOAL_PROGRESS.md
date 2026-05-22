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

## G121_BEE_TEMPLATE_RESOLUTION_AND_INPUT_BINDING

### Before

- Goal id: G121_BEE_TEMPLATE_RESOLUTION_AND_INPUT_BINDING
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Stop criteria:
  - Launch request and plan models exist for template resolution/input binding.
  - Template resolution uses workspace-local `.bee/templates` and existing Bee workspace parsers.
  - Required/default inputs, unknown input policy, workspace policy, and topic policy are validated safely.
  - Invalid or missing templates and unsafe inputs fail deterministically.
  - No BeeTask is created and no node/command execution occurs.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Added `BeeLaunchRequest`, `BeeTemplateResolution`, `BeeInputBinding`, and `BeeLaunchPlan`.
  - `build_bee_launch_plan()` resolves workspace-local `.bee/templates` through existing Bee workspace parsing and manifest validation.
  - Launch input binding validates required inputs, applies defaults, rejects unknown inputs by default, validates workspace existence, and rejects unsafe input/policy metadata.
  - The launch plan path does not create Bee tasks, write artifacts, or execute commands.
  - Bee launch, workspace, and runtime tests passed.
- Remaining risks:
  - G122 still needs manual launch to create topic/task/task.json through the product surface.
  - Scheduled launch, proactive launch, lifecycle controls, console launch views, and launch metrics remain pending.

## G122_MANUAL_BEE_LAUNCH_API_OR_CLI

### Before

- Goal id: G122_MANUAL_BEE_LAUNCH_API_OR_CLI
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Stop criteria:
  - Manual launch service creates a Bee launch record, resolves template/inputs, creates or continues a Topic, creates durable BeeTask/nodes, and optionally writes workspace task.json artifacts.
  - Manual launch returns launch_id, task_id, topic_id, and status.
  - Manual launch does not execute nodes, commands.yaml, or arbitrary commands.
  - Missing template and invalid inputs fail deterministically.
  - Existing Bee runtime/workspace/topic smoke tests still pass.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Added `BeeLaunchOrchestrator` and `BeeLaunchResult` as the manual launch product-layer service.
  - Manual launch creates a durable launch record, resolves template/input binding, creates or continues an open Topic, creates durable BeeTask/node records, attaches launch result IDs, and can write sanitized workspace task artifacts.
  - Missing templates and invalid inputs fail before durable launch records are created.
  - Command intent metadata remains non-executing; launched nodes stay pending for the Bee command bridge.
  - Bee launch, Bee runtime, Bee workspace, and Topic smoke tests passed.
- Remaining risks:
  - G123 still needs resume, retry, cancel, and abort lifecycle controls.
  - Scheduled launch, proactive launch, console launch views, and launch metrics remain pending.

## G123_BEE_TASK_LIFECYCLE_CONTROLS

### Before

- Goal id: G123_BEE_TASK_LIFECYCLE_CONTROLS
- Intended files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Stop criteria:
  - Resume recomputes ready state for incomplete nodes without duplicating completed node attempts.
  - Retry only applies to failed node attempts and preserves previous metadata/evidence.
  - Retry of completed nodes is rejected.
  - Cancel and abort record terminal task status, with abort writing a Bee task abort anchor where topic/tape context is provided.
  - Lifecycle controls do not execute nodes or bypass command/workspace/HITL policy.

### After

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `docs/adr/0044-bee-launch-surfaces.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Added `BeeTaskLifecycleController` for resume, retry, cancel, and abort controls over existing durable Bee tasks/nodes.
  - Resume resets incomplete failed/running nodes to pending without incrementing attempt counts or touching completed nodes.
  - Retry is limited to failed nodes, increments attempt count, and preserves prior evidence metadata.
  - Cancel records terminal task status and skips incomplete nodes; abort can additionally write a `bee_task_aborted` anchor through the Bee task lifecycle.
  - Lifecycle controls only mutate task/node lifecycle state and do not execute command intents or bypass Bee command bridge policy.
- Remaining risks:
  - Scheduled launch, proactive launch, console launch views, and launch metrics remain pending.

### Post-Review Lifecycle Fix

- Changed files:
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Resume now leaves already-claimed `ready` nodes unchanged to avoid duplicate claim/execution paths.
  - Abort validates anchor context and writes the abort anchor before durable task/node closure, so anchor failures do not leave the task cancelled without abort evidence.
  - Cancel/abort now clear active run timing/linkage from skipped nodes and record a terminal timestamp.
- Remaining risks:
  - Close-plus-anchor is still not a single database transaction because tape anchors and Bee task rows use separate product abstractions.
  - Scheduled launch, proactive launch, console launch views, and launch metrics remain pending.

### Post-Review Skipped Node Cleanup Fix

- Changed files:
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -k lifecycle -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Cancel/abort cleanup now also normalizes stale skipped nodes that still carry active run linkage.
  - Added regression coverage for a skipped node with stale `run_id` and `started_at`.
- Remaining risks:
  - Close-plus-anchor is still not a single database transaction because tape anchors and Bee task rows use separate product abstractions.
  - Scheduled launch, proactive launch, console launch views, and launch metrics remain pending.

### Post-Review Artifact Gate Fix

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Moved workspace artifact policy enforcement before launch records, topic creation, task creation, and node creation.
  - Added regression coverage that denied artifact writes leave no launch, topic, task, node, or `.bee/runs` side effects.
  - Bee launch, Bee runtime, Bee workspace, and Topic smoke tests passed.
- Remaining risks:
  - G123 still needs resume, retry, cancel, and abort lifecycle controls.
  - Scheduled launch, proactive launch, console launch views, and launch metrics remain pending.

### Post-Review Label Fix

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Split JSON key forbidden terms from label value forbidden terms so executable-shaped input/default keys stay rejected without rejecting safe command intent names like `shellcheck`.
  - Added regression coverage for safe non-executing command intent names containing tool words.
  - Bee launch, workspace, and runtime tests passed.
- Remaining risks:
  - G122 still needs manual launch to create topic/task/task.json through the product surface.
  - Scheduled launch, proactive launch, lifecycle controls, console launch views, and launch metrics remain pending.

### Post-Review Safety Fix

- Changed files:
  - `docs/bee_launch/GOAL_PROGRESS.md`
  - `src/coding_agent/bee_launch.py`
  - `tests/coding_agent/test_bee_launch.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_bee_launch.py -v`
  - `uv run pytest tests/coding_agent/test_bee_workspace.py -q`
  - `uv run pytest tests/coding_agent/test_bee_runtime.py -q`
  - `uv run ruff format --check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `uv run ruff check src/coding_agent/bee_launch.py tests/coding_agent/test_bee_launch.py`
  - `git diff --check -- .`
- Results:
  - Rejected symlinked workspace `.bee` roots before resolving templates.
  - Extended launch input/policy JSON validation to reject executable-shaped keys such as `cmd`, `args`, `argv`, `exec`, `executor`, `script`, and `shell`.
  - Added regression coverage for symlinked `.bee` roots, nested executable-shaped request inputs, and executable-shaped template defaults.
- Remaining risks:
  - G122 still needs manual launch to create topic/task/task.json through the product surface.
  - Scheduled launch, proactive launch, lifecycle controls, console launch views, and launch metrics remain pending.
