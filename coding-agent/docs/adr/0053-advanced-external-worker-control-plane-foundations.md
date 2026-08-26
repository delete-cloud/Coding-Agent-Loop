# ADR-0053: Advanced external worker control-plane foundations

**Status**: Accepted
**Date**: 2026-05-31

## Context

ADR-0051 and ADR-0052 made `external_worker` sessions usable: the server can
record run requests, local workers can claim and execute them, and users can
inspect sessions, runs, events, and derived worker health. The next step is to
leave concrete extension points for reconnect, durable approval interaction
management, and future workspace sync without prematurely implementing process
checkpointing or bidirectional workspace replication.

The control plane already persists runtime runs and approval interactions. A
first advanced slice should reuse those records so API and CLI users can see and
resolve pending approvals consistently, while workers report enough metadata for
operators to distinguish a logical `worker_id` from a particular local process.

## Decision

Extend external-worker claim and heartbeat requests with optional
`worker_instance_id`, `process_id`, `capabilities`, and `workspace_sync`
metadata. The server stores these fields in run metadata and exposes them in
worker status responses. This is metadata-only reconnect support: it allows
operators to see which process owns a lease and what the worker advertises, but
it does not restore an already-running local process after a disconnect.

Expose durable runtime interaction APIs for active approvals:
`GET /runs/{run_id}/interactions`, `GET /interactions`, `GET
/interactions/{interaction_id}`, and `POST /interactions/{interaction_id}/resolve`.
Resolution delegates to the existing session approval path so the runtime
message bus, in-memory approval coordinator, and persisted interaction record
remain one state machine. If the matching approval is no longer pending, the API
returns a conflict instead of directly mutating the database.

Add remote client and CLI commands for listing, inspecting, and resolving
interactions. Workspace sync remains a declared metadata envelope (`mode`,
workspace reference, and capability data) until a later ADR defines actual sync
protocol semantics.

## Alternatives Rejected

- Add a separate approval queue table - rejected because `agent_interactions`
  already persists approval request/response state and splitting it would create
  reconciliation problems.
- Resolve approvals by updating `agent_interactions` directly - rejected because
  it would not wake the waiting worker approval request or publish a runtime
  approval decision.
- Implement process restoration now - rejected because reconnecting to an
  already-running local process needs a local process registry, durable worker
  supervisor semantics, and recovery rules beyond this slice.
- Implement cross-machine workspace sync now - rejected because live sync needs a
  separate conflict model and transport protocol.

## Acceptance Criteria

- [ ] `test_external_worker_worker_metadata_surfaces_in_status`
- [ ] `test_runtime_run_interactions_endpoint_lists_interactions`
- [ ] `test_runtime_interaction_resolve_uses_session_approval_flow`
- [ ] `test_remote_interactions_list_and_resolve`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "external_worker or interactions" -v`
- [ ] `uv run pytest tests/cli/test_remote_client.py -k "remote_interaction or remote_worker or external_worker" -v`

## References

- `docs/adr/0051-external-worker-execution-control-plane.md`
- `docs/adr/0052-external-worker-usable-control-plane.md`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/runtime_store.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/cli/remote_commands.py`
