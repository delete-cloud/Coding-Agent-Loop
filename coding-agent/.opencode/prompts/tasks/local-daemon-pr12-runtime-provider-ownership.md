Goal:
Move local runtime preparation ownership further behind LocalDaemonExecutor.

Scope:
- Introduce LocalDaemonRuntimeProvider and LocalDaemonRuntimeBinding.
- Make LocalDaemonExecutor.execute_runtime prepare runtime through the provider
  before invoking the adapter turn.
- Let SessionManager pass a session-backed provider and a before-turn hook
  instead of preparing pipeline/ctx/adapter inline before executor execution.
- Keep local daemon run behavior stable.

Out of scope:
- Do not move all Session state persistence out of SessionManager in this slice.
- Do not implement ManagedPoolExecutor, LocalAttached, or ExternalWorker.
- Do not change HTTP API schemas or persisted session payloads.
- Do not rewrite REPL/daemon client transport.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/executors/local_daemon.py`
- `src/coding_agent/executors/__init__.py`
- `src/coding_agent/server/session_manager.py`
- `tests/coding_agent/test_local_daemon_executor.py`
- `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "local_daemon_executor or cloud_runtime or bootstrap_cloud_runtime" -v`
- `uv run ruff check src/coding_agent/executors src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/ui/test_session_manager_runtime.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
