Goal:
Move local daemon runtime preparation out of SessionManager into a dedicated
runtime provider boundary, while preserving the current run path behavior.

Scope:
- Add a LocalDaemonSessionRuntimeProvider under coding_agent.executors.
- Move local target environment resolution, cached runtime reuse validation,
  local runtime creation, tape restoration, consumer binding setup, and binding
  construction from SessionManager into that provider.
- Keep SessionManager.run_agent() as the public control-plane method, but make
  it delegate runtime preparation through the provider.
- Keep LocalDaemonExecutor and RunCoordinator behavior unchanged.
- Refresh ADR-0058 follow-up status for the runtime preparation ownership slice.

Out of scope:
- Change local daemon runtime execution semantics.
- Change checkpoint restore runtime preparation.
- Move run lifecycle bookkeeping, observation callbacks, approval handling, or
  message snapshot persistence out of SessionManager.
- Add a daemon process/client product path.
- Change store schemas, HTTP routes, CLI behavior, or live stream protocols.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/executors/local_daemon.py
  - src/coding_agent/server/session_manager.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_local_daemon_executor.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "local_daemon_executor or coordinator_integration or default_run_target_workspace or rebuilds_live_runtime_when_default_run_target_changes" -v
- uv run ruff check src/coding_agent/executors src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py
- uv run ruff format --check src/coding_agent/executors src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py
- git diff --check

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 runtime ownership, behavior regression,
  compatibility, import-boundary, or test-gap findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.

Stop conditions:
- Stop if this requires changing public HTTP/CLI behavior.
- Stop if this requires changing persisted payloads or runtime store schemas.
- Stop if this expands into daemon process/client implementation.
