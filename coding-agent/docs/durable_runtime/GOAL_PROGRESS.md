# Durable Runtime Goal Progress

Last updated: 2026-05-18

| Goal | Status | Evidence |
| --- | --- | --- |
| G00 | Complete | `docs/durable_runtime/CURRENT_STATE.md` documents SessionManager, PipelineAdapter, storage plugins, PG storage, observability, approval flow, event flow, and tape flow. |
| G01 | Complete | `docs/adr/0029-durable-runtime-identity.md` defines session, run, turn compatibility, tape, event, interaction, checkpoint, and Langfuse/OTLP correlation identity. |
| G02 | Complete | `src/coding_agent/runtime_store.py` adds the inert PostgreSQL durable runtime store, with unit coverage in `tests/coding_agent/test_pg_runtime_store.py` and ADR-0030. |
| G03 | Complete | `SessionManager.run_agent()` creates a root `run_id`, keeps `current_turn_id` as its alias, and binds cold/hot runtime contexts to that identity. |
| G04 | Complete | `SessionManager` can persist root HTTP run lifecycle records through an optional runtime store without changing default storage behavior. |
| G05 | Complete | `storage.runtime_backend = "pg"` opt-in construction wires `PGRuntimeStore` into `SessionManager` while preserving disabled defaults. |
| G06 | Pending | Not started. |
| G07 | Pending | Not started. |
| G08 | Pending | Not started. |
| G09 | Pending | Not started. |
| G10 | Pending | Not started. |
| G11 | Pending | Not started. |

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
