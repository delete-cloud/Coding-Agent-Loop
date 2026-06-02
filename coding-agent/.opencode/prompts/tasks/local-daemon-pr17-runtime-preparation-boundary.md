# Task Packet: Local Runtime Preparation Through Executor

## Goal

Move local REPL/runtime rebuild preparation behind `LocalDaemonExecutor` so
non-turn local runtime setup is no longer constructed directly by
`SessionManager`.

## Scope

- Add a LocalDaemonExecutor runtime-preparation API that validates the
  `RunTarget` and delegates to a runtime provider.
- Make local `ensure_session_runtime()` and
  `replace_session_runtime_config()` paths use this executor preparation
  boundary.
- Keep cloud/non-local runtime builders on the current compatibility path.
- Do not move turn lifecycle persistence hooks or checkpoint restore in this
  slice.

## Context

ADR-0058 says:

```text
Executor owns runtime execution.
ControlPlane does not run the agent loop.
Client is not the executor.
```

Previous PRs moved the normal `run_agent()` adapter turn through
`LocalDaemonExecutor`, but REPL session switching and model-switch runtime
rebuilds still call `_build_session_runtime()` directly from `SessionManager`.

## Acceptance Criteria

- `LocalDaemonExecutor` exposes a preparation boundary for local runtime setup.
- The preparation boundary rejects non-local-daemon targets and non-local
  workspaces before invoking the runtime provider.
- Local `ensure_session_runtime()` calls that build a runtime go through
  `LocalDaemonExecutor`.
- Local `replace_session_runtime_config()` calls that rebuild a runtime go
  through `LocalDaemonExecutor`.
- Existing default-run-target workspace placement behavior remains unchanged.

## Target Tests

`tests/ui/test_session_manager_runtime.py` is included intentionally because the
existing SessionManager runtime and postmortem regression coverage lives there.

```bash
uv run pytest tests/coding_agent/test_local_daemon_executor.py -v
uv run pytest tests/ui/test_session_manager_runtime.py -k "ensure_session_runtime_uses_default_run_target_workspace or replace_session_runtime_config_uses_default_run_target_workspace or run_agent_executes_local_runtime_through_local_daemon_executor or builds_from_preparation_target or rejects_local_daemon_non_local_workspace" -v
uv run ruff check src/coding_agent/executors/local_daemon.py src/coding_agent/server/session_manager.py tests/coding_agent/test_local_daemon_executor.py tests/ui/test_session_manager_runtime.py
git diff --check
```
