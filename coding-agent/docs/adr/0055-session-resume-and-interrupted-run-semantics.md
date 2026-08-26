# ADR-0055: Session resume and interrupted run semantics

**Status**: Accepted
**Date**: 2026-06-01

## Context

ADR-0032 introduced durable runtime statuses including `interrupted`, and
ADR-0054 aligned the product direction around executor/runtime terminology and
resume-first local sessions. The next missing product contract is explicit
session resume: a user should be able to continue from the last known session
state without the system pretending it can reconnect to a dead runtime process.

The important distinction is between restoring context and restoring process
execution. The first resume milestone restores transcript, tape, events,
message/checkpoint context, workspace pointer, and run lineage. It does not
reconnect to an old process, reclaim an executor lease, or continue a running
tool command after the executor died.

## Decision

Add a resume operation for existing sessions. Resume always creates a new run
and links it to the most recent durable run in the session. The previous run is
not reused. If the previous run was interrupted, it remains interrupted. If the
previous run completed, failed, or was cancelled, it also remains unchanged.

Resume run metadata must include:

- `previous_run_id`
- `resume_from_run_id`
- `resume_from_event_id` when a previous runtime event exists
- `resume_reason`
- `resume_context_injected: true`

The new runtime prompt must include a short system-style context note explaining
that the previous run should be continued from the last known state and that
completed work should not be repeated unless necessary. The user's optional
resume prompt is appended after that context note.

If the latest durable run is still active (`queued`, `requested`, `claimed`,
`running`, or `cancelling`), resume is rejected. Users should attach, wait,
cancel, or let stale-run recovery mark the run interrupted before creating a
new resumed run.

`waiting_for_approval` is not a separate durable `AgentRunRecord.status` in this
milestone. Approval wait state is represented by a pending durable interaction
and the session turn remaining active. This keeps resume gating tied to one
durable run lifecycle while preserving approval inbox/replay data for later UI
work.

For local-attached executor sessions, resume records a new requested run using
the same local-attached control-plane path as prompt continuation. For
server-embedded and cloud workspace sessions, resume starts a new server-managed
runtime turn through `SessionManager.run_agent()`.

## Alternatives Rejected

- Reuse the old run ID — rejected because durable run history would become
  ambiguous and observability could not distinguish the interrupted attempt from
  the resumed attempt.
- Restore a dead process — rejected because that requires daemon ownership,
  leases, event spool, fencing, and process supervision, which are explicitly
  future distributed execution work.
- Automatically roll back to the latest checkpoint — rejected because session
  resume means continue from current history, while checkpoint restore means
  controlled rollback to a previous state.
- Allow resume while another durable run is active — rejected because it risks
  two executors mutating the same session/tape/workspace concurrently.

## Acceptance Criteria

- [x] `test_resume_session_creates_new_run_linked_to_interrupted_run`
- [x] `test_resume_session_rejects_active_runtime_run`
- [x] `test_resume_external_executor_session_requests_linked_run`
- [x] `test_http_resume_session_streams_resumed_run`
- [x] `uv run pytest tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "resume_session" -v`

## References

- `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/http_server.py`
- `tests/ui/test_session_manager_runtime.py`
- `tests/ui/test_http_server.py`
