Goal:
Move run-turn outer exception routing out of SessionManager and into RuntimeTurnController.

Scope:
- Add a RuntimeTurnController helper that runs an execution coroutine and routes raised exceptions through existing turn error policy.
- Preserve fatal/cancelled re-raise behavior.
- Preserve generic error handling and RunCoordinatorError ensure-started behavior.
- Keep SessionManager as the composition root for wiring runtime execution and callbacks.
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
  - src/coding_agent/runs/lifecycle.py
  - src/coding_agent/server/session_manager.py
  - tests/coding_agent/test_run_lifecycle.py
  - tests/ui/test_session_manager_runtime.py
  - tests/ui/test_http_server.py

Target tests:
- uv run pytest tests/coding_agent/test_run_lifecycle.py -k "runtime_turn_controller" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "turn_failure or owner_conflict or fatal_tool or run_coordinator or runtime_close" -v
- uv run pytest tests/ui/test_http_server.py -k "fatal_tool or owner_conflict or prompt_streams" -v
- uv run ruff check src/coding_agent/runs/lifecycle.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py

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
