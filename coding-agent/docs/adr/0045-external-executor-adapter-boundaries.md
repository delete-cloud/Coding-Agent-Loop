# ADR-0045: Define external executor adapter boundaries

**Status**: Accepted
**Date**: 2026-05-23

## Context

ADR-0041 defines Bee as a generic Coding Agent task/workflow profile over Topic.
ADR-0042 defines workspace-local `.bee` templates and sanitized run artifacts.
ADR-0043 defines the Bee command bridge and evidence-gated completion. ADR-0044
defines manual, scheduled, and proactive Bee launch.

The next gap is external execution. Bee nodes can already be launched and routed
through local command/validation safety paths, but there is no generic adapter
boundary for optional Docker, Kubernetes Job, or Argo Workflow execution. The
risk is letting an external executor become a second workflow engine that
creates tasks, bypasses policies, leaks logs, or decides node completion.

## Decision

Keep external executor adapters in `src/coding_agent/` as product-layer runtime
code. AgentKit Core remains generic and must not gain Bee-specific or
infrastructure-specific executor primitives.

Define `ExecutorKind`:

- `local`
- `docker`
- `kubernetes_job`
- `argo_workflow`
- `fixture`

Define `ExecutorCapability`:

- Describes whether a kind is enabled and available.
- Uses low-cardinality status/reason values.
- Does not include credentials, kubeconfig, hostnames, pod names, job names,
  workflow names, env dumps, or raw client errors.

Define `ExecutorPlan`:

- Produced only from an approved Bee command/action/validation plan.
- Contains safe task/node/launch/topic correlation IDs, executor kind, workspace
  reference, command category/profile, timeout, validation mode, and safe
  metadata.
- Does not contain raw prompt/content/message/result/secret/text, raw stdout/
  stderr/env, or raw log bodies.
- Does not read executable command strings from `.bee` templates,
  `commands.yaml`, schedules, signals, or executor specs. Any command candidate
  must still pass the existing Bee command bridge and command policy path.

Define `ExternalExecutor`:

- Executes an already-authorized `ExecutorPlan`.
- Reports capability.
- Submits or runs work.
- Imports status through the adapter-specific client.
- Returns `ExecutorResult` and `ExecutorEvidence`.
- Fails closed for unsupported, disabled, denied, missing workspace, or
  approval-required plans.
- Fails open for observability export failures only; runtime safety failures are
  not swallowed.

Define `ExecutorRegistry`:

- Registers executor implementations by `ExecutorKind`.
- Resolves known executors.
- Rejects unknown kinds.
- Keeps Docker, Kubernetes Job, and Argo Workflow adapters disabled by default.

Define `ExecutorRun` / `ExecutorRunRecord`:

- Durable product record for one execution attempt.
- Stores executor run ID, executor kind, task ID, node ID, optional launch/topic
  IDs, status, timestamps, safe error type/summary, sanitized summary, and safe
  metadata.
- May reference executor run IDs in durable records, task artifacts, console
  routes, and safe trace correlation attributes.
- Must not use executor run IDs as Prometheus labels.

Define `ExecutorResult`:

- Contains status, exit-like classification where applicable, duration,
  sanitized summary, and safe evidence references.
- Does not contain raw logs, stdout, stderr, env, prompt, content, messages,
  result text, secrets, kubeconfig, tokens, pod names, job names, or workflow
  names.

Define `ExecutorEvidence`:

- Evidence metadata that can feed existing Bee acceptance logic.
- Evidence can reference sanitized artifacts, validation reports, or action
  records.
- Executor evidence does not by itself complete a Bee node; Bee acceptance rules
  still decide completion.

Define `ExecutorLogSanitizer`:

- Normalizes external logs/status output into bounded safe summaries.
- Drops or hashes sensitive raw references.
- Rejects or redacts forbidden fields before durable records, reports, evidence,
  traces, metrics, or console pages can see them.

Define `ExecutorStatusImporter`:

- Imports adapter-specific status from fake/local clients in tests.
- Maps external status into low-cardinality executor statuses:
  `planned`, `submitted`, `running`, `succeeded`, `failed`, and `cancelled`.
