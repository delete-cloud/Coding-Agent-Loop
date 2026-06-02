Goal:
Let RunCoordinator own unsupported runtime executor rejection during HTTP/local session runs while preserving existing runtime-store failure records.

Scope:
- Remove SessionManager's direct non-local executor rejection path from run_agent.
- Add a focused regression proving managed/cloud runtime execution is passed to RunCoordinator.execute_runtime before rejection.
- Preserve current behavior for runtime_store run creation, running update, failed update, and user-visible error.

Out of scope:
- Implement managed/cloud runtime execution.
- Change RunTarget, executor selection, workspace binding, or cloud workspace semantics.
- Rename or move SessionManager modules.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/runs/coordinator.py
  - tests/ui/test_session_manager_runtime.py
  - tests/coding_agent/test_run_coordinator.py

Target tests:
- uv run pytest tests/ui/test_session_manager_runtime.py -k "does_not_bootstrap_cloud_runtime_from_execution_binding or routes_unsupported_runtime_through_run_coordinator or does_not_route_cloud_runtime_through_local_daemon_executor" -v
- uv run pytest tests/coding_agent/test_run_coordinator.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -v
- uv run pytest tests/ui/test_http_server_failover.py -k "events or event_queue or owner_change or stale_owner or stream or queue_registration" -v
- uv run pytest tests/ui/test_session_manager_public_api.py -k "event_queue or cleanup or teardown" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "event_queues or clear_sessions or close_session or shutdown_session_runtime or cleanup" -v
- uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py tests/coding_agent/test_run_coordinator.py
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
