Goal:
Move normal local-daemon runtime preparation composition out of `SessionManager`
and into a narrower runtime-preparation service boundary.

Scope:
- Add a service that owns normal local-daemon runtime target validation,
  environment resolution, workspace-root compatibility checks, and
  `LocalDaemonSessionRuntimeProvider` assembly.
- Keep `SessionManager` responsible for session locking, owner checks,
  persistence hooks, and checkpoint restore orchestration.
- Update ADR-0058 implementation status for the normal runtime construction gap.

Out of scope:
- Live `DisplayEvent` projection for SSE/UI streams.
- Sandbox policy defaulting.
- Daemon-backed CLI/REPL product entrypoints.
- Cloud managed runtime execution.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/runs/runtime_preparation.py`
  - `src/coding_agent/executors/local_daemon.py`
  - `tests/coding_agent/test_runtime_preparation.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_preparation.py -v`
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py -k "session_runtime_provider" -v`
- `uv run pytest tests/coding_agent/test_runtime_turn_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent or runtime_close or owner_conflict" -v`
- `uv run pytest tests/ui/test_http_server_failover.py tests/ui/test_session_manager_public_api.py -k "teardown or close or delete or event" -v`
- `uv run ruff check src/coding_agent/runs/runtime_preparation.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_preparation.py`

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
