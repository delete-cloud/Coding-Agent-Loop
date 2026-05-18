# Durable Runtime Goal Progress

Last updated: 2026-05-19

## Active Objective Map

This table tracks the current durable-runtime objective. The historical slice log
below records the earlier implementation order, whose G09-G11 labels do not
match the active objective map.

| Goal | Status | Evidence |
| --- | --- | --- |
| G00 | Complete | `docs/durable_runtime/CURRENT_STATE.md` documents SessionManager, PipelineAdapter, storage plugins, PG storage, observability, approval flow, event flow, and tape flow. |
| G01 | Complete | `docs/adr/0029-durable-runtime-identity.md` defines session, run, turn compatibility, tape, event, interaction, checkpoint, and Langfuse/OTLP correlation identity. |
| G02 | Complete | `src/coding_agent/runtime_store.py` adds the PostgreSQL durable runtime store, with unit coverage in `tests/coding_agent/test_pg_runtime_store.py` and ADR-0030. |
| G03 | Needs audit | `storage.runtime_backend = "pg"` opt-in wiring exists, but the storage/plugin/composition boundary still needs final audit against the active objective wording. |
| G04 | Complete | ADR-0032 and `SessionManager` now persist `queued -> running -> completed/failed/cancelled/interrupted` root run lifecycle rows while preserving `current_turn_id` compatibility. |
| G05 | Complete | `SessionManager` persists runtime wire events and `{run_id}:latest` compacted message snapshots when a runtime store is configured. |
| G06 | Complete | HTTP replay APIs expose runtime run records, latest message snapshots, and runtime events with `last_event_id` filtering. |
| G07 | Complete | `SessionManager` persists durable approval interaction records for pending requests, applied decisions, session auto-approvals, and approval timeouts when a runtime store is configured. |
| G08 | Needs audit | AgentKit and HTTP root run correlation were added, but final audit must confirm the active objective's full safe attribute set is populated where required. |
| G09 | Pending | `tape.info` and `tape.search` for PostgreSQL tape storage are still missing. |
| G10 | Complete | ADR-0032 changes startup orphan recovery to mark active-owner `running` rows `interrupted` with `reclaimable: true`, without adding a scheduler. |
| G11 | Pending | End-to-end smoke tests/docs for normal run, failed run, approval run, replay, Langfuse/OTLP correlation, and tape debug remain to be added. |

## Active G04/G10 Alignment Verification

- Added `docs/adr/0032-durable-runtime-lifecycle-statuses.md`.
- Marked `docs/adr/0031-stale-runtime-run-recovery.md` superseded by
  ADR-0032 for the recovery terminal-status decision.
- Added `.opencode/prompts/tasks/durable-runtime-lifecycle-status-alignment.md`.
- Updated `src/coding_agent/ui/session_manager.py`.
- Updated `tests/ui/test_session_manager_runtime.py`.
- Updated `tests/ui/test_http_server.py`.
- Updated `tests/coding_agent/test_pg_runtime_store.py`.
- Red tests before implementation:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or stale_runtime_runs or run_id" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "interrupted_outcome" -v`
- Target tests:
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or stale_runtime_runs or run_id" -v`
  - `uv run pytest tests/ui/test_http_server.py -k "runtime_replay or lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
  - `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
  - `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/coding_agent/test_pg_runtime_store.py docs/adr/0031-stale-runtime-run-recovery.md docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
  - `postmortem/patterns/PM-0021-guard-event-stream-registration-against-disappearing-sessions.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`

## Historical Slice Log

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
