# Task Packet: Restore Runtime Placement From RunTarget

## Goal

Fix the remaining PR #437 placement regressions where checkpoint restore and
legacy runtime builders still resolve the execution environment from
`Session.execution_binding` instead of canonical `Session.default_run_target`.

## Scope

- Update checkpoint restore runtime rebuild to resolve local runtime placement
  from `session.default_run_target`.
- Update `_build_session_runtime()` callers, including `ensure_session_runtime()`
  and `replace_session_runtime_config()`, to use the same RunTarget-aware
  placement path.
- Keep `execution_binding` as compatibility metadata.
- Keep cloud/non-local behavior explicitly rejected for local runtime builders.
- Do not implement Cloud Managed execution or LocalAttached/ExternalWorker.

## Context

ADR-0058 established `RunTarget` as canonical placement:

```text
RunTarget = WorkspaceRef + ExecutorRef + IsolationPolicy + RunConstraints
```

PR #437 persisted `Session.default_run_target` and normal `run_agent()` now
prepares local runtime from `RunRequest.target`. A retrospective local review
found two remaining P2 issues:

- `_restore_checkpoint()` still calls `_resolve_environment(session)`, which
  reads `session.execution_binding`.
- `_build_session_runtime()` still calls `_resolve_environment(session)`, which
  affects `ensure_session_runtime()` and `replace_session_runtime_config()`.

## Acceptance Criteria

- If `execution_binding` points to workspace A and `default_run_target` points
  to workspace B, checkpoint restore builds runtime in workspace B.
- The same divergent session builds runtime in workspace B for
  `ensure_session_runtime()`.
- The same divergent session builds runtime in workspace B for
  `replace_session_runtime_config()`.
- Existing cloud binding restore tests keep passing.
- Existing default-run-target run-agent tests keep passing.

## Target Tests

```bash
uv run pytest tests/ui/test_session_manager_runtime.py -k "default_run_target or restore_checkpoint_preserves_cloud_execution_binding or restore_checkpoint_preserves_execution_binding or ensure_session_runtime_uses_default_run_target or replace_session_runtime_config_uses_default_run_target" -v
uv run pytest tests/ui/test_session_persistence.py -k "default_run_target or SessionRecord or runtime_handle" -v
uv run pytest tests/coding_agent/test_run_target.py -v
uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py
git diff --check
```
