# Durable Runtime Goal Progress

Last updated: 2026-05-18

| Goal | Status | Evidence |
| --- | --- | --- |
| G00 | Complete | `docs/durable_runtime/CURRENT_STATE.md` documents SessionManager, PipelineAdapter, storage plugins, PG storage, observability, approval flow, event flow, and tape flow. |
| G01 | Complete | `docs/adr/0029-durable-runtime-identity.md` defines session, run, turn compatibility, tape, event, interaction, checkpoint, and Langfuse/OTLP correlation identity. |
| G02 | Complete | `src/coding_agent/runtime_store.py` adds the inert PostgreSQL durable runtime store, with unit coverage in `tests/coding_agent/test_pg_runtime_store.py` and ADR-0030. |
| G03 | Pending | Not started. |
| G04 | Pending | Not started. |
| G05 | Pending | Not started. |
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
