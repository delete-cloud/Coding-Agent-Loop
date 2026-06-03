Goal:
Extract attached executor run finalization orchestration out of `SessionManager`
into a dedicated runtime service while preserving the durable ordering where
tape entries are saved before the final runtime run status is written.

Scope:
- Add a `RuntimeAttachedExecutorFinalizeService` in the attached executor runtime
  services module.
- Delegate `SessionManager.finalize_attached_executor_run()` to the new service.
- Add focused service tests for finalization ordering and session state sync.
- Update ADR-0058 follow-up status for the delegated finalization boundary.

Out of scope:
- Changing HTTP API request/response contracts.
- Changing attached executor claim, heartbeat, or event append behavior.
- Changing event stream queue registration or teardown paths.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/attached_executor.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_attached_executor_service.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_attached_executor_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "finalize_attached_executor" -v`
- `uv run pytest tests/ui/test_http_server.py -k "external_worker or attached_executor" -v`
- `uv run ruff check src/coding_agent/runs/attached_executor.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_attached_executor_service.py`
- `uv run ruff format --check src/coding_agent/runs/attached_executor.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_attached_executor_service.py`
- `git diff --check`

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
