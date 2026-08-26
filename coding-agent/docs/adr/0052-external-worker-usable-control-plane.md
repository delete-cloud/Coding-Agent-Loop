# ADR-0052: External worker usable control plane

**Status**: Accepted
**Date**: 2026-05-31

## Context

ADR-0051 introduced `external_worker` sessions: the server records run requests
and local workers execute agent turns. That made the execution path possible,
but daily use still needs remote discovery and continuation. Users must be able
to list sessions, inspect runs and events, continue an existing session, and see
worker health without reading database rows or server logs.

The first usable slice should not implement full process reconnect, approval
inbox UI, or cross-machine workspace sync. It should expose stable control-plane
surfaces that leave those capabilities compatible with the existing
`external_worker` protocol.

## Decision

Add HTTP and CLI operations for session/run/event inspection, external-worker
prompt continuation, attach, and derived worker health.

Worker status is derived from durable run metadata in this slice. A worker is
`running` when it owns an active non-expired run, `stale` when its active lease
has expired or an active run has missed the stale heartbeat window, `idle` after
its last known run is recently terminal, and `offline` after the worker has not
been seen for the offline window. The response shape includes `worker_id`,
`status`, `executor_kind`, `worker_pool`, `workspace_ref`, `current_run_id`,
`current_session_id`, `last_run_id`, `last_session_id`, `last_seen_at`, and
`lease_expires_at` so a future dedicated worker registry can replace the
derivation without changing clients.

Run and event inspection remains scoped through session visibility rules. The
CLI is a thin HTTP client and does not read server storage directly.

## Acceptance Criteria

- [ ] `test_external_worker_session_runs_endpoint_lists_runs`
- [ ] `test_external_worker_workers_endpoint_reports_running_and_stale_workers`
- [ ] `test_remote_prompt_streams_existing_external_worker_session`
- [ ] `test_remote_workers_lists_external_worker_status`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "external_worker or sessions or runs or workers" -v`
- [ ] `uv run pytest tests/cli/test_remote_client.py -k "remote_session or remote_run or remote_worker or external_worker or remote_prompt or remote_workers" -v`

## References

- `docs/adr/0051-external-worker-execution-control-plane.md`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/cli/remote_commands.py`
