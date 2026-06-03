Goal:
Remove `ExecutionBinding` from new code paths. Sessions accept and persist `default_run_target` as the only canonical placement contract; legacy stored `execution_binding` payloads are parsed only to migrate old session records into `RunTarget`.

Scope:
- Replace `Session.execution_binding` and `SessionRecord.execution_binding` with `default_run_target`-only state.
- Stop accepting `execution_binding` in new HTTP `POST /sessions` requests; accept explicit `default_run_target`/`run_target` plus the existing `repo_path` shortcut.
- Convert legacy stored `execution_binding` dictionaries to `default_run_target` only during session record parsing, without storing a binding object on `Session`.
- Rename runtime metadata away from `execution_binding_kind` toward executor/workspace/placement fields.
- Remove binding-only environment APIs and update cloud workspace resolution to use `CloudWorkspaceRef` directly.

Out of scope:
- Implementing Cloud Managed Mode.
- Implementing a new sandbox backend.
- Rewriting historical archived design documents except for current ADR/task references needed by this change.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `docs/adr/0059-remove-execution-binding-compatibility-model.md`
- Postmortems:
  - `postmortem/patterns/PM-0024-preserve-cloud-workspaces-until-cleanup-is-verified.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/server/schemas.py`
  - `src/coding_agent/runs/target.py`
  - `src/coding_agent/runs/metadata.py`
  - `src/coding_agent/runs/runtime_events.py`
  - `src/coding_agent/runs/context_binding.py`
  - `src/coding_agent/runs/environment.py`
  - `src/coding_agent/environment/workspace_provider.py`
  - `src/coding_agent/environment/docker_workspace_provider.py`

Target tests:
- `uv run pytest tests/coding_agent/test_run_target.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server_workspace_transfer.py tests/dogfood/test_workspace_provider_demo.py tests/cli/test_remote_client.py tests/coding_agent/test_runtime_environment.py tests/coding_agent/test_runtime_workspace_export_service.py tests/coding_agent/test_workspace_action_routing.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/coding_agent/test_runtime_metadata_service.py tests/coding_agent/test_runtime_wire_event_recorder.py tests/coding_agent/test_runtime_context_binding_service.py tests/coding_agent/test_runtime_observation_service.py tests/coding_agent/test_runtime_attached_executor_service.py tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_runtime_run_recovery.py tests/ui/test_server_compat_imports.py -q`
- `uv run pytest tests/ui/test_http_server.py -k "create_session or run_target or execution_binding or external_worker or local_attached or attached_executor_alias or workers_endpoint or worker_metadata or workspace_diff or workspace_patch or publish_branch or publish_pr or get_session_response or workspace_provider" -q`
- `uv run ruff check src/coding_agent/server/session_manager.py src/coding_agent/server/http_server.py src/coding_agent/server/schemas.py src/coding_agent/runs src/coding_agent/environment src/coding_agent/remote src/coding_agent/runtime_store.py tests/ui/test_http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server_workspace_transfer.py tests/dogfood/test_workspace_provider_demo.py tests/cli/test_remote_client.py tests/coding_agent/test_run_target.py tests/coding_agent/test_runtime_environment.py tests/coding_agent/test_runtime_workspace_export_service.py tests/coding_agent/test_workspace_action_routing.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/coding_agent/test_runtime_metadata_service.py tests/coding_agent/test_runtime_wire_event_recorder.py tests/coding_agent/test_runtime_context_binding_service.py tests/coding_agent/test_runtime_observation_service.py tests/coding_agent/test_runtime_attached_executor_service.py tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_runtime_run_recovery.py tests/coding_agent/test_pg_runtime_store.py tests/ui/test_server_compat_imports.py`

Loop policy:
- Engineer implements the smallest correct RunTarget-only change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if removing legacy request support would require a public API deprecation window instead of a hard rejection.
- Do not leave `ExecutionBinding` imports in `src/coding_agent` except inside the legacy migration parser if that parser remains.
