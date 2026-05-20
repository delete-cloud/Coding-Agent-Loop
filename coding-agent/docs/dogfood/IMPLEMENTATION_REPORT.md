# Dogfood + Demo Readiness Implementation Report

Date: 2026-05-20

## Summary

G64-G67 completed the Dogfood + Demo Readiness phase for Coding-Agent-Loop. The
phase validated a local, credential-free dogfood path over the completed
runtime, console, observability, and release surfaces. It did not change
AgentKit Core, alter G00-G63 contracts, add schedule/sandbox/desktop/bridge,
or require production credentials.

## Landed Goals

| Goal | Result |
| --- | --- |
| G64 | Current-state map and dogfood plan landed in PR #272. |
| G65 | Local run_id-level dogfood evidence and deterministic replay test landed in PR #273. |
| G66 | Repeatable demo path landed in PR #274. |
| G67 | Final smoke verification and implementation report completed in this goal. |

## Key Artifacts

- `docs/dogfood/CURRENT_STATE.md`
- `docs/dogfood/GOAL_PROGRESS.md`
- `docs/dogfood/RUN_EVIDENCE.md`
- `docs/dogfood/DEMO_PATH.md`
- `docs/dogfood/IMPLEMENTATION_REPORT.md`
- `tests/dogfood/test_local_dogfood_run.py`

## Dogfood Evidence

G65 recorded run_id-level evidence in `docs/dogfood/RUN_EVIDENCE.md`:

- `session_id`: `3bfed77d-4d2c-4f03-977f-f7c56a2e9a04`
- `run_id`: `5c716d8294c84d99848dba9aeab0d0b5`
- provider/model: `mock` / `mock`
- run status: `completed`
- runtime event count: `13`
- message snapshot recorded: `true`
- health, readiness, sessions, runs, run detail, observability, and release
  routes returned `200` in the local evidence run.

The evidence does not include raw prompt, message, model result text, command
output, stdout, stderr, environment values, secrets, file contents, or patch
contents.

## Demo Readiness

The repeatable demo path is documented in `docs/dogfood/DEMO_PATH.md`. It
separates:

- deterministic G65 same-process replay for run-specific console evidence
- manually started HTTP console demos for shell, navigation, empty states,
  health/readiness, release, and optional local metrics
- run-specific manual demos only when the run is created in the same configured
  persistent runtime store used by the server

## Acceptance Audit

- Local dogfood can produce non-empty `session_id` and `run_id` evidence without
  production credentials or hosted services.
- The committed dogfood replay test exercises existing `SessionManager`
  runtime wiring, local execution binding, `MockProvider`, runtime records,
  message snapshot metadata, and Developer Console routes.
- The demo path is deterministic and does not pretend that in-memory test run
  IDs exist in a separately started server process.
- No high-cardinality Prometheus labels were added.
- No Langfuse/OTLP, Prometheus, Grafana, release, console, action safety,
  context system, or durable runtime semantics were changed.

## Verification

Final G67 verification is recorded in `docs/dogfood/GOAL_PROGRESS.md`.

## Remaining Risks

- The local evidence path uses the existing `MockProvider`; hosted LLM provider
  dogfood remains outside this phase because production credentials and hosted
  services are not required.
- Manual run-specific console demos require a persistent runtime store shared
  by the run creator and the HTTP server.
- The optional Prometheus/Grafana visual demo depends on the documented local
  stack being started separately.
