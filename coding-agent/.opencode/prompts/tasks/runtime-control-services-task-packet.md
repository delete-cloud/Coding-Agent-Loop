Goal:
Move runtime control service construction out of SessionManager so query,
resume, attached-executor, cancel, recovery, persistence, and task-stop service
composition lives under coding_agent.runs.

Scope:
- Add RuntimeControlServices under src/coding_agent/runs/.
- Use lazy providers for runtime store and owner recoverability so existing
  runtime-store reconfiguration and tests keep working.
- Replace SessionManager's private runtime service factory helpers with
  RuntimeControlServices calls.
- Add focused tests for lazy store usage, metadata wiring, and recovery policy.
- Update ADR-0058 follow-up status.

Out of scope:
- Changing query, resume, cancel, attached executor, or recovery behavior.
- Changing runtime store persistence formats or event/display replay semantics.
- Changing local daemon execution, CLI, HTTP, or webui surfaces.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/runs/query.py
  - src/coding_agent/runs/resume.py
  - src/coding_agent/runs/attached_executor.py
  - src/coding_agent/runs/cancel.py
  - src/coding_agent/runs/recovery.py
  - src/coding_agent/runs/lifecycle.py

Target tests:
- uv run pytest tests/coding_agent/test_runtime_control_services.py -v
- uv run pytest tests/coding_agent/test_runtime_query_service.py tests/coding_agent/test_runtime_resume_service.py -v
- uv run pytest tests/coding_agent/test_runtime_cancel_service.py tests/coding_agent/test_runtime_attached_executor_service.py -v
- uv run pytest tests/coding_agent/test_runtime_run_recovery.py tests/coding_agent/test_run_lifecycle.py -v
- uv run pytest tests/coding_agent/test_runtime_run_persistence.py tests/coding_agent/test_runtime_turn_service.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "runtime_metadata or cancel or close_session or shutdown_session_runtime" -v
- uv run pytest tests/ui/test_session_manager_public_api.py -k "close or delete or teardown or cancel" -v
- uv run ruff check src/coding_agent/runs/control_services.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_control_services.py
- uv run ruff format --check src/coding_agent/runs/control_services.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_control_services.py

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
