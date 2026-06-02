Goal:
Move local daemon turn-success lifecycle handling into the
LocalDaemonExecutor execution sequence.

Scope:
- Add an after-turn hook to LocalDaemonRuntimeExecution.
- Make LocalDaemonExecutor.execute_runtime run the after-turn hook after
  adapter.run_turn and before returning the runtime result.
- Move SessionManager's successful turn snapshot/finish/observation/persist
  handling into the after-turn hook passed to LocalDaemonExecutor.
- Keep existing runtime behavior and HTTP/CLI API shape stable.

Out of scope:
- Do not move fatal/cancel/exception handling out of SessionManager in this
  slice.
- Do not split stores or change persisted payload schemas.
- Do not implement ManagedPoolExecutor, LocalAttached, or ExternalWorker.
- Do not rewrite daemon/client transport.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/executors/local_daemon.py`
- `src/coding_agent/server/session_manager.py`
- `tests/coding_agent/test_local_daemon_executor.py`
- `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "local_daemon_executor or cloud_runtime or bootstrap_cloud_runtime" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "run_agent or ensure_session_runtime" -v`
- `uv run ruff check src/coding_agent/executors src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/ui/test_session_manager_runtime.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
