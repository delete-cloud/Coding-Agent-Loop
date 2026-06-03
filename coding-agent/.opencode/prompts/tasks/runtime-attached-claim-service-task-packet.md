Goal:
Move attached/external executor claim orchestration out of `SessionManager`.

Scope:
- Add a runtime attached executor claim service for claim invocation, session loading, and claim DTO construction.
- Delegate `SessionManager.claim_attached_executor_run()` to the new service.
- Preserve `claim_external_worker_run()` as a compatibility wrapper.
- Update ADR-0058 follow-up status with the completed extraction.

Out of scope:
- Attached executor request, heartbeat, event append, or finalization behavior.
- Runtime store claim selection semantics.
- Event stream registration, cleanup, or teardown.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/attached_executor.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_attached_executor_service.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_attached_executor_service.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "external_worker or attached_executor" -v`

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
