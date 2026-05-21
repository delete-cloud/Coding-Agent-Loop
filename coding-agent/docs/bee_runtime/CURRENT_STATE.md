# Bee Runtime Current State

G93 inspected the current Coding Agent runtime, Topic layer, scheduled runs, workspace provider, action safety, context system, observability, and Developer Console surfaces before adding a generic Bee-style workflow template and task manifest runtime.

## Summary

- There is no first-class Bee workflow, task manifest, task store, task planner, or task node runtime in production code today.
- The correct foundation already exists in Coding Agent rather than AgentKit Core: Topics, scheduled launch metadata, durable runs, workspace execution bindings, action safety, context packs, and observability.
- Bee should be modeled as a product/runtime profile built on Topic, not as a new AgentKit Core primitive.
- This phase can add generic task manifests and bounded node planning without external executors, Kubernetes, Argo Workflows, desktop, bridge, or multi-agent task graphs.
- Homelab-specific templates and integrations must remain out of scope.

## Existing Topic Foundation

Relevant files:

- `src/coding_agent/topic_store.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/topic_recall.py`
- `src/coding_agent/topic_provenance.py`
- `src/coding_agent/plugins/topic.py`
- `tests/coding_agent/test_topic_lifecycle.py`
- `tests/coding_agent/test_topic_layer_smoke.py`
- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `docs/topic_layer/IMPLEMENTATION_REPORT.md`

Current behavior:

- `TopicRecord` persists `topic_id`, `tape_id`, `session_id`, `kind`, `status`, `title`, `summary`, owner, lifecycle seqs, timestamps, and safe metadata.
- `TopicLifecycle` writes `topic_initial`, `topic_finalized`, and `topic_aborted` product anchors through existing generic tape anchors.
- `TopicRecallLinkRecord` and `TopicCostRecord` already support recall and cost aggregation.
- `ContextPack` integration can carry `source_topic_ids` and `source_entry_ranges`.
- Prometheus must not use `topic_id` labels.

Compatibility and privacy caveat:

- The legacy `TopicPlugin` still exists in `src/coding_agent/plugins/topic.py`.
- It detects topic shifts from recent file overlap and writes `topic_start` / `topic_end` anchors plus `topic_start` / `topic_end` session events.
- Its `topic_start` path can store a truncated first user message in anchor payload `content` and event `label`.
- ADR-0039 already identifies this as a compatibility/privacy risk. G97 must inspect, migrate, bypass, or explicitly supersede this plugin path before adding Bee-specific topic anchors so Bee work does not create conflicting anchors or repeat raw-message exposure.

Bee implications:

- A Bee workflow should bind to one Topic.
- Bee task/node provenance can reference `topic_id` in durable product records and console routes, but not in Prometheus labels.
- Bee-specific anchors should be additive product anchors on the Topic tape range only after resolving the legacy `TopicPlugin` compatibility boundary. They must carry safe bounded metadata only.

## Existing Scheduled Run Foundation

Relevant files:

- `src/coding_agent/scheduled_runs.py`
- `tests/coding_agent/test_scheduled_runs.py`
- `docs/adr/0040-topic-aware-scheduled-runs.md`
- `docs/scheduled_runs/USAGE.md`
- `docs/scheduled_runs/IMPLEMENTATION_REPORT.md`

Current behavior:

- `ScheduleRecord`, `ScheduleTriggerRecord`, and `ProactiveSignalRecord` persist bounded scheduled/proactive state.
- `ScheduledLaunchIntent` and `PreparedScheduledRun` carry safe launch metadata.
- `ScheduledRunLaunchPreparer` creates or continues Topics before normal durable run creation.
- Schedule/proactive planners do not execute tools.

Bee implications:

- Scheduled/proactive launch metadata can optionally include a Bee task/workflow profile later.
- Bee runtime should reuse the same normal durable run path and must not execute nodes directly.
- If a schedule launches a Bee workflow, it should create normal launch intents and safe task provenance metadata.

## Durable Runtime And Run Metadata

Relevant files:

- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/runtime_store.py`
- `tests/integration/test_durable_runtime_smoke.py`
- `tests/ui/test_session_manager_runtime.py`

Current behavior:

- `SessionManager.run_agent()` creates a normal durable run through `_create_runtime_agent_run`, updates it through `_update_runtime_agent_run`, and preserves approval policy and execution binding semantics.
- `_run_metadata_for_session()` currently includes provider, model, approval policy, and max steps.
- Existing scheduled launch metadata is additive and does not overwrite approval/workspace policy fields.

Bee implications:

- Bee task execution must become normal durable runs with additive metadata, not a parallel run lifecycle.
- Task IDs and node IDs may appear in durable run metadata as safe correlation IDs.
- Raw prompt/content/message/result/secret/text/command output/stdout/stderr/env must not be stored in task metadata.

## Workspace And Action Safety

Relevant files:

- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/patch_plan.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `src/coding_agent/action_safety/approval_routing.py`
- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `tests/coding_agent/test_workspace_action_routing.py`

Current behavior:

- Sessions have an `ExecutionBinding` that routes local or provider-backed workspace behavior.
- File, patch, shell, validation, and approval policies are enforced by existing action-safety paths.
- The safe action smoke test proves patch, command, validation, and restore behavior through existing gates.

Bee implications:

- Bee nodes must not execute arbitrary commands directly from templates.
- Any node that results in command/file/patch/validation work must route through existing runtime tools and action-safety gates.
- Task manifests should describe allowed intent/profile metadata, not raw command output or privileged executor instructions.

## Context And Memory

Relevant files:

- `src/coding_agent/context_pack.py`
- `src/coding_agent/kb.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/plugins/memory.py`
- `tests/coding_agent/test_context_system_smoke.py`
- `tests/coding_agent/evaluation/`

Current behavior:

- Context packs already carry evidence with source kind/id, repo path, line range, scores, reasons, topic source metadata, and memory provenance.
- Memory remains reference evidence and not policy or system instruction.
- Tests use fake/local data and do not require real LLM calls.

Bee implications:

- Bee task manifests can request context profiles or evidence scopes, but context construction should reuse existing ContextPack and retrieval boundaries.
- Task outputs should reference evidence IDs/ranges, not raw prompt/content/message/result text.
- Future task reports should summarize validation/context provenance in sanitized form.

## Observability

Relevant files:

- `src/coding_agent/observability.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`

Current behavior:

- Tracing filters reject sensitive attributes.
- Prometheus labels are allowlisted.
- Existing forbidden labels include `run_id`, `schedule_id`, `session_id`, `signal_id`, `trace_id`, `topic_id`, `event_id`, `interaction_id`, `tool_call_id`, `file_path`, `prompt`, `message`, `content`, `command_output`, and `secret`.
- Existing low-cardinality labels include action, schedule, signal, topic, status, stage, source, and policy fields.

Bee implications:

- `task_id` and `node_id` must be added to the forbidden Prometheus label set before task metrics are introduced.
- Low-cardinality task labels can include task kind/status/profile and node kind/status/profile.
- Trace correlation may include task and node IDs only as safe trace attributes if existing no-leak rules are preserved.

## Developer Console

Relevant files:

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`

Current behavior:

- Console pages already cover sessions, runs, HITL/interactions, tape, context, memory, actions/validation, observability, topics, schedules, workspaces, and release.
- Console rendering uses safe ID/label/text helpers and does not render raw prompt/content/message/result/secret/text/command output/stdout/stderr/env from metadata.
- Route metrics use stable route labels and avoid high-cardinality IDs.

Bee implications:

- Later goals can add `/console/bee` and `/console/bee/{task_id}` or equivalent pages.
- Console should render safe task/workflow summaries, node statuses, topic linkage, run linkage, validation status, and low-cardinality policy results.
- Console must remain read-only unless a later goal explicitly proves a policy-preserving resolve/launch action.

## Existing Tests To Preserve

Regression baseline remains:

- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
- `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v`
- `uv run pytest tests/dogfood/test_local_dogfood_run.py tests/dogfood/test_workspace_provider_demo.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `git diff --check -- .`

## G94-G101 Suggested Target Shape

The user goal did not provide named subgoals, so this phase should use the following bounded sequence unless superseded by a later explicit list:

- G94: ADR for generic Bee workflow/task boundaries.
- G95: Task manifest parser and sanitizer for fixture templates.
- G96: Durable Bee task/workflow schema and store.
- G97: Topic-bound Bee lifecycle anchors and task provenance.
- G98: Deterministic task planner that creates normal launch intents without executing nodes.
- G99: Action safety/context/workspace integration metadata and no-bypass tests.
- G100: Developer Console and observability integration with safe labels and no high-cardinality metrics.
- G101: End-to-end smoke tests, usage docs, and implementation report.

## Exact Files Likely To Modify Later

Production files:

- `src/coding_agent/bee_runtime.py` or `src/coding_agent/bee.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/plugins/topic.py`
- `src/coding_agent/scheduled_runs.py`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`

Test files:

- `tests/coding_agent/test_bee_runtime.py`
- `tests/coding_agent/test_scheduled_runs.py`
- `tests/coding_agent/test_observability.py`
- `tests/ui/test_developer_console.py`

Docs:

- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/bee_runtime/GOAL_PROGRESS.md`
- `docs/bee_runtime/USAGE.md`
- `docs/bee_runtime/IMPLEMENTATION_REPORT.md`

## Blocker Check

No blocker was found in G93.

- No AgentKit Core rewrite is needed.
- No broad G00-G92 behavior change is needed.
- No production credentials or hosted services are needed.
- Docker, Kubernetes, Argo, desktop, bridge, external executor, and multi-agent work are not needed for the generic runtime foundation.
- Homelab-specific logic is not needed.
- Deterministic verification commands are available.
