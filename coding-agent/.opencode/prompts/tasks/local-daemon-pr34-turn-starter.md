Goal:
Move local daemon before-turn runtime wiring out of `SessionManager.run_agent`
into a tested `RuntimeTurnStarter` lifecycle helper.

Scope:
- Add `RuntimeTurnStarter` under `coding_agent.runs.lifecycle`.
- Move root run identity binding, runtime run start, adapter consumer binding,
  runtime message bus wiring, wire consumer wiring, subagent publisher binding,
  and observation start orchestration into the helper.
- Keep `SessionManager` responsible for supplying concrete callbacks and
  storing the returned observation recorder.
- Preserve runtime execution, store writes, wire protocol, and error semantics.

Out of scope:
- Extracting fatal/cancel/generic error action bodies.
- Changing checkpoint restore, sandbox behavior, CLI/daemon product routing, or
  persisted run/session schemas.
- Moving observation implementation or message publisher implementation.

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
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_persists_agent_run_lifecycle_when_store_configured or run_agent_executes_local_runtime_through_local_daemon_executor or run_agent_marks_agent_run_failed_when_turn_outcome_errors or run_agent_records_error_outcome_without_runtime_store or agent_run_marks_interrupted_outcome_as_interrupted or run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn" -v
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
