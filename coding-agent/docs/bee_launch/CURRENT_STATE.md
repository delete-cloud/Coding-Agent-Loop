# Bee Launch Current State

## Summary

The repository has the pieces required for launchable Bee tasks, but no unified
Bee launch surface yet. Current capabilities are split across Bee task storage,
workspace-local template parsing, scheduled/proactive planning, topic lifecycle,
Developer Console rendering, observability, and the Bee command bridge.

G118-G126 should connect those surfaces without adding an external executor or
changing existing runtime semantics.

## Bee Task Creation Paths

- `src/coding_agent/bee_runtime.py`
  - `BeeTaskManifest` and `BeeNodeManifest` define sanitized Bee task intent.
  - `parse_bee_task_manifest()` validates manifests and rejects raw sensitive or
    executable fields.
  - `BeeTaskRecord` and `BeeNodeRecord` represent durable task and node state.
  - `PGBeeTaskStore` owns `bee_tasks` and `bee_task_nodes` schema plus task/node
    upsert, load, list, status update, and ready-node claiming.
  - `BeeTaskPlanner.plan_ready_nodes()` plans ready nodes but does not execute.
  - `BeeTaskLifecycle` writes task start/final/abort topic anchors.
  - `build_bee_launch_metadata()` creates safe per-node launch metadata for
    existing durable runs.

There is no `BeeLaunchRequest`, `BeeLaunchPlan`, `BeeLaunchRecord`, or unified
manual/scheduled/proactive launch flow yet.

## Workspace Template Discovery

- `src/coding_agent/bee_workspace.py`
  - `discover_bee_workspace_templates()` and `load_bee_workspace_template()`
    read `.bee/templates/<template_id>/`.
  - `build_bee_manifest_from_workspace_template()` converts workspace metadata
    into a `BeeTaskManifest`.
  - `load_bee_workspace_command_intents()` parses declarative `commands.yaml`
    intent metadata. It does not execute commands.
  - Template and artifact validation rejects unsafe fields, raw output markers,
    secret-like values, and symlink escapes.

Future launch integration should resolve workspace template IDs here and then
bind inputs, topic policy, and workspace policy into a launch plan.

## Task JSON Sync Behavior

- `write_bee_workspace_run_artifacts()` writes sanitized `.bee/runs/<task>/`
  artifacts:
  - `task.json`
  - `report.md`
  - `evidence/`
  - optional `memory_candidates.yaml`
- `discover_bee_workspace_run_artifacts()` reads sanitized run artifact
  summaries for console display.
- `task.json` is a sanitized mirror and not authoritative state.

There is no launch-time task artifact sync yet. G122/G124/G125 should reuse this
writer when workspace artifacts are enabled.

## Command Bridge Execution Behavior

- `src/coding_agent/bee_command_bridge.py`
  - `resolve_bee_command_intent()` resolves a node `command_ref` to a workspace
    command intent without execution.
  - `plan_bee_command_intent()` evaluates an explicit command candidate through
    existing command policy and approval routing, without execution.
  - `run_bee_validation_node()` allows only validation nodes through the
    existing `ValidationRunner`.
  - `complete_bee_node_from_bridge_result()` requires accepted evidence or a
    passed validation report before a node may complete.

Launch must not execute nodes directly. Any future node execution still routes
through this bridge and existing safety gates.

## Schedule And Proactive Signal Planning

- `src/coding_agent/scheduled_runs.py`
  - `ScheduleRecord`, `ScheduleTriggerRecord`, and `ProactiveSignalRecord`
    model schedules, triggers, and signals.
  - `ScheduledRunPlanner.plan_due_schedules()` creates bounded
    `ScheduledLaunchIntent` records for due schedules.
  - `ProactiveSignalPlanner.plan_new_signals()` consumes bounded new signals,
    applies cooldown, and produces `ScheduledLaunchIntent` records.
  - `ScheduledRunLaunchPreparer.prepare()` resolves or creates a Topic for a
    scheduled run and returns safe run metadata.

These planners currently prepare durable agent runs, not Bee launch records or
Bee tasks. G124/G125 should add Bee launch metadata without breaking existing
scheduled run behavior.

## Topic Policy Behavior

- `src/coding_agent/topic_lifecycle.py` creates/finalizes/aborts topics and
  writes topic anchors.
- `src/coding_agent/topic_store.py` stores durable topics, anchors, recall
  links, and costs.
- `BeeTaskLifecycle` already writes Bee task anchors against a topic.
- `ScheduledRunLaunchPreparer` can create or continue an open topic for
  scheduled runs.

Bee launch should create or continue a Topic through product-layer topic policy,
not by changing AgentKit Core.

## Console Routes

- `src/coding_agent/ui/developer_console.py`
  - `ConsoleBeePage` renders Bee task, node, workspace template, run artifact,
    and command intent summaries.
  - G117 added command bridge status, approval route, and evidence status fields
    to command intent rows.
- `src/coding_agent/ui/http_server.py` wires `/console/bee` through existing
  console stores and access checks.

Future launch integration can extend `/console/bee` with launch list/detail
summaries and lifecycle-control visibility. Console pages must not execute
launches or bypass policy.

## Observability And Metrics

- `src/coding_agent/observability.py`
  - `PrometheusMetricsObservationSink` records low-cardinality span/event
    labels.
  - Bee task/workspace metrics already allow low-cardinality labels such as
    `task_kind`, `task_profile`, `task_status`, `node_kind`, `node_profile`,
    `node_status`, `command_category`, `command_policy`, and `command_status`.
  - High-cardinality labels such as task IDs, node IDs, topic IDs, run IDs,
    session IDs, command strings, and file paths are dropped or normalized.

G126 should add low-cardinality launch metrics only. `launch_id`, `task_id`,
`topic_id`, `run_id`, `session_id`, `node_id`, and raw commands must not be
Prometheus labels.

## Exact Files Likely To Modify

- `src/coding_agent/bee_launch.py` or equivalent new product-layer module for:
  - `BeeLaunchRequest`
  - `BeeLaunchPlan`
  - `BeeLaunchRecord`
  - launch store
  - template resolution
  - input binding
  - manual/schedule/proactive launch orchestration
- `src/coding_agent/bee_runtime.py` for narrow lifecycle-control additions if
  existing task/node status APIs are insufficient.
- `src/coding_agent/bee_workspace.py` for launch-time task artifact sync only if
  existing artifact writer needs a small extension.
- `src/coding_agent/scheduled_runs.py` for scheduled/proactive Bee launch
  metadata and planner integration.
- `src/coding_agent/ui/developer_console.py` and possibly
  `src/coding_agent/ui/http_server.py` for launch list/detail console views.
- `src/coding_agent/observability.py` for low-cardinality launch metric labels.
- Tests likely under:
  - `tests/coding_agent/test_bee_launch.py`
  - `tests/coding_agent/test_scheduled_runs.py`
  - `tests/ui/test_developer_console.py`
  - existing Bee runtime/workspace/command bridge tests.

## Existing Tests To Preserve

- `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
- `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
- `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
- `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`

## Out Of Scope

- External executor.
- Kubernetes, Argo Workflows, Argo CD, Docker-only execution, or hosted
  executor integration.
- Homelab-specific templates or NetBird/OCI/nmem logic.
- nmem deployment or sync.
- Desktop app, bridge app, or multi-agent task graph runtime.
- Any launch path that bypasses command policy, workspace policy, path policy,
  HITL, validation policy, or the Bee command bridge.
