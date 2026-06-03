Goal:
Move stale runtime run recovery out of `SessionManager` into a run/service
boundary.

Scope:
- Add a `RuntimeRunRecoveryService` under `coding_agent.runs`.
- Add a narrow `RuntimeRunRecoveryStore` contract for list/update recovery
  operations.
- Route `SessionManager.recover_stale_runtime_runs()` through the service.
- Add focused service tests for stale running runs, expired attached executor
  claims, owner filtering, and storeless no-op behavior.

Out of scope:
- Attached executor claim authorization/finalization extraction.
- Approval service ownership.
- Checkpoint restore preparation ownership.
- Runtime close/error policy extraction.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/recovery.py`
  - `src/coding_agent/stores/runtime.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_runtime_run_recovery.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_run_recovery.py -v`
- `uv run pytest tests/coding_agent/test_runtime_store_contracts.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "recover_stale_runtime_runs" -v`
- `uv run pytest tests/ui/test_http_server.py -k "external_worker_recovery_expires_stale_claim or lifespan_recovers_stale_runtime_runs_after_backfill_before_renewal" -v`
- `uv run ruff check src/coding_agent/runs src/coding_agent/stores src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_run_recovery.py tests/coding_agent/test_runtime_store_contracts.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
