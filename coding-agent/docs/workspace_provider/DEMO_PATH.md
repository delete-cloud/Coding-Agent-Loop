# Workspace Provider Demo Path

Date: 2026-05-21

## Purpose

This checklist demonstrates the Workspace Provider / Sandbox MVP phase without
requiring Docker, production credentials, hosted services, schedules, desktop,
bridge, proactive-agent, or multi-agent task graph work.

## Preconditions

- Run commands from `coding-agent/`.
- Install dependencies with `uv sync --all-extras` if needed.
- Do not record raw prompt, message, model result text, command output, stdout,
  stderr, environment values, secrets, file contents, or patch contents in demo
  notes or screenshots.
- Treat Docker as optional. The deterministic replay uses a fake workspace
  provider/client so it can run on machines without a Docker daemon.

## Demo Checklist

### 1. Replay G75 Workspace Evidence

Run:

```bash
uv run pytest tests/dogfood/test_workspace_provider_demo.py -v
```

Expected safe signal:

- a cloud execution binding is created with workspace provider metadata
- durable workspace metadata is recorded
- a local mock run produces a non-empty `session_id` and `run_id`
- selected workspace tools route through the fake workspace client
- a secret sentinel is present in non-rendered workspace metadata and remains
  absent from rendered console pages
- `/console/workspaces`, `/console/sessions`, `/console/runs`,
  `/console/runs/{run_id}`, and `/console/observability?run_id={run_id}` render
  in the same ASGI process
- rendered console pages do not include forbidden raw content
- Prometheus HTTP metrics use a stable route label and do not contain the
  workspace id or a `workspace_id` label

The one-time G75 evidence sample is recorded in
`docs/workspace_provider/RUN_EVIDENCE.md`.

### 2. Inspect The Read-Only Console Shell

Start the HTTP server when you want a manual empty-state demo:

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/console/workspaces
```

Expected safe signal:

- the Workspaces page is present in Developer Console navigation
- local/no-provider mode degrades to safe empty states
- configured provider capability status renders without credentials
- workspace inventory renders only when the server has a configured provider or
  durable workspace metadata store

### 3. Optional Docker Demo

Use Docker only when the local machine already has Docker available and the
operator explicitly wants to demonstrate the concrete Docker provider. Keep the
deterministic G75 replay as the required proof.

Recommended optional checks:

```bash
uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k "capabilities or readiness" -v
uv run pytest tests/ui/test_http_server.py -k "durable_cloud_workspace_gc or local_durable_record or foreign_provider_instance" -v
```

Expected safe signal:

- Docker capability reporting uses low-cardinality ready/unavailable reasons
- lifecycle routes fail closed for foreign provider instances
- missing provider workspaces do not strand durable records in `cleaning`

### 4. Final Smoke Inputs For G76

Before the final report, rerun the practical workspace-provider checks:

```bash
uv run pytest tests/dogfood/test_workspace_provider_demo.py -v
uv run pytest tests/dogfood/test_local_dogfood_run.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_workspace_action_routing.py -v
git diff --check -- .
```

## Boundaries

- No production credentials.
- No external hosted services.
- No Docker requirement for the required demo replay.
- No raw sensitive content in docs, rendered console pages, screenshots, traces,
  metrics, or durable evidence.
- No high-cardinality Prometheus labels such as `run_id`, `session_id`,
  `workspace_id`, `file_path`, `command`, prompt, content, or secret.
- No schedules, desktop, bridge, proactive-agent, or multi-agent task graph.
- No changes to AgentKit Core or G00-G74 contracts.
