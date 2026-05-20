# Dogfood Run Evidence

Date: 2026-05-20

## G65 Local Run

This evidence records a local dogfood execution that used existing
`SessionManager` runtime wiring, the local execution binding, the existing
`MockProvider`, an in-memory runtime store, and existing Developer Console HTTP
routes through ASGI. It did not require production credentials, hosted services,
or real external LLM calls.

The executed task text and model response text are intentionally not recorded.

## Command

Run from `coding-agent/`:

```bash
uv run pytest tests/dogfood/test_local_dogfood_run.py -v
```

The committed regression guard replays the same sanitized local execution path.
A temporary local harness was used only to print the one-time run identifiers
listed below.

## Sanitized Evidence

- `session_id`: `3bfed77d-4d2c-4f03-977f-f7c56a2e9a04`
- `run_id`: `5c716d8294c84d99848dba9aeab0d0b5`
- provider: `mock`
- model: `mock`
- run status: `completed`
- runtime event count: `13`
- message snapshot recorded: `true`
- result keys:
  - `steps_taken`
  - `stop_reason`
- route status:
  - `/healthz`: `200`
  - `/readyz`: `200`
  - `/console/sessions`: `200`
  - `/console/runs`: `200`
  - `/console/runs/{run_id}`: `200`
  - `/console/observability?run_id={run_id}`: `200`
  - `/console/release`: `200`

## Safety Notes

- No production credentials were used.
- No hosted services were required.
- The evidence above does not include raw prompt, message, model result text,
  command output, stdout, stderr, environment values, secrets, file contents, or
  patch contents.
- Prometheus labels were not changed.
- AgentKit Core and G00-G63 behavior were not changed.
