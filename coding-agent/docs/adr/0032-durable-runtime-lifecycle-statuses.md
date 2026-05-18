# ADR-0032: Adopt durable runtime lifecycle statuses

**Status**: Proposed
**Date**: 2026-05-19

## Context

The durable runtime objective requires root run lifecycle rows to distinguish
queued, running, completed, failed, cancelled, and interrupted states. The first
runtime lifecycle slices persisted a root run as `running`, finished successful
turns as `succeeded`, mapped interrupted adapter outcomes to `cancelled`, and
recovered stale startup rows as `failed`.

Those early statuses are not expressive enough for replay and recovery. A
completed model turn is not the same thing as a queued or still-running turn, an
adapter-level interruption is distinct from task cancellation, and process
restart recovery needs to say that a previously running row is no longer owned
by a live executor without pretending the model/tool failed.

## Decision

Use this forward-going durable runtime status vocabulary for root HTTP run rows:

- `queued` when `SessionManager` has allocated the `run_id` and persisted the
  run row before invoking the adapter.
- `running` immediately before executing the adapter turn.
- `completed` for non-error adapter outcomes.
- `failed` for adapter error outcomes, fatal tool execution, and unexpected
  exceptions.
- `cancelled` when the in-process turn task is cancelled.
- `interrupted` when the adapter reports `StopReason.INTERRUPTED`.

Startup recovery keeps ADR-0031's owner-safety checks, but changes recovered
stale `running` rows to `interrupted` and adds `reclaimable: true` plus recovery
metadata. Recovery does not delete rows, mutate tape history, schedule resumed
work, or claim that old `succeeded` rows were migrated. Existing rows with older
free-text statuses remain readable.

`queued` is an immediate persisted transition, not a scheduler. The HTTP turn
path creates the durable row as `queued` after runtime identity and tape
identity are known, then updates the same row to `running` before
`adapter.run_turn()`.

## Alternatives Rejected

- Keep `succeeded` for normal completion - rejected because the active durable
  runtime contract names `completed`, and replay consumers should not need two
  success words for new rows.
- Treat adapter interruption as cancellation - rejected because cancellation is
  initiated by local task cancellation, while interruption is a runtime outcome
  that may still produce deterministic replay metadata.
- Recover orphan rows as `failed` - rejected because startup reconciliation
  indicates ownership loss/process death, not a model or tool failure.
- Add a scheduler for queued rows now - rejected because the objective only
  requires lifecycle checkpoints and orphan recovery, not background scheduling.
- Backfill existing status values - rejected because there is no destructive
  migration requirement and old rows can remain readable as historical data.

## Acceptance Criteria

- [x] `test_run_agent_persists_agent_run_lifecycle_when_store_configured`
- [x] `test_agent_run_marks_interrupted_outcome_as_interrupted`
- [x] `test_recover_stale_runtime_runs_marks_running_runs_interrupted_reclaimable`
- [x] `test_runtime_replay_endpoints_return_run_snapshot_and_events`
- [x] `test_create_update_load_and_list_agent_runs`
- [x] `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or stale_runtime_runs or run_id" -v`
- [x] `uv run pytest tests/ui/test_http_server.py -k "runtime_replay or lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
- [x] `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`

## References

- `docs/adr/0029-durable-runtime-identity.md`
- `docs/adr/0030-postgresql-durable-runtime-store.md`
- `docs/adr/0031-stale-runtime-run-recovery.md`
- `docs/durable_runtime/GOAL_PROGRESS.md`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/runtime_store.py`
- `tests/ui/test_session_manager_runtime.py`
- `tests/ui/test_http_server.py`
- `tests/coding_agent/test_pg_runtime_store.py`
