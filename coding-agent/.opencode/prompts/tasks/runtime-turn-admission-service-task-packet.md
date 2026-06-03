Goal:
Move local runtime turn admission checks out of `SessionManager` and into a
small runtime service while preserving existing HTTP prompt and local run
behavior.

Scope:
- Add `RuntimeTurnAdmissionService` for turn lock checks, workspace-export
  guards, owner assertion, session loading, and prepare-time active turn checks.
- Delegate `SessionManager.prepare_session_turn()` and `SessionManager.run_agent()`
  admission to that service.
- Add focused runtime service tests and update ADR-0058 follow-up status.

Out of scope:
- HTTP response shape or event stream behavior.
- Runtime execution, `RunRequest` construction, executor selection, or daemon CLI
  behavior.
- Changing the existing HTTP path where `/prompt` pre-marks the session as
  running before scheduling `run_agent()`.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/lifecycle.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_turn_admission_service.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_turn_admission_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "rejects_concurrent_turn_for_same_session or active_workspace_export or run_coordinator or local_daemon_executor" -v`
- `uv run pytest tests/ui/test_http_server.py -k "prompt_streaming_events or prompt_sets_turn_in_progress" -v`
- `uv run ruff check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_admission_service.py`
- `uv run ruff format --check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_admission_service.py`

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
