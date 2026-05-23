# External Executor Usage

External executors are Coding Agent product-layer adapters for already-authorized
Bee node execution plans. They do not create Bee tasks, topics, launches, or
completion decisions.

## Execution Flow

1. Resolve a Bee workspace command intent through the Bee command bridge.
2. Build a local `ExecutorPlan` from a signed, ready, allow/allow command plan.
3. Optionally derive a Docker, Kubernetes Job, or Argo Workflow dry-run plan from
   that signed local plan.
4. Submit only through an adapter that preserves workspace, command policy, HITL,
   validation, and no-leak contracts.
5. Convert sanitized `ExecutorResult.evidence` to Bee completion evidence.
6. Let Bee acceptance rules decide whether the node completes.

## Adapter Status

- `local`: normalized local safe executor interface.
- `docker`: optional, disabled by default, dry-run/capability boundary only.
- `kubernetes_job`: optional, disabled by default, dry-run/fake-status boundary only.
- `argo_workflow`: optional, disabled by default, dry-run/fake-status boundary only.

Normal tests do not require Docker, Kubernetes, Argo Workflows, Argo CLI, kubectl,
production credentials, or hosted services.

## Safe Artifacts

Workspace `.bee/runs/<task_id>/task.json` can mirror safe executor fields:

- `executor_run_id`
- `executor_kind`
- `executor_status`
- `executor_summary`
- `executor_evidence_path`

Reports and evidence files may include sanitized executor summaries only. Raw
stdout/stderr, command output, env dumps, pod names, job names, workflow names,
secrets, prompts, messages, and raw status payloads must not be written.

## Console And Metrics

The Developer Console Bee page shows executor run summaries with safe
task/node/launch/topic links. Prometheus metrics use low-cardinality labels only:

- `executor_runs_total{executor_kind,status}`
- `executor_run_duration_seconds{executor_kind,status}`
- `executor_capability_status{executor_kind,status}`

Prometheus must not label by executor run ID, task ID, node ID, launch ID, topic
ID, run ID, session ID, pod name, job name, workflow name, command, file path,
prompt, content, output, or secret.

## Verification

```bash
uv run pytest tests/coding_agent/test_external_executor_smoke.py -v
uv run pytest tests/coding_agent/test_external_executor.py -v
uv run pytest tests/coding_agent/test_bee_workspace.py -v
uv run pytest tests/coding_agent/test_observability.py -v
uv run pytest tests/ui/test_developer_console.py -v
```
