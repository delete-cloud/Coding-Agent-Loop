# Dogfood + Demo Readiness Current State

Date: 2026-05-20

## Purpose

G64 maps the surfaces that can support dogfooding and a local demo. This goal
does not change production code. Later goals should use this map to collect
run_id-level evidence, document a repeatable demo, and run final smoke checks.

## Completed Platform Baseline

- Durable runtime G00-G11 is complete.
- Context System + Evaluation G12-G24 is complete.
- Action Safety + Workspace Execution G25-G37 is complete.
- Release Hardening + Contract Stabilization G38-G45 is complete.
- Observability Platform G46-G53 is complete.
- Developer Console / Debug UI G54-G63 is complete.

The current phase is readiness and validation work. It should not change the
semantics of any completed phase.

## Local Runtime Entrypoints

- Dev/testkit one-shot compatibility: `uv run python -m coding_agent run --goal "..." --repo .`
- REPL mode: `uv run python -m coding_agent repl`
- Local daemon control plane: `uv run python -m coding_agent daemon --host 127.0.0.1 --port 8080`
- HTTP server: `uv run python -m coding_agent serve --host 127.0.0.1 --port 8080`
- HTTP session creation: `POST /sessions`
- HTTP prompt streaming: `POST /sessions/{session_id}/prompt`
- Durable run APIs:
  - `GET /runs/{run_id}`
  - `GET /runs/{run_id}/message-snapshot`
  - `GET /runs/{run_id}/events`

The HTTP path is the best dogfood candidate because it exercises the runtime,
durable records, console, health, and metrics surfaces in one local flow.
The `run` command remains useful for task packets and quick checks, but it is
not the target local product path.
`daemon` is the local product entrypoint for a foreground control plane; it
does not yet provide background lifecycle, IPC socket transport, or daemon
client pairing.

## Developer Console Surfaces

The completed console is read-only and served by the existing FastAPI app.
Relevant pages:

- `/console`
- `/console/sessions`
- `/console/runs`
- `/console/runs/{run_id}`
- `/console/interactions`
- `/console/tape`
- `/console/context?run_id=...`
- `/console/memory?run_id=...`
- `/console/actions?run_id=...`
- `/console/observability?run_id=...`
- `/console/release`

The console intentionally renders sanitized IDs, statuses, timestamps, counts,
safe labels, safe source paths, and release commands. It must not expose raw
prompt, message, model result, command output, stdout, stderr, env, secrets,
patch contents, or credential-bearing URLs.

## Observability And Release Surfaces

- Liveness endpoint: `GET /healthz`
- Readiness endpoint: `GET /readyz`
- Metrics endpoint: `GET /metrics` when local Prometheus metrics are enabled
- Local stack docs: `docs/observability/LOCAL_STACK.md`
- Local Prometheus/Grafana config: `docs/observability/local/`
- Release verification manifest:
  `docs/release_hardening/release-verification.yaml`

Prometheus metrics must keep low-cardinality labels and must not include
`run_id`, `session_id`, `trace_id`, `event_id`, `interaction_id`,
`tool_call_id`, `file_path`, prompt/message/content/result text,
`command_output`, or `secret` values.

## Existing Deterministic Checks

Regression checks relevant to this phase:

Run these commands from `coding-agent/`.

- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
- release verification gates listed in
  `docs/release_hardening/release-verification.yaml`, executed as the exact
  `command` entries in that manifest
- scoped ruff/check for touched files
- `git diff --check -- .`

These checks are deterministic and do not require production credentials or
external hosted services.

## Dogfood Evidence Requirements

G65 must record actual run_id-level evidence for real local execution, or a
blocker report if a real run cannot be performed locally. Acceptable evidence
should include:

- the local command or HTTP route used, without raw task prompt text
- generated `session_id` and `run_id`
- run status and timestamps
- sanitized console/API paths that show the run
- health/readiness/metrics availability
- validation commands and results

Evidence must avoid raw prompt, content, message, result text, secret, command
output, stdout, stderr, env, and unsafe trace attributes.

## Candidate Dogfood Flow For G65

1. Start the Coding Agent HTTP server locally.
2. Create a session using local repo path and a non-production provider
   configuration that does not require hosted credentials.
3. Execute a minimal repo-local dogfood task.
4. Capture `session_id` and `run_id` from durable runtime APIs or console data.
5. Verify:
   - `/console/sessions`
   - `/console/runs`
   - `/console/runs/{run_id}`
   - `/console/observability?run_id={run_id}`
   - `/console/release`
   - `/healthz`
   - `/readyz`
   - `/metrics` when enabled
6. Record only sanitized IDs, statuses, timestamps, route paths, and command
   names in `docs/dogfood/RUN_EVIDENCE.md`.

If the current public CLI/HTTP surface cannot run without external LLM
credentials, G65 should record a blocker report instead of marking dogfood
complete.

## Demo Path To Harden In G66

The repeatable demo should show:

1. Start the local server.
2. Create or select a local run.
3. Open `/console`.
4. Inspect sessions and runs.
5. Open a run detail page.
6. Inspect runtime event metadata and message snapshot metadata.
7. Inspect HITL, tape, context, memory, action, and validation pages when data
   exists.
8. Inspect observability correlation and release verification pages.
9. Confirm health/readiness and metrics endpoint behavior.

G66 should turn this into a clear checklist with exact local commands and
expected safe outputs.

## Files Likely To Change Later

Expected documentation-only targets:

- `docs/dogfood/GOAL_PROGRESS.md`
- `docs/dogfood/RUN_EVIDENCE.md`
- `docs/dogfood/DEMO_PATH.md`
- `docs/dogfood/IMPLEMENTATION_REPORT.md`

Production code should remain unchanged unless G65 finds a small, scoped bug
that prevents local dogfood from exercising an existing completed surface.

## Current Assessment

The repository has enough completed local surfaces to define a deterministic
dogfood and demo plan. The unresolved risk is whether real local agent
execution can be completed without external LLM credentials. That must be
validated in G65 with run_id-level evidence or a blocker report.
