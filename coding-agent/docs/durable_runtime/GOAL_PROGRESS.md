# Durable Runtime Goal Progress

Last updated: 2026-05-19

| Goal | Status | Evidence |
| --- | --- | --- |
| G00 | Complete | `docs/durable_runtime/CURRENT_STATE.md` documents SessionManager, PipelineAdapter, storage plugins, PG storage, observability, approval flow, event flow, and tape flow. |
| G01 | Complete | `docs/adr/0029-durable-runtime-identity.md` defines session, run, turn compatibility, tape, event, interaction, checkpoint, and Langfuse/OTLP correlation identity. |
| G02 | Complete | `src/coding_agent/runtime_store.py` adds the inert PostgreSQL durable runtime store, with unit coverage in `tests/coding_agent/test_pg_runtime_store.py` and ADR-0030. |
| G03 | Complete | `SessionManager.run_agent()` creates a root `run_id`, keeps `current_turn_id` as its alias, and binds cold/hot runtime contexts to that identity. |
| G04 | Complete | `SessionManager` can persist root HTTP run lifecycle records through an optional runtime store without changing default storage behavior. |
| G05 | Complete | `storage.runtime_backend = "pg"` opt-in construction wires `PGRuntimeStore` into `SessionManager` while preserving disabled defaults. |
| G06 | Complete | `SessionManager` appends runtime-store wire events for emitted HTTP turn messages, approval requests, and error turn notifications when a runtime store is configured. |
| G07 | Complete | `SessionManager` saves `{run_id}:latest` compacted message snapshots from `ctx.messages` when a runtime store is configured. |
| G08 | Complete | HTTP replay APIs expose runtime run records, latest message snapshots, and runtime events with `last_event_id` filtering. |
| G09 | Complete | `SessionManager` persists durable approval interaction records for pending requests, applied decisions, session auto-approvals, and approval timeouts when a runtime store is configured. |
| G10 | Complete | AgentKit pipeline spans include safe runtime correlation attributes, and HTTP root runs bind `turn_id`/`tape_id` trace metadata without weakening OTLP privacy filtering. |
| G11 | Complete | HTTP startup recovers stale durable runtime runs by marking owned `running` rows failed after owner lease backfill. |

## G00 Verification

- Inspected the files listed in `CURRENT_STATE.md`.
- Confirmed this checkpoint is documentation-only.

## G01 Verification

- Added `docs/adr/0029-durable-runtime-identity.md`.
- Confirmed this checkpoint is documentation-only.

## G02 Verification

- Added `docs/adr/0030-postgresql-durable-runtime-store.md`.
- Added `.opencode/prompts/tasks/pg-durable-runtime-store.md`.
- Added `src/coding_agent/runtime_store.py`.
- Added `tests/coding_agent/test_pg_runtime_store.py`.
- Target tests:
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run pytest tests/agentkit/storage/test_pg.py tests/coding_agent/plugins/test_storage_factory.py -v`

## G03 Verification

- Added `.opencode/prompts/tasks/durable-runtime-run-identity.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_id or reuses_live_runtime or hardcode_api_key or emits_error_turn_end" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -v`
  - `uv run pytest tests/ui/test_session_manager_public_api.py -k "run_agent or turn_id" -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

## G04 Verification

- Added `.opencode/prompts/tasks/durable-runtime-run-lifecycle.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or run_id or reuses_live_runtime or hardcode_api_key or emits_error_turn_end" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -v`
  - `uv run pytest tests/ui/test_session_manager_public_api.py -k "run_agent or turn_id" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

## G05 Verification

- Added `.opencode/prompts/tasks/durable-runtime-store-config.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `src/coding_agent/agent.toml`.
- Updated `docs/remote-sandbox-production.md`.
- Updated `tests/ui/test_session_manager_public_api.py`.
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_public_api.py -k "runtime_store or pg_backends or pg_pool or runtime_backend" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or run_id" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_public_api.py`

## G06 Verification

- Added `.opencode/prompts/tasks/durable-runtime-events.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "persists_wire_events or approval_request_wire_events or agent_run or run_id" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

## G07 Verification

- Added `.opencode/prompts/tasks/durable-runtime-message-snapshots.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "message_snapshot or persists_wire_events or approval_request_wire_events or agent_run or run_id" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

## G08 Verification

- Added `.opencode/prompts/tasks/durable-runtime-replay-apis.md`.
- Updated `src/coding_agent/runtime_store.py`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `src/coding_agent/ui/http_server.py`.
- Updated `src/coding_agent/ui/schemas.py`.
- Updated `tests/coding_agent/test_pg_runtime_store.py`.
- Updated `tests/ui/test_http_server.py`.
- Target tests:
  - `uv run pytest tests/ui/test_http_server.py -k "runtime_replay or get_runtime_run" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -k "runtime_event" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "message_snapshot or persists_wire_events or approval_request_wire_events or agent_run or run_id" -v`
  - `uv run ruff check src/coding_agent/runtime_store.py src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py src/coding_agent/ui/schemas.py tests/coding_agent/test_pg_runtime_store.py tests/ui/test_http_server.py`
- Additional note: `uv run pytest tests/ui/test_http_server.py -v` was attempted but is blocked by the pre-existing `main` failure in `TestSessionCreation.test_http_create_session_provisions_docker_cloud_workspace`, where the current implementation returns `workspace_provider` and `workspace_root_ref` in `origin`.

## G09 Verification

- Added `.opencode/prompts/tasks/durable-runtime-approval-interactions.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0013-clear-answered-request-projections.md`
  - `postmortem/patterns/PM-0014-make-approval-responses-single-shot.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "approval" -v`
  - `uv run pytest tests/ui/test_http_server.py -k "ApprovalEndpoint or ApprovalStoreIntegration" -v`
  - `uv run pytest tests/approval/test_coordinator.py tests/approval/test_store.py -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -k "interaction" -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

## G10 Verification

- Added `.opencode/prompts/tasks/durable-runtime-observability-correlation.md`.
- Updated `src/agentkit/runtime/pipeline.py`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/agentkit/runtime/test_pipeline.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Updated `tests/coding_agent/test_observability.py`.
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0006-add-usage-event-fields-and-fix-tool-name-kwarg-in-pipeline.md`
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
  - `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`
- Target tests:
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "span" -v`
  - `uv run pytest tests/agentkit/runtime/test_pipeline.py -v`
  - `uv run pytest tests/agentkit/observability/test_core.py -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_id or approval or message_snapshot" -v`
  - `uv run pytest tests/coding_agent/test_observability.py -v`
  - `uv run ruff check src/agentkit/runtime/pipeline.py src/coding_agent/ui/session_manager.py tests/agentkit/runtime/test_pipeline.py tests/ui/test_session_manager_runtime.py tests/coding_agent/test_observability.py`

## G11 Verification

- Added `docs/adr/0031-stale-runtime-run-recovery.md`.
- Added `.opencode/prompts/tasks/durable-runtime-stale-run-recovery.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `src/coding_agent/ui/http_server.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Updated `tests/ui/test_http_server.py`.
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
  - `postmortem/patterns/PM-0021-guard-event-stream-registration-against-disappearing-sessions.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`
- Red tests before implementation:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "stale_runtime_runs" -v`
  - `uv run pytest tests/ui/test_http_server.py -k "lifespan_recovers_stale_runtime_runs" -v`
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "stale_runtime_runs or agent_run or run_id" -v`
  - `uv run pytest tests/ui/test_http_server.py -k "lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py`
  - `uv run ruff format --check src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py`
