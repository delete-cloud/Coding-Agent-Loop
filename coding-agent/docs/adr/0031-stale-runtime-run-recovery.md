# ADR-0031: Mark stale runtime runs during HTTP startup recovery

**Status**: Proposed
**Date**: 2026-05-19

## Context

Durable runtime run records are now created when HTTP prompt execution starts
and are updated when the turn completes, fails, or is cancelled. If the process
dies between those writes, the `agent_runs` row remains `running` with no
`ended_at`, even though no in-process task can finish it after restart.

The HTTP server already backfills owner leases on startup before starting lease
renewal. That is the safe point to reconcile runtime rows for sessions this
process now owns. Without that reconciliation, replay and debugging surfaces can
show permanently active runs after process death.

## Decision

During HTTP startup, after owner lease backfill and before owner lease renewal,
recover stale runtime runs through `SessionManager`.

Recovery scans durable runs for persisted sessions. When owner leases are
configured, it only updates sessions with an active, unexpired lease owned by
this process. When owner leases are not configured, it treats the process as a
single-instance owner and scans all persisted sessions.

Only runs with `status == "running"` and no `ended_at` are recovered. Recovery
marks those rows `failed`, sets `ended_at` to the recovery timestamp, preserves
the existing result, preserves existing metadata, and adds recovery metadata so
operators can distinguish startup reconciliation from model/runtime failures.

## Alternatives Rejected

- Leave stale rows as `running` - rejected because durable replay/debug state
  would be misleading after process death.
- Delete stale run rows - rejected because durable runtime history should remain
  append/queryable, and deletion would hide the crash window.
- Mark all running rows globally in SQL without owner checks - rejected because
  multi-instance deployments must not mutate rows for sessions owned by another
  live process.
- Add a new terminal status such as `abandoned` immediately - rejected because
  current lifecycle consumers already understand `failed`, and the recovery
  metadata can distinguish the cause without expanding the status contract.

## Acceptance Criteria

- [x] `test_recover_stale_runtime_runs_marks_running_runs_failed`
- [x] `test_recover_stale_runtime_runs_skips_sessions_without_current_owner`
- [x] `test_lifespan_recovers_stale_runtime_runs_after_backfill_before_renewal`
- [x] `uv run pytest tests/ui/test_session_manager_runtime.py -k "stale_runtime_runs or agent_run or run_id" -v`
- [x] `uv run pytest tests/ui/test_http_server.py -k "lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
- [x] `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`

## References

- `docs/adr/0029-durable-runtime-identity.md`
- `docs/adr/0030-postgresql-durable-runtime-store.md`
- `docs/durable_runtime/CURRENT_STATE.md`
- `src/coding_agent/runtime_store.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
