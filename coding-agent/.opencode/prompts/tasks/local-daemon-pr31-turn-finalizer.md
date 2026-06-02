Goal:
Extract local daemon turn completion finalization from `SessionManager.run_agent`
into a tested lifecycle helper while preserving runtime behavior.

Scope:
- Add a `RuntimeTurnFinalizer` boundary under `coding_agent.runs.lifecycle`.
- Move turn outcome status/result mapping and store-backed/storeless completion
  handling into the helper.
- Update `SessionManager.run_agent` to delegate after-turn finalization.
- Update ADR-0058 follow-up status for this behavior-preserving slice.

Out of scope:
- Moving error handling, wire consumer setup, checkpoint restore, or runtime
  close policy out of `SessionManager`.
- Changing runtime store schema, wire protocol, CLI behavior, or executor
  selection.
- Implementing daemon-backed clients or sandbox defaults.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runs/lifecycle.py
  - src/coding_agent/server/session_manager.py
  - tests/coding_agent/test_run_lifecycle.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_run_lifecycle.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or agent_run_marks_interrupted_outcome_as_interrupted or run_agent_executes_local_runtime_through_local_daemon_executor" -v
- uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py

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
