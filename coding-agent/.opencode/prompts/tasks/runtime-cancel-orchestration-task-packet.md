Goal:
Move cancel-session runtime orchestration out of `SessionManager` and into the
runtime runs layer.

Scope:
- Add `RuntimeCancelOrchestrationService` around the existing
  `RuntimeCancelService`.
- Delegate attached/local/idle cancellation branching, session persistence,
  local task cancellation, and cancelled-task observation scheduling.
- Keep `SessionManager` responsible for owner/session lookup, lock ownership,
  and the existing cancelled-task observation callback.
- Update ADR-0058 follow-up status.

Out of scope:
- Changing HTTP cancel response semantics.
- Changing runtime store formats or attached executor claim protocol.
- Moving `_observe_cancelled_turn()` lock/session reload behavior.
- Changing event queue attach, event ownership, or teardown cleanup paths.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/runs/cancel.py`
  - `tests/coding_agent/test_runtime_cancel_service.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_cancel_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "cancel_session_turn or cancel_attached_executor" -v`
- `uv run pytest tests/ui/test_http_server.py -k "cancel_session_turn" -v`
- `uv run ruff check src/coding_agent/runs/cancel.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_cancel_service.py`
- `uv run ruff format --check src/coding_agent/runs/cancel.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_cancel_service.py`

Postmortem checks:
- PM-0022 and PM-0023 were consulted because this task runs
  `tests/ui/test_session_manager_runtime.py` and `tests/ui/test_http_server.py`.
- This slice does not change `/events` queue attach, event append ownership,
  disconnect cleanup, or session teardown ordering.

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
