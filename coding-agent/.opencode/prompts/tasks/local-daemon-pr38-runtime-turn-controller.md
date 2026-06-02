Goal:
Combine the extracted local daemon runtime turn helpers behind a single
`RuntimeTurnController` so `SessionManager.run_agent` no longer owns the
before/after/error turn state machine.

Scope:
- Add `RuntimeTurnController` under `coding_agent.runs.lifecycle`.
- Route before-turn, after-turn, executor error, and outer exception handling
  through the controller.
- Keep wire notification, observation projection, runtime preparation, and
  checkpoint restore as injected/session-manager-owned callbacks for now.
- Preserve existing fatal, cancelled, generic, and RunCoordinatorError
  propagation semantics.
- Update ADR-0058 follow-up notes.
- Add focused unit coverage for controller hook routing and double-handle
  prevention.

Out of scope:
- Moving wire message construction out of `SessionManager`.
- Moving runtime preparation or checkpoint restore.
- Changing `LocalDaemonExecutor` hook protocol.
- Changing daemon/CLI product entrypoints.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn or run_agent_closes_cached_runtime_after_turn_failure or run_agent_rejects_concurrent_turn_for_same_session" -v
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