- Does not expose raw Kubernetes or Argo object names as metrics labels or
  durable summaries.

External executors do not create Bee tasks, Topics, schedules, or launches. They
do not decide Bee node completion. Bee launch, task lifecycle, node acceptance,
workspace policy, command policy, HITL, validation, artifacts, console, metrics,
and tracing remain Coding Agent product-layer responsibilities.

Docker, Kubernetes Job, and Argo Workflow adapters are optional and disabled by
default. Normal tests must use fake clients, dry-run rendering, capability
detection, or skip-if-unavailable behavior. Production credentials, production
clusters, hosted services, Docker availability, Kubernetes availability, Argo
availability, and real LLM calls are not required for normal tests.

Observability boundaries:

- Metrics may use low-cardinality labels such as `executor_kind`, `status`, and
  capability status.
- Metrics must not use `executor_id`, `executor_run_id`, `launch_id`,
  `task_id`, `topic_id`, `run_id`, `session_id`, `node_id`, file path, command,
  prompt, content, `pod_name`, `job_name`, `workflow_name`, or secret labels.
- Traces may carry safe correlation IDs where existing privacy contracts permit
  them, but must not contain raw logs, stdout, stderr, env, prompts, messages,
  result text, content, or secrets.

Console and artifact boundaries:

- Console may show executor kind, status, capability status, sanitized summary,
  safe error summary, and links to existing Bee task/node/launch/topic records.
- Console must not render raw logs, raw command output, stdout, stderr, env,
  prompt, content, messages, result text, kubeconfig, tokens, or secrets.
- `task.json`, reports, and evidence may include safe executor IDs/kinds/status
  and sanitized evidence references. They must not include raw external logs or
  production credentials.

Argo CD remains out of scope. Argo Workflow dry-run rendering is not Argo CD
integration and must not create or apply Argo CD Application manifests.

## Alternatives Rejected

- Let external executors create Bee tasks or decide node completion. Rejected
  because Bee launch/task/acceptance semantics must remain inside Coding Agent.
- Execute command strings from executor specs. Rejected because execution must
  start from an already-authorized Bee command bridge plan.
- Require Docker/Kubernetes/Argo for normal tests. Rejected because this phase
  must be deterministic and credential-free.
- Store raw executor logs for debugging. Rejected because it violates the
  no-leak contract and would leak stdout/stderr/env or infrastructure details.
- Put executor primitives in AgentKit Core. Rejected because these adapters are
  Coding Agent product integrations over Bee, workspace, action safety, and
  observability.
- Integrate Argo CD now. Rejected because GitOps reconciliation is a separate
  integration concern and not required for a generic executor adapter MVP.
- Hard-code homelab templates or NetBird/OCI/nmem behavior. Rejected because the
  adapter boundary must stay generic.

## Acceptance Criteria

- [ ] `test_executor_registry_resolves_known_and_rejects_unknown_kind`
- [ ] `test_executor_run_store_schema_is_idempotent`
- [ ] `test_executor_run_store_create_update_attach_and_list`
- [ ] `test_local_executor_runs_approved_plan_and_records_sanitized_result`
- [ ] `test_local_executor_rejects_denied_or_approval_required_plan`
- [ ] `test_docker_executor_capability_detection_and_dry_run`
- [ ] `test_kubernetes_job_executor_renders_sanitized_dry_run_spec`
- [ ] `test_kubernetes_job_executor_imports_fake_status_safely`
- [ ] `test_argo_workflow_executor_renders_sanitized_dry_run_spec`
- [ ] `test_argo_workflow_executor_imports_fake_status_safely`
- [ ] `test_console_executor_runs_render_safe_summary`
- [ ] `test_executor_metrics_omit_high_cardinality_labels`
- [ ] `test_external_executor_e2e_smoke`
- [ ] `uv run pytest tests/coding_agent/test_external_executor.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `uv run pytest tests/coding_agent/test_observability.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/external_executor/CURRENT_STATE.md`
- `docs/external_executor/GOAL_PROGRESS.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/adr/0042-bee-workspace-contract.md`
- `docs/adr/0043-bee-command-bridge.md`
- `docs/adr/0044-bee-launch-surfaces.md`
- `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/bee_launch.py`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
