Goal:
Move the local runtime turn invocation from a `SessionManager`-owned callable
wrapper into `LocalDaemonExecutor` as the next ADR-0058 executor-ownership
slice.

Scope:
- Replace `LocalDaemonRuntimeExecution.run` with explicit `adapter` and `prompt`
  fields.
- Add a small runtime adapter protocol for the executor boundary.
- Let `LocalDaemonExecutor.execute_runtime(...)` validate the local daemon target
  and call `adapter.run_turn(prompt)` itself.
- Update `SessionManager.run_agent()` so local daemon targets pass the adapter
  and prompt to `LocalDaemonExecutor` instead of passing a runtime callable.
- Preserve existing run lifecycle, runtime store, HTTP, CLI, approval, and
  checkpoint behavior.

Out of scope:
- Do not move pipeline/context/adapter preparation out of `SessionManager` in
  this slice.
- Do not implement daemon IPC, background worker lifecycle, or reconnect.
- Do not change persisted session payloads or runtime store schemas.
- Do not change public HTTP or CLI APIs.
- Do not implement managed pool, local attached, or cloud executor execution.
- Do not demote `coding_agent run` in this slice.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- Relevant files:
  - `src/coding_agent/executors/local_daemon.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_local_daemon_executor.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run ruff check src/coding_agent/executors src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py tests/ui/test_session_manager_runtime.py`
- `uv run basedpyright src/coding_agent/executors src/coding_agent/runs tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py`
- `git diff --check`

Known type-check caveat:
- `session_manager.py` and `test_session_manager_runtime.py` still have
  pre-existing strict typing debt. This slice type-checks the executor and run
  surfaces directly.

Loop policy:
- Engineer implements the smallest adapter-owned runtime turn shift and runs
  the target tests.
- Reviewer reports only P1/P2 correctness, safety, or scope issues.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Stop after one review/fix/retest cycle unless a human expands the scope.

Stop conditions:
- Stop if full runtime preparation migration is required.
- Stop if daemon lifecycle or IPC design is required.
- Stop if persisted schemas or public APIs must change.
