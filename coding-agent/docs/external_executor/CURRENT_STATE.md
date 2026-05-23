# External Executor Current State

G127 maps the existing Coding Agent surfaces that an External Executor Adapter
MVP can extend. This is a documentation-only goal. No production code is changed
in this goal.

## Phase Boundary

The current platform is a topic-aware, schedule-aware, workspace-aware,
launchable Bee task platform. External executor work must add product-layer
executor adapters for already-authorized Bee execution plans. It must not move
Bee, schedules, topics, workspace policy, command policy, approval, validation,
or acceptance decisions into AgentKit Core.

External executors must not create Bee tasks, topics, schedules, launches, or
completion decisions. They can only execute a sanitized and approved execution
plan and return sanitized result/evidence metadata for the existing Bee
acceptance path.

## Bee Launch And Task State

Primary files:

- `src/coding_agent/bee_launch.py`
- `src/coding_agent/bee_runtime.py`
- `tests/coding_agent/test_bee_launch.py`
- `tests/coding_agent/test_bee_runtime.py`

Current behavior:

- `BeeLaunchRequest` is the manual/schedule/proactive launch input.
- `BeeLaunchOrchestrator` resolves templates, creates or continues Topics,
  creates `BeeTaskRecord` and `BeeNodeRecord` rows, optionally writes sanitized
  `.bee/runs/<task_id>/task.json`, and records low-cardinality launch metrics.
- `ScheduledBeeLaunchOrchestrator` and `ProactiveBeeLaunchOrchestrator` reuse
  the same launch flow and link schedule/signal trigger metadata.
- `BeeTaskLifecycleController` supports bounded resume, retry, cancel, and
  abort controls.
- `BeeTaskRecord` and `BeeNodeRecord` are durable product records. They already
  reject raw prompt/content/message/result/secret/text/command output/stdout/
  stderr/env metadata.
- `BeeNodeManifest` carries safe intent metadata such as `node_id`, `kind`,
  `profile`, `context_profile`, `validation_profile`, and optional
  `command_ref`. It explicitly rejects executable keys such as `command`,
  `commands`, `cmd`, `args`, `argv`, `shell`, `script`, `exec`, and `executor`.

Later executor work should add executor run records and node execution evidence
beside these existing records, not redefine task or node lifecycle.

## Bee Command Bridge And Local Safe Execution

Primary files:

- `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/approval_routing.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `tests/coding_agent/test_bee_command_bridge.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`

Current behavior:

- `resolve_bee_command_intent()` resolves a Bee node `command_ref` to a
  workspace-declared `BeeWorkspaceCommandIntent`.
- `plan_bee_command_intent()` accepts an explicit caller-supplied command
  candidate, evaluates it through `evaluate_command_policy()`, routes approval
  through `route_command_action()`, and returns a non-executing
  `BeeCommandIntentPlan`.
- `commands.yaml` describes command intent metadata only. It does not carry raw
  executable command strings and does not grant execution rights.
- `run_bee_validation_node()` is the only current bridge path that executes. It
  is limited to validation nodes and delegates to `ValidationRunner`.
- `ValidationRunner` currently supports local execution only. It reruns command
  policy with `validation_command=True`, rejects denied or approval-required
  commands, executes with `subprocess.run()` only for local validation commands,
  and stores safe failure summaries using output sizes or parsed summaries
  rather than raw stdout/stderr.
- `complete_bee_node_from_bridge_result()` requires accepted/passed evidence.
  A node cannot complete from model text alone.

There is no generic `ExternalExecutor`, `ExecutorPlan`, `ExecutorRunRecord`, or
executor registry yet.

## Workspace Lease And Provider State

Primary files:

- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/workspace_store.py`
- `tests/coding_agent/test_workspace_action_routing.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/coding_agent/environment/test_docker_workspace_provider_transfer.py`
- `tests/dogfood/test_workspace_provider_demo.py`

Current behavior:

- Workspace provider code models local/cloud workspace binding, lifecycle,
  archive, diff, patch, publication, and capability reporting.
- Docker support exists as a workspace provider implementation. It manages
  workspace lifecycle and remote command/file operations for cloud workspace
  bindings; it is not a Bee node executor adapter.
- Docker provider tests use fakes and capability checks. Docker is not required
  for normal deterministic tests.
- Workspace identifiers may appear in durable records and console routes, but
  must not become Prometheus labels.

External executor adapters should consume an already-bound workspace reference
or lease-like object from product-layer code and must not bypass workspace/path
policy.

## Workspace Artifacts, Reports, And Evidence

Primary files:

- `src/coding_agent/bee_workspace.py`
- `tests/coding_agent/test_bee_workspace.py`
- `docs/bee_workspace/USAGE.md`

Current behavior:

- `.bee/templates/<template_id>/` discovery loads `metadata.yaml` or
  `metadata.json`, `SKILL.md`, `features/*.feature`, and optional
  `commands.yaml`.
- `build_bee_manifest_from_workspace_template()` converts workspace templates
  into the existing sanitized Bee manifest shape.
- `write_bee_workspace_run_artifacts()` writes sanitized `task.json`,
  `report.md`, `evidence/`, and optional `memory_candidates.yaml`.
- `task.json` is a non-authoritative mirror of durable state. It currently
  records task/template/topic/status, node statuses, attempts, run IDs, action
  IDs, validation IDs, and report path.
