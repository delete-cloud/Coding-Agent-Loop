Goal:
Move local daemon turn begin/final cleanup bookkeeping out of
`SessionManager.run_agent` and into a runtime lifecycle helper.

Scope:
- Add `RuntimeTurnSessionState` under `coding_agent.runs.lifecycle`.
- Move turn begin metadata mutation and final cleanup/persist logic out of
  `SessionManager.run_agent`.
- Preserve existing `current_turn_id`, `turn_in_progress`, `turn_status`,
  `last_failure_details`, `last_activity`, and task ownership behavior.
- Update ADR-0058 follow-up notes.
- Add focused unit coverage for the new lifecycle helper.

Out of scope:
- Changing turn lock or workspace export gating.
- Changing run id generation.
- Changing runtime run store status behavior.
- Moving checkpoint restore, wire consumer setup, or runtime close policy.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_creates_run_id_and_preserves_current_turn_id_alias or run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_rejects_concurrent_turn_for_same_session" -v
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
