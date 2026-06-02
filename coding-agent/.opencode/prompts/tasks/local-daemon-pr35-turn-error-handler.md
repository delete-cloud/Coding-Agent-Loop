Goal:
Extract local daemon turn error actions from `SessionManager.run_agent` into a
runtime lifecycle helper so error handling is owned by the runtime turn boundary,
not by the session manager loop body.

Scope:
- Add a `RuntimeTurnErrorHandler` helper under `coding_agent.runs.lifecycle`.
- Move fatal, cancelled, and generic turn error actions out of
  `SessionManager.run_agent`.
- Preserve existing behavior for run finish status, observation state, runtime
  close, wire error notification, and outer exception propagation.
- Update ADR-0058 follow-up notes.
- Add focused unit coverage for the new lifecycle helper.

Out of scope:
- Changing runtime store payloads or status names.
- Moving checkpoint restore, display projection, or final cleanup policy.
- Changing HTTP/CLI API behavior.
- Changing LocalDaemonExecutor execution semantics.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runs/lifecycle.py
  - src/coding_agent/runs/__init__.py
  - src/coding_agent/server/session_manager.py
  - tests/coding_agent/test_run_lifecycle.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_run_lifecycle.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn or run_agent_persists_agent_run_lifecycle_when_store_configured" -v
- uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py
- uv run ruff format --check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py
- git diff --check

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