- Artifact validation rejects forbidden keys and markers for prompt/content/
  message/result/secret/text/command_output/stdout/stderr/env and executable
  command fields.

G134 can extend artifact payloads with safe executor fields such as
`executor_run_id` and `executor_kind`. It must not write raw logs, raw command
output, env dumps, pod names, job names, workflow names, or secrets.

## Node Acceptance Rules

Primary files:

- `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/bee_runtime.py`
- `tests/coding_agent/test_bee_command_bridge.py`

Current behavior:

- Completion evidence is represented by `BeeNodeCompletionEvidence`.
- Supported evidence kinds are currently `action_record`, `sanitized_artifact`,
  and `validation_report`.
- Evidence refs are hashed in safe dictionaries.
- `complete_bee_node_from_bridge_result()` returns `evidence_required` unless
  there is accepted/passed evidence.

Executor results should become sanitized evidence metadata consumed by the same
acceptance path. External executor status alone must not mark a Bee node
complete.

## Developer Console

Primary files:

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`

Current behavior:

- `/console/bee` renders Bee task summaries, node launch metadata, workspace
  templates, workspace run artifacts, command intents, and durable Bee launch
  summaries.
- Bee launch summaries are loaded from `PGBeeLaunchStore` when configured and
  fall back to sanitized run metadata.
- Workspace-backed Bee artifact sections are visible only to admin/local
  console contexts.
- Console tests seed forbidden raw text sentinels and assert they are not
  rendered.
- HTTP metrics use route labels such as `console_bee`, not high-cardinality
  IDs.

G134 can add executor run rows to the existing Bee page or a closely related
console section. The console should show executor kind, status, capability, safe
summary, and links to Bee task/node/launch/topic identifiers where existing
privacy contracts allow them.

## Observability

Primary files:

- `src/coding_agent/observability.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`
- `docs/observability/IMPLEMENTATION_REPORT.md`

Current behavior:

- Langfuse/OTLP tracing and Prometheus metrics are additive through
  `CompositeObservationSink`.
- Prometheus metrics are fail-open and label allowlisted.
- Forbidden Prometheus labels already include `run_id`, `session_id`,
  `signal_id`, `schedule_id`, `template_id`, `task_id`, `topic_id`, `node_id`,
  `trace_id`, file path, prompt/content/message/command output, and secret
  labels.
- Bee metrics currently allow only low-cardinality task/node/template/command/
  topic/launch labels.
- Launch metrics are:
  - `bee_launches_total{source,status}`
  - `bee_launch_duration_seconds{source,status}`
  - `scheduled_bee_launches_total{status}`
  - `proactive_bee_launches_total{kind,status}`

Executor metrics should add only low-cardinality labels such as
`executor_kind`, `status`, and capability status. Prometheus must not use
`executor_run_id`, `launch_id`, `task_id`, `topic_id`, `run_id`, `session_id`,
`node_id`, `pod_name`, `job_name`, `workflow_name`, file path, command, prompt,
content, or secret labels.

Trace attributes may carry safe correlation identifiers where existing
contracts permit them, but must not contain raw logs, env, stdout/stderr,
prompt, message, content, result, or secrets.

## Exact Files And Functions Likely To Modify Later

Likely new module and tests:

- `src/coding_agent/external_executor.py`
- `tests/coding_agent/test_external_executor.py`
- `docs/external_executor/ADR_EXTERNAL_EXECUTOR.md` or an ADR under
  `docs/adr/`

Likely existing files:

- `src/coding_agent/bee_command_bridge.py`
  - convert approved `BeeCommandIntentPlan` into an `ExecutorPlan`
  - connect executor results to `BeeNodeCompletionEvidence`
- `src/coding_agent/bee_workspace.py`
  - add safe executor fields to `BeeWorkspaceRunArtifacts` and `task.json`
  - write sanitized executor evidence/report references
- `src/coding_agent/observability.py`
  - add executor metrics with low-cardinality labels only
- `src/coding_agent/ui/developer_console.py`
  - add console dataclasses and rendering sections for executor runs
- `src/coding_agent/ui/http_server.py`
  - load executor run summaries from a store and render them on console pages
- `src/coding_agent/environment/docker_workspace_provider.py`
  - reuse capability patterns or fake-client ideas for optional Docker executor
    boundaries, without making Docker required

Existing regression tests to preserve:

- `tests/coding_agent/test_bee_launch.py`
- `tests/coding_agent/test_bee_command_bridge.py`
- `tests/coding_agent/test_bee_workspace.py`
- `tests/coding_agent/test_bee_runtime.py`
- `tests/coding_agent/test_scheduled_runs.py`
- `tests/coding_agent/test_topic_layer_smoke.py`
- `tests/ui/test_developer_console.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`
- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `tests/dogfood/test_workspace_provider_demo.py`
- release verification commands from
  `docs/release_hardening/release-verification.yaml`

## Out Of Scope

This phase must not implement or require:

- production Kubernetes
- production Argo Workflows
- Argo CD integration
- nmem integration
- homelab-specific templates or infrastructure logic
- multi-agent task graph execution
- desktop app or bridge app
- production credentials or hosted services
- real Docker/Kubernetes/Argo availability for normal tests

Docker, Kubernetes Job, and Argo Workflow adapters should use disabled-by-
default behavior, fake clients, dry-run rendering, capability detection, and
skip-if-unavailable optional smokes.
