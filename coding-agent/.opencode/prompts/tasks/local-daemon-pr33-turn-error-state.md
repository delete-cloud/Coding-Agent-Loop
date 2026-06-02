Goal:
Move local daemon turn error handled/handler-failed bookkeeping out of
`SessionManager.run_agent` into a tested lifecycle helper.

Scope:
- Add `RuntimeTurnErrorState` under `coding_agent.runs.lifecycle`.
- Replace `turn_error_handled` and `turn_error_handler_failed` nonlocal state in
  `run_agent`.
- Preserve fatal/cancel/generic error behavior, wire messages, runtime close,
  and run finish semantics.
- Add focused unit tests for handled and handler-failed transitions.

Out of scope:
- Extracting fatal/cancel/generic error action bodies.
- Changing exception propagation, wire protocol, runtime store schemas, or
  persisted run/session status values.
- Changing `RunCoordinatorError` force-start behavior.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or agent_run_marks_interrupted_outcome_as_interrupted or run_agent_executes_local_runtime_through_local_daemon_executor or run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn" -v
- uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py
- uv run ruff format --check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py

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
