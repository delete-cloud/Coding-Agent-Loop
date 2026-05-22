# Bee Launch Implementation Report

## Scope

G118-G126 completed the Bee Launch / Scheduled Bee Task Integration phase.
The phase adds a safe, durable, topic-aware launch surface for manual,
scheduled, and proactive Bee task creation.

The implementation stays in the Coding Agent product layer. AgentKit Core,
durable runtime semantics, topic semantics, workspace policy, action safety,
Bee command bridge behavior, and observability contracts remain unchanged.

## Landed Goals

- G118 mapped current Bee runtime, workspace contract, command bridge, schedule,
  proactive signal, console, and observability surfaces.
- G119 accepted ADR-0044 for Bee launch surfaces.
- G120 added durable Bee launch records and store behavior.
- G121 added workspace template resolution and input binding.
- G122 added manual Bee launch through the product-layer orchestrator.
- G123 added bounded resume, retry, cancel, and abort lifecycle controls.
- G124 connected schedules to Bee launch.
- G125 connected proactive signals to Bee launch.
- G126 added console launch visibility, low-cardinality launch metrics, final
  smoke coverage, and usage documentation.

## Safety Boundaries

- Launch does not execute arbitrary commands.
- Bee nodes remain evidence-gated and policy-bound through the Bee command
  bridge before execution.
- Schedules and proactive signals cannot bypass command policy, workspace
  policy, action safety, validation, approval, or HITL.
- Prometheus metrics do not use `launch_id`, `task_id`, `topic_id`, `run_id`,
  `session_id`, `schedule_id`, `signal_id`, or `node_id` labels.
- Console output avoids raw prompt, message, content, command output, stdout,
  stderr, env, and secrets.
- The Developer Console reads durable Bee launch records when a PG store is
  configured and falls back to sanitized run metadata otherwise.

## Verification

Primary verification commands for the final state:

```bash
uv run pytest tests/coding_agent/test_bee_launch.py -v
uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_bee_command_bridge.py -v
uv run pytest tests/coding_agent/test_bee_workspace.py -v
uv run pytest tests/coding_agent/test_bee_runtime.py -v
uv run pytest tests/coding_agent/test_scheduled_runs.py -v
uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v
uv run pytest tests/integration/test_durable_runtime_smoke.py -v
uv run pytest tests/coding_agent/test_context_system_smoke.py -v
uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v
uv run ruff format --check src/coding_agent/bee_launch.py src/coding_agent/observability.py src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/coding_agent/test_bee_launch.py tests/coding_agent/test_observability_platform_smoke.py tests/ui/test_developer_console.py
uv run ruff check src/coding_agent/bee_launch.py src/coding_agent/observability.py src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/coding_agent/test_bee_launch.py tests/coding_agent/test_observability_platform_smoke.py tests/ui/test_developer_console.py
git diff --check -- .
```

## Deferred

External executor adapters, Docker/Kubernetes/Argo execution, Argo CD
integration, nmem integration, homelab-specific templates, desktop app, bridge,
and multi-agent task graph remain out of scope.
