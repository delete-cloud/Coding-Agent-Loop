Goal:
Move local daemon runtime turn-error lifecycle handling into the
LocalDaemonExecutor execution sequence.

Scope:
- Add an on-turn-error hook to LocalDaemonRuntimeExecution.
- Make LocalDaemonExecutor.execute_runtime invoke the hook when
  adapter.run_turn raises, including asyncio.CancelledError.
- Let SessionManager pass a session-backed hook that handles fatal, cancel,
  and generic runtime turn errors.
- Keep bootstrap/provider preparation failure behavior stable as a
  SessionManager fallback.

Out of scope:
- Do not handle prepare_runtime or before_turn failures inside the executor
  turn-error hook.
- Do not change HTTP/CLI API schemas or persisted payloads.
- Do not split stores or implement ManagedPoolExecutor/LocalAttached.
- Do not alter successful turn lifecycle moved in PR-13.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/executors/local_daemon.py`
- `src/coding_agent/executors/__init__.py`
- `src/coding_agent/server/session_manager.py`
- `tests/coding_agent/test_local_daemon_executor.py`
- `tests/ui/test_session_manager_runtime.py`
- `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "local_daemon_executor or closes_cached_runtime_after_turn_failure or reraises_fatal_tool_execution_error or reraises_owner_conflict or cancel_session_turn or bootstrap_fails" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "run_agent or ensure_session_runtime" -v`
- `uv run ruff check src/coding_agent/executors src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
