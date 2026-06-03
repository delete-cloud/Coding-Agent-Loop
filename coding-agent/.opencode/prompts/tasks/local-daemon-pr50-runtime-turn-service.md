Goal:
Move run-turn composition root and runtime execution callback assembly out of SessionManager.

Scope:
- Add a RuntimeTurnService that owns turn state begin/finalize, run tracking, observation state, wire error notification, controller setup, and LocalDaemonRuntimeExecution callback assembly.
- Keep SessionManager responsible for turn locking, ownership checks, session lookup, and dependency composition.
- Preserve RunCoordinator.execute_runtime as the real runtime execution path.
- Preserve fatal/cancelled re-raise behavior, RunCoordinatorError ensure-started handling, and generic error wire notification.
- Update ADR-0058 implementation status for this ownership slice.

Out of scope:
- Runtime construction extraction.
- Live DisplayEvent projection.
- Sandbox wrapper defaults.
- Daemon-backed client surfaces.
- Cloud managed or local-attached executor implementation.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runs/turn_execution.py
  - src/coding_agent/runs/lifecycle.py
  - src/coding_agent/server/session_manager.py
  - tests/coding_agent/test_runtime_turn_service.py
  - tests/ui/test_session_manager_runtime.py
  - tests/ui/test_http_server.py
- Postmortems:
  - postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md

Target tests:
- uv run pytest tests/coding_agent/test_runtime_turn_service.py -v
- uv run pytest tests/coding_agent/test_run_lifecycle.py -k "runtime_turn_controller" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent or turn_failure or owner_conflict or fatal_tool or run_coordinator or runtime_close" -v
- uv run pytest tests/ui/test_http_server.py -k "fatal_tool or owner_conflict or prompt_streams" -v
- uv run ruff check src/coding_agent/runs/turn_execution.py src/coding_agent/runs/lifecycle.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_service.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py

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
