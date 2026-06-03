# ADR-0059: Remove ExecutionBinding Compatibility Model

**Status**: Accepted
**Date**: 2026-06-04

## Context

ADR-0058 introduced `RunTarget` as the canonical placement model and left
`ExecutionBinding` as a migration compatibility layer. That compatibility layer
has now become architectural drag: new code can still accept and persist
`execution_binding`, runtime metadata still leaks `execution_binding_kind`, and
environment resolution still routes cloud workspaces through binding-shaped
objects.

The next boundary cleanup is to finish the migration. New code should speak in
terms of `RunTarget`, `WorkspaceRef`, `ExecutorRef`, and `IsolationPolicy`.
Legacy stored session payloads may still contain `execution_binding`; that is a
parse-time migration concern, not a runtime/session object concern.

## Decision

Remove `ExecutionBinding` from the runtime model and new API surface.

`Session` and `SessionRecord` will store only `default_run_target` for placement.
If a legacy stored session lacks `default_run_target` but includes
`execution_binding`, `SessionRecord.from_store_data()` may convert that
dictionary into a `RunTarget` and then discard the binding representation. The
converted `Session` must not expose an `execution_binding` attribute and must
not write `execution_binding` back to durable metadata.

HTTP `POST /sessions` will reject new `execution_binding` request bodies and
will accept explicit `default_run_target` or `run_target` placement payloads
alongside the existing `repo_path` local shortcut. Runtime metadata will expose
placement in executor/workspace terms such as `executor_kind`,
`workspace_surface`, and `execution_plane`; it must not emit
`execution_binding_kind` for newly recorded runs or events.

Cloud workspace environment resolution will use `CloudWorkspaceRef` directly.
Provider APIs that must return or consume cloud workspace identity will use
cloud workspace references or provider-specific payloads, not
`CloudWorkspaceBinding`.

## Alternatives Rejected

- Keep `ExecutionBinding` as a hidden in-memory adapter — rejected because it
  preserves the old abstraction and lets new code keep depending on binding
  semantics indirectly.
- Keep accepting `execution_binding` in HTTP requests while only dropping it
  from persistence — rejected because clients would still target the old
  contract.
- Delete all legacy parsing immediately — rejected because existing stored
  sessions without `default_run_target` still need deterministic migration.

## Acceptance Criteria

- [x] `Session` and `SessionRecord` have no `execution_binding` field.
- [x] `SessionRecord.to_store_data()` does not emit `execution_binding`.
- [x] Legacy stored `execution_binding` payloads are converted into
  `default_run_target` during parsing only.
- [x] `POST /sessions` rejects `execution_binding` and accepts
  `default_run_target` / `run_target`.
- [x] New runtime run/event/observation metadata does not emit
  `execution_binding_kind`.
- [x] Cloud workspace environment resolution uses `CloudWorkspaceRef` directly.
- [x] `src/coding_agent/environment/execution_binding.py` and binding-only
  resolver APIs are deleted or reduced to migration-only code outside new
  runtime paths.
- [x] `uv run pytest tests/coding_agent/test_run_target.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server_workspace_transfer.py tests/dogfood/test_workspace_provider_demo.py tests/cli/test_remote_client.py tests/coding_agent/test_runtime_environment.py tests/coding_agent/test_runtime_workspace_export_service.py tests/coding_agent/test_workspace_action_routing.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/coding_agent/test_runtime_metadata_service.py tests/coding_agent/test_runtime_wire_event_recorder.py tests/coding_agent/test_runtime_context_binding_service.py tests/coding_agent/test_runtime_observation_service.py tests/coding_agent/test_runtime_attached_executor_service.py tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_runtime_run_recovery.py tests/ui/test_server_compat_imports.py -q`
- [x] `uv run pytest tests/ui/test_http_server.py -k "create_session or run_target or execution_binding or external_worker or local_attached or attached_executor_alias or workers_endpoint or worker_metadata or workspace_diff or workspace_patch or publish_branch or publish_pr or get_session_response or workspace_provider" -q`
- [x] `uv run ruff check src/coding_agent/server/session_manager.py src/coding_agent/server/http_server.py src/coding_agent/server/schemas.py src/coding_agent/runs src/coding_agent/environment src/coding_agent/remote src/coding_agent/runtime_store.py tests/ui/test_http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server_workspace_transfer.py tests/dogfood/test_workspace_provider_demo.py tests/cli/test_remote_client.py tests/coding_agent/test_run_target.py tests/coding_agent/test_runtime_environment.py tests/coding_agent/test_runtime_workspace_export_service.py tests/coding_agent/test_workspace_action_routing.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/coding_agent/test_runtime_metadata_service.py tests/coding_agent/test_runtime_wire_event_recorder.py tests/coding_agent/test_runtime_context_binding_service.py tests/coding_agent/test_runtime_observation_service.py tests/coding_agent/test_runtime_attached_executor_service.py tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_runtime_run_recovery.py tests/coding_agent/test_pg_runtime_store.py tests/ui/test_server_compat_imports.py`

## References

- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/runs/target.py`
- `src/coding_agent/environment/workspace_provider.py`
