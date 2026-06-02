Goal:
Introduce the first ADR-0058 `LocalDaemonExecutor` boundary and route default
local daemon run coordination through it, without moving runtime ownership yet.

Scope:
- Add a `coding_agent.executors` package for run-executor product boundaries.
- Add `LocalDaemonExecutor` as the first production executor skeleton.
- Validate that `LocalDaemonExecutor` only accepts
  `LocalDaemonExecutorRef + LocalPathWorkspaceRef` targets.
- Allow `DefaultRunCoordinator` to delegate local daemon run requests to an
  injected run executor.
- Configure `SessionManager`'s default coordinator with `LocalDaemonExecutor`
  so the real default run path crosses the executor boundary.
- Add focused tests for executor target validation and coordinator delegation.

Out of scope:
- Do not move pipeline/context/adapter/runtime ownership out of
  `SessionManager` in this slice.
- Do not implement daemon lifecycle, IPC, or background worker management.
- Do not change HTTP or CLI APIs.
- Do not change persisted session payloads or runtime store schemas.
- Do not demote `coding_agent run` in this slice.
- Do not implement managed pool, local attached, or cloud executor behavior.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- Relevant files:
  - `src/coding_agent/executors/`
  - `src/coding_agent/runs/coordinator.py`
  - `src/coding_agent/runs/target.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_local_daemon_executor.py`
  - `tests/coding_agent/test_run_coordinator.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run ruff check src/coding_agent/executors src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py tests/ui/test_session_manager_runtime.py`
- `uv run basedpyright src/coding_agent/executors src/coding_agent/runs tests/coding_agent/test_local_daemon_executor.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py`
- `git diff --check`

Known type-check caveat:
- `session_manager.py` and `test_session_manager_runtime.py` still have
  pre-existing strict typing debt. This slice type-checks the new executor and
  run modules directly.

Loop policy:
- Engineer implements the smallest executor boundary and runs the target tests.
- Reviewer reports only P1/P2 correctness, safety, or scope issues.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Stop after one review/fix/retest cycle unless a human expands the scope.

Stop conditions:
- Stop if runtime ownership migration becomes necessary.
- Stop if daemon lifecycle or IPC design is required.
- Stop if persisted schemas or public APIs must change.
