# ADR-0014: Separate session execution binding from runtime ownership

**Status**: Proposed
**Date**: 2026-04-19

## Context

ADR-0013 defines Phase 2 multi-instance session ownership through owner records, leases, and fencing tokens. That layer answers "which instance may drive this session right now." It does not answer "where do the session's tools and files run," and it does not address how a session moves between execution environments while keeping the same durable identity.

Today `SessionManager` conflates three concepts inside the `Session` object and its `repo_path` field:

1. **Durable session metadata** (id, provider, model, policy, tape_id) that survives restarts.
2. **Execution/workspace binding** (`repo_path`, tool filesystem scope, shell working directory) that decides where `file_read`, `file_write`, and `bash_run` operate.
3. **Current runtime owner** (`task`, `approval_event`, `event_queues`, `runtime_pipeline`, `runtime_ctx`, `runtime_adapter`) that is local-authoritative and ephemeral.

These three concepts change on different timelines. Session metadata changes when the user switches models or policies. Execution binding changes when a user moves from a local repository to a cloud workspace, or when a local `repo_path` is remounted. Runtime owner changes every time a lease expires, an instance restarts, or load balancing shifts traffic.

Because `SessionManager` stores `repo_path` directly on the session and passes it to `create_agent_for_session` as `workspace_root`, there is no place to introduce a cloud workspace binding without overloading `repo_path` with incompatible semantics. The HTTP server also keeps approval state, event queues, and task ownership in local memory, which makes it impossible to reason about failover without first knowing whether the new owner can satisfy the old owner's execution contract.

## Decision

Introduce a first-class **execution binding** abstraction that sits beside the ownership layer defined in ADR-0013. The binding layer decides where a session's tools execute; the ownership layer decides which instance drives the session. The two layers are orthogonal and change independently.

Specifically:

- Add an `ExecutionBinding` dataclass/protocol in `coding_agent.ui.execution_binding` with at least two concrete variants:
  - `LocalExecutionBinding(workspace_root: str)` for local filesystem mode.
  - `CloudWorkspaceBinding(workspace_url: str, workspace_id: str)` for remote workspace mode.
- Store the serialized binding on the session metadata table as `execution_binding`, separate from `repo_path`. Migrate the current `repo_path` field to mean "local workspace root" only.
- Add a `BindingResolver` interface that turns an `ExecutionBinding` into the `workspace_root` and tool configuration that `create_agent_for_session` expects. The resolver lives in `coding_agent.ui.binding_resolver`.
- Update `SessionManager` to resolve the binding before calling `create_agent_for_session`, so `run_agent`, `ensure_session_runtime`, and checkpoint restore all use the resolved workspace consistently.
- Update `Session.to_store_data` and `Session.from_store_data` to round-trip the binding as a typed dict, and default missing bindings to `LocalExecutionBinding(repo_path)` for backward compatibility.
- Keep the `Session` runtime fields (`task`, `approval_event`, `event_queues`, `runtime_pipeline`, `runtime_ctx`, `runtime_adapter`) untouched. Those belong to the ownership layer (ADR-0013) and remain local-authoritative.

This ADR does not add cloud workspace tool implementations. It only creates the abstraction and the local binding variant so that future cloud bindings slot in without changing `SessionManager` or `http_server.py`.

## Alternatives Rejected

- Overload `repo_path` to mean both local path and cloud workspace identifier — rejected because it creates a semantic mismatch. A `Path` cannot represent a cloud workspace URL, and conditional parsing based on string prefixes is fragile.
- Encode binding information inside the `CheckpointSnapshot.extra` field — rejected because binding is session-level metadata, not checkpoint-level state. Restoring a checkpoint should not change where tools execute.
- Wait until cloud workspace tools exist before adding the abstraction — rejected because `SessionManager` and `http_server.py` already carry local-only assumptions (approval state, event queues, task ownership) that will harden into technical debt without a binding boundary.
- Merge execution binding and runtime owner into a single "session placement" record — rejected because owner identity and workspace identity change on different timelines. A session can fail over to a new owner while keeping the same binding, and a session can change binding while staying on the same owner.

## Acceptance Criteria

- [ ] `test_local_binding_resolves_to_workspace_root_for_create_agent`
- [ ] `test_session_metadata_round_trips_local_execution_binding`
- [ ] `test_session_metadata_defaults_missing_binding_to_local_from_repo_path`
- [ ] `test_cloud_workspace_binding_serializes_to_typed_dict`
- [ ] `test_binding_resolver_rejects_unknown_binding_kind`
- [ ] `test_run_agent_uses_resolved_workspace_root_from_binding`
- [ ] `test_restore_checkpoint_preserves_execution_binding`
- [ ] `test_http_server_create_session_stores_local_binding_by_default`
- [ ] `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "binding" -v`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0012-complete-phase1-postgresql-http-session-persistence.md`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/app.py`
- `src/coding_agent/plugins/core_tools.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_session_manager_runtime.py`
- `tests/ui/test_http_server.py`
