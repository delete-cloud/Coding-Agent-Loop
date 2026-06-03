Goal:
Move runtime close policy out of SessionManager behind a focused runtime lifecycle service.

Scope:
- Add a RuntimeCloser service that owns runtime-handle invalidation and adapter close semantics.
- Keep existing SessionManager public/private call sites behavior-compatible.
- Preserve async close awaiting, sync-safe close scheduling, and close-failure propagation behavior.
- Update ADR-0058 implementation status for this completed ownership slice.

Out of scope:
- Normal runtime construction extraction.
- Event/display live stream projection.
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
  - tests/ui/test_session_manager_public_api.py
  - tests/ui/test_http_server_failover.py
- Postmortems:
  - postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md

Target tests:
- uv run pytest tests/coding_agent/test_run_lifecycle.py -k "runtime_closer" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "close or remove_session_async or clear_sessions or register_session or runtime_close" -v
- uv run pytest tests/ui/test_session_manager_public_api.py -k "close or remove_session_async or clear_sessions or register_session" -v
- uv run pytest tests/ui/test_http_server_failover.py -k "close or teardown or cleanup" -v
- uv run ruff check src/coding_agent/runs/lifecycle.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server_failover.py

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
