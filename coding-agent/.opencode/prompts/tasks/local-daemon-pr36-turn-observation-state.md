Goal:
Move local daemon turn observation recorder ownership out of
`SessionManager.run_agent` nonlocal closures and into a runtime lifecycle helper.

Scope:
- Add `RuntimeTurnObservationState` under `coding_agent.runs.lifecycle`.
- Use it from `RuntimeTurnStarter`, `RuntimeTurnFinalizer`, and
  `RuntimeTurnErrorHandler` integration in `SessionManager.run_agent`.
- Preserve existing observation behavior for start, complete, fail, and cancel.
- Update ADR-0058 follow-up notes.
- Add focused unit coverage for the new observation state helper.

Out of scope:
- Changing observation store payloads or statuses.
- Moving wire error notification out of `SessionManager`.
- Moving final turn cleanup or checkpoint restore.
- Changing HTTP/CLI API behavior.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn or run_agent_closes_cached_runtime_after_turn_failure" -v
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
