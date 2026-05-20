# Workspace Provider Dogfood Evidence

Date: 2026-05-21

## G75 Local Workspace Provider Run

This evidence records a local, credential-free workspace provider dogfood path.
It used an explicit `CloudWorkspaceBinding`, `DefaultBindingResolver`, a fake
cloud workspace client, an in-memory runtime store, durable workspace metadata,
and existing Developer Console routes through ASGI.

The run did not require Docker, production credentials, hosted services, or real
external LLM calls. The executed task text and model response text are
intentionally not recorded in this committed evidence or rendered console
pages; the in-memory runtime store still records sanitized message snapshot
metadata as part of the runtime path under test.

## Command

Run from `coding-agent/`:

```bash
uv run pytest tests/dogfood/test_workspace_provider_demo.py -v
```

The committed regression guard replays the same sanitized workspace provider
path. A temporary local harness was used only to print the one-time identifiers
listed below.

## Sanitized Evidence

- `session_id`: `9ba40953-dfdf-403c-b6dc-5e72fc62fbf1`
- `run_id`: `3ad60c7953a74d84a63adc217286212a`
- `workspace_id`: `ws-dogfood`
- workspace provider: `docker`
- provider instance: `dogfood-local`
- runtime profile: `dogfood-fixture`
- model provider: `mock`
- model: `mock`
- run status: `completed`
- runtime event count: `13`
- message snapshot recorded: `true`
- workspace metadata recorded: `true`
- routed workspace tool calls:
  - `file_read`
  - `bash_run`
- route status:
  - `/console/workspaces`: `200`
  - `/console/sessions`: `200`
  - `/console/runs`: `200`
  - `/console/runs/{run_id}`: `200`
  - `/console/observability?run_id={run_id}`: `200`

## Safety Notes

- No production credentials were used.
- No hosted services were required.
- Docker was not required for this deterministic replay.
- The evidence above does not include raw prompt, message, model result text,
  command output, stdout, stderr, environment values, secrets, file contents, or
  patch contents.
- The replay injects a secret sentinel into non-rendered workspace metadata to
  prove the console does not expose it.
- Prometheus metrics were checked for the workspace console route; the stable
  route label was present and the workspace id was not present in metric labels
  or exposition text.
- AgentKit Core and G00-G74 behavior were not changed.
