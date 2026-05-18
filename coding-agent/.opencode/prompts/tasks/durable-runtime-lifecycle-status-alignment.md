Goal:
Align durable runtime run lifecycle rows with the active G04/G10 objective:
queued, running, completed, failed, cancelled, interrupted, and startup orphan
recovery as interrupted/reclaimable.

Scope:
- Create root HTTP run records as `queued`.
- Transition created rows to `running` before adapter execution.
- Finish normal outcomes as `completed`.
- Finish adapter interruptions as `interrupted`.
- Preserve `failed` for adapter errors, fatal tool execution, and unexpected
  exceptions.
- Preserve `cancelled` for local task cancellation.
- Recover stale active-owner `running` rows as `interrupted` with
  `reclaimable: true`.
- Update replay/store tests and durable runtime progress docs to reflect the
  active objective labels.

Out of scope:
- Do not introduce a scheduler or resume orphaned work.
- Do not destructively migrate existing rows with older status strings.
- Do not change JSONL/file defaults or require PostgreSQL unless
  `storage.runtime_backend = "pg"` is configured.
- Do not mutate tape rows, runtime events, message snapshots, or approval
  interactions during recovery.
- Do not add public HTTP endpoints in this slice.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
  - `docs/adr/0031-stale-runtime-run-recovery.md`
  - `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
  - `postmortem/patterns/PM-0021-guard-event-stream-registration-against-disappearing-sessions.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/runtime_store.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`
  - `tests/coding_agent/test_pg_runtime_store.py`
  - `docs/durable_runtime/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or stale_runtime_runs or run_id" -v`
- `uv run pytest tests/ui/test_http_server.py -k "runtime_replay or lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
- `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/coding_agent/test_pg_runtime_store.py`
- `uv run ruff format --check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/coding_agent/test_pg_runtime_store.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- Stop with a blocker if queued/running semantics require a scheduler.
- Stop with a blocker if recovery needs to claim or resume work from another
  active owner.
- Stop with a blocker if satisfying G04/G10 requires a destructive migration.
