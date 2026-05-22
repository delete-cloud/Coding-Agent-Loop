# Bee Launch Usage

Bee launch is the product-layer entry point for starting workspace-local Bee
tasks. It resolves a `.bee/templates/<template_id>` template, binds safe inputs,
creates or continues a Topic, creates a durable BeeTask, and writes `task.json`
when workspace artifacts are enabled.

Launch itself does not execute command intents. Bee nodes remain pending until
execution goes through the Bee command bridge, workspace binding, command policy,
action safety, validation, and HITL where required.

## Launch Sources

- Manual launch uses `BeeLaunchOrchestrator.launch_manual()`.
- Scheduled launch uses `ScheduledBeeLaunchOrchestrator.launch_due()`.
- Proactive signal launch uses `ProactiveBeeLaunchOrchestrator.launch_signal()`.

All launch sources create a `BeeLaunchRecord` with `launch_id`, `source`,
`template_id`, `status`, optional `task_id`, optional `topic_id`, and optional
schedule or signal linkage.

## Lifecycle Controls

`BeeTaskLifecycleController` provides bounded controls:

- `resume_task()` marks only incomplete ready work for continuation.
- `retry_node()` retries failed nodes and preserves prior evidence.
- `cancel_task()` records a cancelled terminal task.
- `abort_task()` records an aborted terminal task and preserves task metadata.

Lifecycle controls do not execute commands and do not bypass existing safety
policy.

## Console

The Developer Console Bee page is available at:

```text
/console/bee
```

It shows Bee tasks, node launches, workspace templates, workspace run artifacts,
command intents, and Bee launch summaries. Launch summaries include source,
status, template, task, topic, schedule, and signal links where available. Raw
prompt, message, command output, stdout, stderr, env, and secrets are not
rendered.

When PostgreSQL-backed stores are configured, launch summaries come from the
durable Bee launch store. In local fixture mode the console can still fall back
to sanitized run metadata.

## Metrics

Bee launch metrics are low cardinality:

```text
bee_launches_total{source,status}
bee_launch_duration_seconds{source,status}
scheduled_bee_launches_total{status}
proactive_bee_launches_total{kind,status}
```

Identifier labels such as `launch_id`, `task_id`, `topic_id`, `run_id`,
`session_id`, `schedule_id`, `signal_id`, and `node_id` are intentionally not
exported.

## Verification

Representative local checks:

```bash
uv run pytest tests/coding_agent/test_bee_launch.py -v
uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_bee_command_bridge.py -v
uv run pytest tests/coding_agent/test_bee_workspace.py -v
uv run pytest tests/coding_agent/test_bee_runtime.py -v
uv run pytest tests/coding_agent/test_scheduled_runs.py -v
git diff --check -- .
```

External executor, Kubernetes, Argo, nmem, desktop, bridge, and multi-agent
execution are deferred.
