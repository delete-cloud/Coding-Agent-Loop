# Dogfood Demo Path

Date: 2026-05-20

## Purpose

This checklist demonstrates the completed G00-G63 platform locally using the
G65 dogfood path. It is for demo readiness only. It does not add a new feature,
change runtime semantics, or require production credentials.

## Preconditions

- Run commands from `coding-agent/`.
- Install dependencies with `uv sync --all-extras` if the environment is not
  already synced.
- No production credentials are required.
- No external hosted service is required.
- Optional local Prometheus/Grafana setup is documented in
  `docs/observability/LOCAL_STACK.md`.
- Do not paste or record raw task prompts, messages, model result text, command
  output, stdout, stderr, environment values, secrets, file contents, or patch
  contents in demo notes or screenshots.

## Demo Checklist

### 1. Replay G65 Evidence

This is the G65 evidence replay step.

Run the deterministic local dogfood smoke:

```bash
uv run pytest tests/dogfood/test_local_dogfood_run.py -v
```

Expected safe signal:

- the test passes
- a local run is created with a non-empty `session_id`
- a local run is created with a non-empty `run_id`
- run status reaches `completed`
- message snapshot metadata is available
- `/healthz` and `/readyz` return `200`
- console routes for sessions, runs, run detail, observability, and release
  render without forbidden raw text

The one-time G65 evidence sample is recorded in
`docs/dogfood/RUN_EVIDENCE.md`.

This replay runs the console and runtime store in the same test process. The
sample `run_id` in `RUN_EVIDENCE.md` is evidence, not a durable identifier that
will exist in a separately started HTTP server process.

### 2. Start The Read-Only Console

Start the HTTP server:

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/console
```

Expected safe signal:

- the console shell loads
- navigation includes Sessions, Runs, HITL / Interactions, Tape, Context,
  Memory, Actions / Validation, Observability, and Release / Health
- console pages are read-only and do not bypass approval or action policy
- when the server has no persisted runtime store, pages render safe empty
  states instead of run-specific data

### 3. Inspect Sessions And Runs

Open:

```text
http://127.0.0.1:8080/console/sessions
http://127.0.0.1:8080/console/runs
```

Expected safe signal:

- session rows show safe IDs, timestamps, status, provider, model, and
  execution binding metadata when available
- run rows show safe IDs, status, timestamps, and sanitized error summary when
  available
- empty states render cleanly when no durable data exists

### 4. Inspect Run Detail In The G65 Replay

Run detail is deterministic in the G65 evidence replay, where the generated
run and the console share the same in-memory runtime store:

```bash
uv run pytest tests/dogfood/test_local_dogfood_run.py -v
```

Inside that replay, the console exercises:

```text
/console/runs/{run_id}
/console/observability?run_id={run_id}
```

Expected safe signal:

- run metadata renders
- message snapshot metadata renders
- runtime event metadata renders in replay order
- links to context, actions, observability, and related views are visible when
  data is available
- raw prompt, message, model result text, command output, stdout, stderr,
  environment values, and secrets do not render

For a manually started server, use `/console/runs/{run_id}` only with a run ID
that was created in the same configured persistent runtime store used by that
server.

### 5. Inspect HITL, Tape, Context, Memory, And Actions

Open the relevant pages:

```text
http://127.0.0.1:8080/console/interactions
http://127.0.0.1:8080/console/tape
http://127.0.0.1:8080/console/context?run_id={run_id}
http://127.0.0.1:8080/console/memory?run_id={run_id}
http://127.0.0.1:8080/console/actions?run_id={run_id}
```

Expected safe signal:

- HITL page separates pending and resolved interactions when present
- tape page shows tape metadata/search results when a debug store is available
- context page shows evidence metadata such as reason, safe source path, line
  range, score, and confidence when present
- memory page shows evidence metadata when present
- actions page shows action, policy, patch-summary, and validation metadata
  when present
- missing data produces read-only empty states
- run-scoped pages require a run ID visible to the same server/runtime store

### 6. Inspect Observability And Release

Open:

```text
http://127.0.0.1:8080/console/observability?run_id={run_id}
http://127.0.0.1:8080/console/release
```

Expected safe signal:

- trace correlation metadata renders with safe IDs and without credentials
- Langfuse and Grafana links appear only when configured as safe URLs
- local/no-Langfuse/no-Grafana mode degrades gracefully
- release page shows health, readiness, and release verification manifest gates

### 7. Optional Metrics And Local Stack

Enable the local metrics endpoint using the config shape in
`docs/observability/LOCAL_STACK.md`, then start the server and check:

```bash
curl -fsS http://127.0.0.1:8080/metrics
```

Expected safe signal:

- Prometheus text exposition is returned when enabled
- metrics do not use high-cardinality labels such as `run_id`, `session_id`,
  `trace_id`, `file_path`, `tool_call_id`, prompt, message, content,
  command output, or secret

Start local Prometheus/Grafana only when needed for a visual demo:

```bash
cd docs/observability/local
docker compose up
```

Prometheus runs at `http://127.0.0.1:9090`. Grafana runs at
`http://127.0.0.1:3000`.

## Verification Commands

Run from `coding-agent/`:

```bash
uv run pytest tests/dogfood/test_local_dogfood_run.py -v
uv run pytest tests/ui/test_developer_console.py -v
git diff --check -- .
```

## Demo Boundaries

- No production credentials.
- No external hosted services.
- No raw sensitive content in docs, rendered console pages, screenshots, trace
  attributes, metrics, or durable evidence.
- No schedules, sandbox, desktop, bridge, proactive-agent, or multi-agent task
  graph work.
- No changes to AgentKit Core or G00-G63 contracts.
