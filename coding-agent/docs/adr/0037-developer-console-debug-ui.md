# ADR-0037: Developer Console debug UI boundary

**Status**: Accepted
**Date**: 2026-05-20

## Context

G00-G53 added durable runtime state, context evidence, action safety, release
contracts, and observability backends. Those capabilities are available through
HTTP APIs, stores, tests, metrics, and docs, but there is no browsable
per-session or per-run debug surface. Operators and developers currently need
to inspect separate JSON endpoints, tests, logs, metrics, and documents.

The repository has a FastAPI HTTP server in `src/coding_agent/ui/http_server.py`
and terminal UI modules, but no web frontend stack, static assets, templates,
or `/console` routes. Introducing a new client framework for this phase would
increase surface area before the console contract is proven.

## Decision

Add a Coding Agent Developer Console as a debug/product surface over existing
runtime, context, action, observability, and release APIs. The console belongs
to `coding_agent`, not `agentkit`. AgentKit Core remains generic and must not
import console code.

The console will start as server-rendered HTML from the existing FastAPI app or
a small adjacent `coding_agent.ui` module imported by that app. It may use small
inline CSS/HTML helpers. It must not require a Node build, hosted service,
production credentials, real LLM call, new schedule system, desktop app,
bridge, proactive agent, or full Docker sandbox.

Supported pages for this phase:

- `/console`
- `/console/sessions`
- `/console/runs`
- `/console/runs/{run_id}`
- `/console/interactions`
- `/console/tape`
- `/console/context`
- `/console/memory`
- `/console/actions`
- `/console/observability`
- `/console/release`

The console is read-only by default. A resolve action for HITL interactions may
be added only if it delegates to existing approval APIs and preserves all
approval/action policy checks. Console pages must never bypass approval,
command, file-edit, validation, or workspace policy.

Console rendering must be privacy-preserving:

- Render only existing sanitized API fields or explicitly safe derived
  summaries.
- Do not render raw prompt, message, content, command output, stdout, stderr,
  environment values, model results, secrets, full tool payloads, file content,
  patch content, or unredacted text unless an existing sanitized API contract
  explicitly allows it.
- Prefer counts, statuses, bounded enums, timestamps, ids, source kinds,
  evidence reasons, scores, line ranges, and links.
- Escape all HTML output.
- Do not add sensitive data to tracing attributes while rendering console
  pages.

The console may display correlation identifiers such as `session_id`, `run_id`,
`tape_id`, retrieval/action/validation ids, and `interaction_id` as debug UI
values, but those identifiers must not become Prometheus labels and must not be
exported as raw sensitive trace attributes.

## API Dependency Map

Initial console pages should reuse these existing routes and helpers:

- sessions: `GET /sessions`, `GET /sessions/{session_id}`,
  `GET /sessions/{session_id}/result`
- runtime replay: `GET /runs/{run_id}`,
  `GET /runs/{run_id}/message-snapshot`, `GET /runs/{run_id}/events`
- HITL data: `AgentInteractionRecord` and runtime store helpers; add a
  read-only Coding Agent HTTP/helper path if needed
- tape debug: `PGTapeStore.info(...)` and `PGTapeStore.search(...)`; add a
  read-only Coding Agent HTTP/helper path if needed
- context and memory: existing context pack, KB, memory, and storage helpers;
  missing data should render an empty/read-only fallback
- actions and validation: existing action-safety records, runtime events,
  workspace diff/patch summaries, validation runner summaries, and approval
  metadata
- observability: `/metrics`, local Grafana docs/config, safe Langfuse/OTLP
  correlation metadata
- release: `docs/release_hardening/release-verification.yaml` through
  `coding_agent.verification.release_manifest`

New APIs added for console support must be read-only unless explicitly using an
existing mutation route. They must use existing auth/session visibility checks
where applicable.

## Observability Integration

Console pages may record normal HTTP request metrics through the existing HTTP
metrics middleware. They must not introduce new high-cardinality Prometheus
labels. If page-specific metrics are later added, labels must remain low
cardinality and must follow ADR-0036.

Console pages may link to local Grafana or Langfuse only when configured and
safe. They must not render Langfuse secret keys, Grafana tokens, Prometheus
credentials, API keys, or authorization headers.

## Testing Strategy

Use deterministic tests with fixture sessions, fake runtime stores, fake data,
and mocked config. Do not require external hosted services, production
credentials, Docker containers, or real LLM calls.

Expected test layers:

- route availability and navigation tests for `/console*`
- no-secret/no-raw-content rendering tests
- sessions and runs list fixture tests
- run detail and event replay fixture tests
- HITL interaction inbox fixture tests
- tape/context/memory/action/validation fixture tests
- observability/release configuration rendering tests
- final smoke tests covering the end-to-end console navigation chain
- prior durable runtime, context system, action safety, observability, and
  release verification regression gates where practical

## Non-Goals

- Do not build a new frontend framework or SPA build pipeline in this phase.
- Do not implement schedules, desktop app, bridge, proactive agent, or sandbox
  orchestration.
- Do not replace Prometheus/Grafana or Langfuse.
- Do not reimplement durable runtime, context retrieval, memory, action safety,
  release contracts, or observability exporters.
- Do not create write-capable action execution pages that bypass existing
  policies.

## Alternatives Rejected

- **Build a separate frontend app immediately**. Rejected because the repo has
  no frontend stack and the phase can be delivered through deterministic
  FastAPI HTML tests.
- **Expose an API-only debug console fallback for the whole phase**. Rejected
  because the phase requires browsable console pages and a demo path.
- **Put console primitives in AgentKit Core**. Rejected because the console is a
  product surface over Coding Agent stores and APIs.
- **Render raw traces, prompts, tool payloads, or command output for maximum
  debugging detail**. Rejected because it violates established privacy and
  observability boundaries.
- **Add resolve/execute controls first**. Rejected because read-only inspection
  is safer for MVP; mutation controls must reuse existing policy-preserving
  APIs.

## Acceptance Criteria

- [ ] `test_console_shell_routes_render_navigation_without_secrets`
- [ ] `test_console_sessions_list_renders_fixture_sessions`
- [ ] `test_console_runs_list_renders_fixture_runs`
- [ ] `test_console_run_detail_renders_snapshot_events_in_order`
- [ ] `test_console_interactions_renders_pending_and_resolved`
- [ ] `test_console_tape_context_render_safe_fixture_data`
- [ ] `test_console_memory_action_validation_render_safe_fixture_data`
- [ ] `test_console_observability_release_render_config_without_secrets`
- [ ] `test_developer_console_smoke_covers_runtime_context_action_hitl_observability_release`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `uv run pytest tests/coding_agent/test_developer_console_contract.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/developer_console/CURRENT_STATE.md`
- `docs/developer_console/UI_CONTRACT.md`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/runtime_store.py`
- `docs/adr/0033-postgresql-tape-debug-queries.md`
- `docs/adr/0034-context-system-boundaries-and-evidence.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `tests/ui/test_http_server.py`
