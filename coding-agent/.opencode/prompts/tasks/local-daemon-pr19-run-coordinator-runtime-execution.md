# Task Packet: Route Local Runtime Execution Through RunCoordinator

## Goal

Move the local runtime execution delegation in `SessionManager.run_agent()` from
a direct `LocalDaemonExecutor` call to the `RunCoordinator` boundary.

## Scope

- Extend `RunCoordinator` with a runtime-execution delegation method.
- Make `DefaultRunCoordinator` select the local daemon executor for local
  runtime execution.
- Keep the existing `RunRequest` submission behavior unchanged.
- Make `SessionManager.run_agent()` call the coordinator for runtime execution
  after it has submitted the run request.
- Preserve the current local daemon runtime provider, before/after hooks,
  approval/error handling, observation, and persistence behavior.

## Out Of Scope

- Durable `RunRecord` storage.
- EventStore / DisplayEvent projection.
- Local daemon process/client product path.
- Cloud managed runtime execution.
- Moving session-specific persistence hooks out of `SessionManager`.

## Context

ADR-0058 says `RunCoordinator` selects the executor from `RunTarget`, and the
executor owns runtime execution. Previous PRs moved local runtime preparation,
turn execution, and checkpoint restore behind `LocalDaemonExecutor`, but
`SessionManager.run_agent()` still invokes `_local_daemon_executor.execute_runtime`
directly.

## Acceptance Criteria

- `RunCoordinator` exposes a runtime execution method.
- `DefaultRunCoordinator` delegates local daemon runtime execution to the local
  daemon executor.
- `DefaultRunCoordinator` rejects local runtime execution when the selected
  executor is unsupported or missing.
- `SessionManager.run_agent()` uses `RunCoordinator.execute_runtime()` instead
  of calling `_local_daemon_executor.execute_runtime()` directly.
- Existing local runtime behavior remains unchanged.

## Target Tests

```bash
uv run pytest tests/coding_agent/test_run_coordinator.py -v
uv run pytest tests/ui/test_session_manager_runtime.py -k "run_agent_submits_run_request_to_run_coordinator or run_agent_executes_local_runtime_through_run_coordinator or run_agent_executes_local_runtime_through_local_daemon_executor" -v
uv run pytest tests/ui/test_session_manager_runtime.py -v
uv run pytest tests/coding_agent/test_local_daemon_executor.py -v
uv run ruff check src/coding_agent/runs/coordinator.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_coordinator.py tests/ui/test_session_manager_runtime.py
git diff --check
```
