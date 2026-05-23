# External Executor Adapter MVP Implementation Report

## Status

G127-G135 are complete.

The platform is now executor-aware for Bee node execution:

```text
BeeLaunch
-> BeeTask
-> BeeNode
-> authorized ExecutorPlan
-> executor adapter
   -> local
   -> docker dry-run
   -> kubernetes_job dry-run
   -> argo_workflow dry-run
-> sanitized ExecutorResult
-> Bee completion evidence
-> task.json/report/evidence
-> console/metrics
```

## Landed Goals

- G127: Current state map in `docs/external_executor/CURRENT_STATE.md`.
- G128: ADR-0045 external executor adapter boundaries.
- G129: Generic executor model, registry, and durable executor run store.
- G130: Existing local safe execution normalized behind `LocalExecutorAdapter`.
- G131: Optional Docker executor capability/dry-run boundary.
- G132: Kubernetes Job dry-run/fake-status adapter.
- G133: Argo Workflow dry-run/fake-status adapter.
- G134: Console, artifacts, and Prometheus executor integration.
- G135: Final smoke tests and usage/report documentation.

## Safety Properties

- External executors execute only already-authorized Bee execution plans.
- External executors do not create Bee tasks, topics, launches, schedules, or
  completion decisions.
- Local execution still requires workspace binding, command policy, approval
  routing, validation, and sanitized evidence.
- Docker/Kubernetes/Argo adapters are optional and disabled by default.
- Normal tests do not require Docker, Kubernetes, Argo, production credentials,
  hosted services, or real LLM calls.
- Prometheus executor metrics use low-cardinality labels only.
- Artifacts, console, metrics, and traces do not store raw stdout/stderr, command
  output, env dumps, secrets, pod/job/workflow names, prompts, or raw messages.

## Verification

Final G135 scoped verification:

- `uv run pytest tests/coding_agent/test_external_executor_smoke.py -v`
- `uv run pytest tests/coding_agent/test_external_executor.py -v`
- `uv run pytest tests/coding_agent/test_bee_launch.py -v`
- `uv run pytest tests/coding_agent/test_bee_command_bridge.py -v`
- `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md docs/external_executor/USAGE.md docs/external_executor/IMPLEMENTATION_REPORT.md tests/coding_agent/test_external_executor_smoke.py`
- `uv run ruff check tests/coding_agent/test_external_executor_smoke.py`
- `git diff --check -- .`

## Deferred Work

- Real Docker/Kubernetes/Argo submission clients.
- External executor status import from real runtimes.
- Sanitized external log import.
- Homelab-specific adapters/templates.
- Argo CD, nmem, desktop, bridge, and multi-agent task graph work.
