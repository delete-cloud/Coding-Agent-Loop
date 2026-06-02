Goal:
Move local daemon turn run-start bookkeeping out of `SessionManager.run_agent`
into a tested `RuntimeTurnRunTracker` helper.

Scope:
- Add `RuntimeTurnRunTracker` under `coding_agent.runs.lifecycle`.
- Replace `agent_run_created` nonlocal state in `run_agent` with the tracker.
- Keep existing run lifecycle store writes, error behavior, wire behavior, and
  runtime execution unchanged.
- Add focused lifecycle tests for start-once and finish-if-started behavior.

Out of scope:
- Extracting local daemon error handling or wire notification policy.
- Moving before-turn wire consumer setup or observation start out of
  `SessionManager`.
- Changing runtime store schemas, status names, or persisted metadata.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or agent_run_marks_interrupted_outcome_as_interrupted or run_agent_executes_local_runtime_through_local_daemon_executor" -v
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
