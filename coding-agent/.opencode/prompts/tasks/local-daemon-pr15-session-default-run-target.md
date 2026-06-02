Goal:
Persist canonical session default RunTarget metadata while keeping
ExecutionBinding as a compatibility field.

Scope:
- Add RunTarget serialization/deserialization helpers.
- Add SessionRecord.default_run_target and Session.default_run_target.
- Derive default_run_target from execution_binding for legacy payloads.
- Keep execution_binding serialized for compatibility.
- Make runtime RunRequest use session.default_run_target.

Out of scope:
- Do not remove ExecutionBinding from persisted payloads.
- Do not migrate every execution_binding call site in this slice.
- Do not implement ManagedPoolExecutor, LocalAttached, or ExternalWorker.
- Do not change HTTP/CLI API schemas beyond adding default_run_target metadata.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/runs/target.py`
- `src/coding_agent/runs/__init__.py`
- `src/coding_agent/server/session_manager.py`
- `tests/coding_agent/test_run_target.py`
- `tests/ui/test_session_persistence.py`
- `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_run_target.py -v`
- `uv run pytest tests/ui/test_session_persistence.py -k "SessionRecord or default_run_target or runtime_state" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_submits_run_request_to_run_coordinator or rebuilds_live_runtime_when_default_run_target_changes or local_daemon_executor or cloud_runtime" -v`
- `uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_run_target.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_runtime.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
