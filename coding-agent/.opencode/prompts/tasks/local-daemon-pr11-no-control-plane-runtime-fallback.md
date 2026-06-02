Goal:
Stop the control plane from directly executing runtime turns for RunTarget
executors that do not have an implemented executor path.

Scope:
- Remove the `SessionManager.run_agent()` fallback that calls
  `adapter.run_turn(prompt)` for non-local-daemon executor targets.
- Mark unsupported executor runs failed through the existing run failure path.
- Keep local daemon execution behavior unchanged.
- Add focused regression coverage for managed-pool/cloud targets.

Out of scope:
- Do not implement ManagedPoolExecutor.
- Do not implement LocalAttached/ExternalWorker execution.
- Do not move pipeline/bootstrap construction into LocalDaemonExecutor in this
  slice.
- Do not change RunTarget persistence or HTTP API schemas.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/server/session_manager.py`
- `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "local_daemon_executor or cloud_runtime or cloud_environment_from_execution_binding" -v`
- `uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
