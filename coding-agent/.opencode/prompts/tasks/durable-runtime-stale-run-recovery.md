Goal:
Recover stale durable runtime runs left in `running` after HTTP process restart.

Scope:
- Add startup recovery semantics for durable runtime run rows.
- Recover only `running` agent runs with no `ended_at`.
- When owner leases are configured, mutate only sessions currently owned by the
  current `SessionManager`.
- Wire HTTP startup to run recovery after owner lease backfill and before owner
  renewal.
- Update durable runtime progress documentation for G11.

Out of scope:
- Do not change JSONL/file defaults or require PostgreSQL unless
  `storage.runtime_backend = "pg"` is configured.
- Do not add new public HTTP endpoints.
- Do not delete or rewrite runtime events, message snapshots, interactions, or
  tape rows.
- Do not introduce a new terminal run status in this checkpoint.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
  - `docs/adr/0031-stale-runtime-run-recovery.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
  - `postmortem/patterns/PM-0021-guard-event-stream-registration-against-disappearing-sessions.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`
  - `docs/durable_runtime/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "stale_runtime_runs or agent_run or run_id" -v`
- `uv run pytest tests/ui/test_http_server.py -k "lifespan_recovers_stale_runtime_runs or lifespan_backfills_owner_leases or lifespan_logs_backfill_failure" -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
- `uv run ruff check src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py`
- `uv run ruff format --check src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py`

Stop conditions:
- Stop with a blocker if safe recovery requires a new owner/lease protocol.
- Stop with a blocker if stale run recovery needs to mutate tape, event, message
  snapshot, or approval interaction history.
- Stop with a blocker if a new public terminal run status is required.
