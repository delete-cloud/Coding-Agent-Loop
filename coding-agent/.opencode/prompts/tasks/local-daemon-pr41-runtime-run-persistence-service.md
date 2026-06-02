Goal:
Move runtime run lifecycle and message snapshot persistence out of
`SessionManager` into a run/service boundary.

Scope:
- Add a `RuntimeRunPersistenceService` under `coding_agent.runs`.
- Route `SessionManager.run_agent` turn tracking and finalization through the
  service.
- Add focused service tests for run lifecycle delegation and message snapshot
  persistence.

Out of scope:
- Checkpoint restore preparation ownership.
- Approval-driven consumer setup/session mutation.
- Runtime close/error policy extraction.
- Live display-event projection changes.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/lifecycle.py`
  - `src/coding_agent/runs/persistence.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_runtime_run_persistence.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_run_persistence.py -v`
- `uv run pytest tests/coding_agent/test_run_lifecycle.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_persists_message_snapshot_when_runtime_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store" -v`
- `uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_run_persistence.py tests/ui/test_session_manager_runtime.py`

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
