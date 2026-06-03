Goal:
Move attached/external executor run-request orchestration out of `SessionManager`.

Scope:
- Add a runtime attached executor request service for lock ownership, owner assertion, session loading, attached-session validation, turn-state mutation, and session persistence.
- Delegate `SessionManager.request_attached_executor_run()` to the new service.
- Preserve `request_external_worker_run()` as a compatibility wrapper.
- Update ADR-0058 follow-up status with the completed extraction.

Out of scope:
- Attached executor claim, heartbeat, event append, or finalization behavior.
- Resume prompt construction.
- Local daemon run execution.
- Event stream registration, cleanup, or teardown.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/attached_executor.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_attached_executor_service.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_attached_executor_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "attached_executor" -v`

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
