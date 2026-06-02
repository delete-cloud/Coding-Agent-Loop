# Task Packet: Checkpoint Restore Through LocalDaemonExecutor

## Goal

Route local checkpoint restore runtime rebuilds through `LocalDaemonExecutor`
so restore uses the same executor-owned runtime preparation boundary as normal
local runtime setup.

## Scope

- Keep checkpoint/tape validation and truncation behavior unchanged.
- Extract restore-specific runtime construction behind a local preparation
  boundary.
- Route local-daemon restore targets through `LocalDaemonExecutor.prepare_runtime()`.
- Keep cloud/non-local restore runtime builders on the current compatibility
  direct path.
- Preserve provider reuse, plugin state injection, restore consumer binding, and
  session config rewind behavior.

## Out Of Scope

- Full process-level resume.
- Moving checkpoint stores or tape stores.
- Changing cloud managed restore behavior.
- Moving turn lifecycle persistence hooks.

## Context

ADR-0058 says executor owns runtime execution and ControlPlane should not run the
agent loop. PR #439 moved `ensure_session_runtime()` and model-switch rebuilds
behind `LocalDaemonExecutor.prepare_runtime()`, but checkpoint restore still
constructs the local runtime directly inside `SessionManager`.

## Acceptance Criteria

- Local checkpoint restore calls `LocalDaemonExecutor.prepare_runtime()`.
- The restore preparation request uses `session.default_run_target`.
- Restore runtime construction uses the validated preparation request target,
  not mutable session state re-read after executor validation.
- Invalid local-daemon restore targets are rejected by `LocalDaemonExecutor`
  before the agent builder runs.
- Existing checkpoint restore tests still pass.

## Target Tests

`tests/ui/test_session_manager_runtime.py` is included intentionally because the
existing checkpoint restore and postmortem regression coverage lives there.

```bash
uv run pytest tests/ui/test_session_manager_runtime.py -k "restore_checkpoint_uses_default_run_target_workspace or restore_checkpoint_builds_from_preparation_target or restore_checkpoint_rejects_local_daemon_non_local_workspace" -v
uv run pytest tests/ui/test_session_manager_runtime.py -k "restore" -v
uv run pytest tests/ui/test_session_manager_runtime.py -v
uv run pytest tests/cli/test_commands.py -k "checkpoint_restore" -v
uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py
git diff --check
```
