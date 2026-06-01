# ADR-0054: Executor runtime terminology and resume-first direction

**Status**: Accepted
**Date**: 2026-06-01

## Context

ADRs 0051 through 0053 added an `external_worker` execution path so the HTTP
session manager can record a run request while another process performs agent
runtime execution. That path is useful, but the worker terminology now leaks a
distributed worker-pool mental model into the main product surface.

The product direction is closer to Codex and Claude Code: local-first,
interactive sessions that can restore context after interruption. The first
resume milestone should restore the session state, transcript, events,
checkpoint context, and workspace pointer. It should not promise process-level
reconnect, worker lease reclaim, fencing, event spooling, or a pool UI.

The current local `run --goal` entrypoint also bypasses `SessionManager` by
creating a pipeline directly. That makes batch local execution inconsistent with
the durable session, run, event, checkpoint, and future resume path.

## Decision

Use executor/runtime terminology for user-facing surfaces. The canonical
execution placements are:

- `server_embedded` for runtime execution inside the server/session-manager
  process.
- `local_attached` for a local CLI-started executor that claims o6n-managed
  runs and executes against the local workspace.
- `cloud_workspace` for server-managed remote workspace execution.
- `local_daemon` as a future placement for a persistent local runtime service.

Keep existing worker-named HTTP endpoints, payload fields, and CLI commands as
compatibility aliases while adding executor-named CLI surfaces. New attached
executor sessions should persist `local_attached`; the old `external_worker`
binding kind remains readable and claimable for compatibility.

Local `run --goal` must stop using the unmanaged direct pipeline path. It should
create a managed local session, run the first prompt through `SessionManager`,
and stream the session wire messages to the CLI. This keeps local batch
execution on the same durable session/run/event/checkpoint path as interactive
and remote execution.

Session resume remains a separate follow-up decision. This ADR only aligns the
terminology and removes the unmanaged local batch bypass so the next ADR can add
explicit interrupted-run resume semantics.

## Alternatives Rejected

- Keep `worker` as the primary term — rejected because it implies a distributed
  worker registry, lease/fencing model, and pool UI that are explicitly out of
  scope for the first resume milestone.
- Rename every internal symbol and HTTP field immediately — rejected because it
  would create a large protocol migration before the resume semantics are
  implemented. Compatibility aliases provide the product language without
  breaking existing deployments.
- Remove `run --goal` entirely — rejected because scripts and tests may still
  depend on a non-interactive entrypoint. Reusing `SessionManager` preserves the
  convenience while aligning it with the durable runtime path.
- Implement local daemon first — rejected because the current goal is restoring
  session context after interruption, not keeping a local executor alive after
  the CLI disconnects.

## Acceptance Criteria

- [x] `test_run_command_uses_managed_session`
- [x] `test_process_message_uses_managed_session_when_available`
- [x] `test_session_manager_creates_file_session_store_for_local_cli_storage`
- [x] `test_file_session_and_jsonl_runtime_store_reopen_resume_metadata`
- [x] `test_jsonl_runtime_store_persists_runs_and_events_across_instances`
- [x] `test_remote_executor_alias_runs_existing_attached_executor_loop`
- [x] `test_remote_executors_alias_lists_existing_executor_status`
- [x] `uv run pytest tests/cli/test_repl.py tests/cli/test_entrypoint_contract.py tests/cli/test_remote_client.py tests/ui/test_session_manager_public_api.py tests/coding_agent/test_jsonl_runtime_store.py -k "process_message_uses_managed_session_when_available or run_command_uses_managed_session or remote_executor_alias or remote_executors_alias or default_non_interactive or file_session_store or jsonl_runtime_store or reopen_resume_metadata" -v`

## References

- `src/coding_agent/cli/main.py`
- `src/coding_agent/cli/remote_commands.py`
- `src/coding_agent/server/execution_binding.py`
- `docs/adr/0051-external-worker-execution-control-plane.md`
- `docs/adr/0052-external-worker-usable-control-plane.md`
- `docs/adr/0053-advanced-external-worker-control-plane-foundations.md`
