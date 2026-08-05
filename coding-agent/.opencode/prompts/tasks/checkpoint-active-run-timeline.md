Goal:
Restore a checkpoint without replaying or resuming runtime runs that occurred
after that checkpoint, while retaining those runs and events for audit.

Scope:
- Add durable, backwards-compatible run supersession fields to JSONL, SQLite,
  and PostgreSQL runtime records.
- Mark post-checkpoint active runs inside SQLite and PostgreSQL durable restore
  transactions.
- Add an explicit active-run query contract and use it for session history,
  latest-run/resume state, result fallback, and ACP session load.
- Exclude superseded runs from executor claim paths and persist the reconciled
  current turn in session metadata.
- Add focused persistence, restore, query, HTTP, ACP, and WebUI regressions.
- Record the recurring projection-reconciliation failure in `postmortem/`.

Out of scope:
- Deleting runtime runs, events, snapshots, or interactions.
- Atomic runtime-projection reconciliation for legacy JSONL checkpoint restore.
- Production deployment, o6n mutation, GitOps updates, or live smoke tests.
- Changing developer-console audit or direct run-id/event endpoints to hide
  superseded records.
- Redesigning checkpoint snapshot payloads or restore authorization.

Context:
- ADRs:
  - `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
  - `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
  - `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
  - `docs/adr/0075-checkpoint-restore-active-run-timeline.md`
- Relevant files:
  - `src/coding_agent/stores/runtime_store.py`
  - `src/coding_agent/stores/durable_local.py`
  - `src/coding_agent/stores/durable_pg.py`
  - `src/coding_agent/runs/query.py`
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/acp/server.py`
  - `webui/app/src/App.test.tsx`
- Existing behavior:
  - Checkpoint capture runs under exclusive runtime maintenance admission.
  - Durable restore already reconciles tape, session, topics, and checkpoints in
    one SQLite or PostgreSQL transaction.
  - `list_agent_runs()` is an audit/control query and must remain unfiltered.
  - A test command that selects zero tests is a failure, not verification.
- Postmortem:
  - `PM-0026` matches `runtime_store.py`; run its focused adapter/lifecycle
    release checks and verify no new blank exception-to-run-error path.

Target tests:
- `uv run pytest tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_pg_runtime_store.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_runtime_query_service.py -v`
- `uv run pytest tests/ui/test_http_server.py tests/acp/test_server.py -k "checkpoint or runtime_run or session_load or result" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "restore_durable_state" -v`
- `uv run pytest tests/coding_agent/test_adapter_types.py tests/coding_agent/test_pipeline_adapter.py tests/coding_agent/test_run_lifecycle.py -v`
- `pnpm --dir webui/app --ignore-workspace run test -- App.test.tsx`
- `rg -n "str\\(exc\\)" src/coding_agent/stores/runtime_store.py src/coding_agent/stores/durable_local.py src/coding_agent/stores/durable_pg.py`

Loop policy:
- Engineer writes focused failing tests first, confirms the intended failures,
  implements the smallest correct change, and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
- Do not push, open a PR, deploy, or mutate o6n without explicit authorization.
