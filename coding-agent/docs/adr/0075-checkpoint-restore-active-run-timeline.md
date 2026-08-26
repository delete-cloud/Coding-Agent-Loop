# ADR-0075: Reconcile the active run timeline on checkpoint restore

**Status**: Proposed
**Date**: 2026-08-05

## Context

Checkpoint restore replaces the stable tape and session configuration with the
selected snapshot, but runtime run records and their events remain durable. The
session history, resume metadata, result fallback, and ACP session load currently
list every durable run. A run created after the restored checkpoint can therefore
reappear as an apparently current assistant response even though its tape entries
were rolled back.

Runtime records also serve audit, worker recovery, and developer diagnostics.
Deleting those records would repair the visible timeline at the cost of losing
evidence. Filtering by the checkpoint timestamp forever is also incorrect because
new runs created after a successful restore belong to the new active timeline.

Checkpoint capture is admitted through the session's exclusive runtime
maintenance boundary. A run cannot legitimately overlap capture, so the
checkpoint creation time is the durable boundary available to existing snapshots.

## Decision

Preserve every runtime run and event for audit, and add nullable run fields that
record when and by which checkpoint a run was superseded. During durable SQLite
and PostgreSQL restore, mark previously active runs for the same session whose
`started_at` is later than the checkpoint's `created_at`. This mutation is part of
the same fenced transaction that restores tape, session state, topics, and the
checkpoint set. Existing supersession markers are not overwritten.

Before the restore transaction persists the session payload, recompute
`current_turn_id` from the latest active run at or before the checkpoint boundary.
Persist that optional value in session metadata so restart hydration remains
aligned with the restored timeline.

Keep `list_agent_runs()` and direct run/event lookup as full audit queries. Add an
explicit active-timeline query in the run query service that excludes superseded
runs. Session history, latest-run selection, resume metadata, result fallback, and
ACP session load use the active query; worker recovery, developer diagnostics, and
direct run/event endpoints keep using the audit query.

New runs created after restore are active because supersession is persisted only
for rows present when restore executes. Existing databases are upgraded
idempotently so the nullable fields do not require an offline migration.

JSONL records round trip the optional fields for compatibility, but JSONL restore
does not gain projection atomicity in this change. JSONL is a legacy/debug format;
the production contract applies to the authoritative SQLite and PostgreSQL
durable stores.

## Alternatives Rejected

- Delete post-checkpoint runs and events: fixes replay but destroys audit and
  incident evidence.
- Filter permanently by checkpoint creation time: also hides legitimate runs
  created after restore.
- Filter only in WebUI: leaves ACP load, resume, session summary, and result
  fallback inconsistent.
- Change `list_agent_runs()` to hide superseded rows everywhere: breaks audit,
  startup recovery, worker state, and developer-console consumers.

Executor claim paths are control-plane consumers rather than audit queries. They
exclude superseded requested or expired rows so rolled-back work cannot execute
after restore.

## Acceptance Criteria

- [ ] SQLite and PostgreSQL runtime schemas upgrade existing databases and round
  trip supersession fields.
- [ ] SQLite and PostgreSQL durable restore atomically mark only active runs that
  started after the restored checkpoint.
- [ ] Active run queries, latest-run selection, session summary, result fallback,
  and ACP load ignore superseded runs.
- [ ] Full run listing and direct run/event/display-event lookup retain
  superseded records for audit.
- [ ] A run created after restore remains visible on the active timeline.
- [ ] Superseded requested or expired runs cannot be claimed by an executor.
- [ ] Restart hydration retains the reconciled `current_turn_id`.
- [ ] `uv run pytest tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_pg_runtime_store.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_runtime_query_service.py -v`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/acp/test_server.py -k "checkpoint or runtime_run or session_load or result" -v`
- [ ] `pnpm --dir webui/app --ignore-workspace run test -- App.test.tsx`

## References

- `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
- `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/durable_local.py`
- `src/coding_agent/stores/durable_pg.py`
- `src/coding_agent/runs/query.py`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/acp/server.py`
- `webui/app/src/lib/api.ts`
