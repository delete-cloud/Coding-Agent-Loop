# ADR-0051: External worker execution control plane

**Status**: Accepted
**Date**: 2026-05-31

## Context

Remote sessions currently execute inside the server process: `/sessions/{id}/prompt`
prepares the HTTP session, creates or reuses an agent runtime, and runs the turn
through the server-side `SessionManager`. The existing `local` execution binding
therefore means server-local workspace execution, not execution on the user's
local CLI machine.

The desired production shape is for the server to own session metadata,
durable run state, approvals, cancellation, and observability, while an external
worker such as a local CLI process performs file, shell, and tool execution in
its own workspace.

## Decision

Add an `external_worker` execution binding. For sessions using this binding,
the server must not resolve an environment or call `create_agent()` in response
to `/sessions/{id}/prompt`. Instead, the server records a durable run request,
exposes worker claim/heartbeat/finalize endpoints, and accepts idempotent event
uploads from the worker.

The existing persisted `local` binding remains backward compatible and keeps
its server-local meaning. New user-facing documentation and code paths should
call this `server_local` when contrasting it with external workers.

## Alternatives Rejected

- Reinterpret `local` as the caller's Mac — rejected because existing sessions
  persist `kind = "local"` and server code resolves it to a server path.
- Upload every local workspace to the server before execution — rejected because
  it preserves remote execution semantics instead of making local execution the
  owner of tools and filesystem access.
- Let local CLI write directly to the server database — rejected because it
  bypasses server auth, lease, approval, cancellation, and observability policy.

## Acceptance Criteria

- [ ] `test_external_worker_prompt_creates_run_without_running_agent`
- [ ] `test_external_worker_claim_marks_run_claimed`
- [ ] `test_external_worker_events_are_replayed_and_broadcast`
- [ ] `test_remote_local_run_uses_external_worker_binding`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "external_worker" -v`
- [ ] `uv run pytest tests/cli/test_remote_client.py -k "local_run or external_worker" -v`

## References

- `src/coding_agent/server/execution_binding.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/cli/remote_commands.py`
