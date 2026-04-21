# ADR-0015: Enforce owner-routed HTTP event streams for Phase 2 sticky routing

**Status**: Proposed
**Date**: 2026-04-21

## Context

ADR-0013 defines Phase 2 multi-instance ownership as an owner/lease/fencing layer on top of PostgreSQL-backed session durability. That ADR already says runtime execution, approval handling, and event streaming are owner-routed operations, and it explicitly allows sticky routing as the first event-routing strategy.

The current HTTP implementation does not yet enforce that rule for live SSE streams. `GET /sessions/{session_id}/events` only checks that the session exists, then registers an in-memory event queue and starts a keepalive loop. In a multi-instance deployment, a non-owner instance can still attach a live event stream for a session it does not own, and an already-open stream on the old owner is not forced to stop when ownership changes.

That gap matters because Phase 2 is already using owner checks to fence runtime-sensitive mutations (`run_agent`, checkpoint restore, close, approval submission). Leaving `/events` unfenced means the repository still allows a stale instance to appear live even after the owner has changed. This does not violate at-most-once execution directly, but it does violate the owner-routed contract that ADR-0013 and the PG phase 2 checklist already claim.

## Decision

Adopt owner-routed sticky routing as the first concrete Phase 2 policy for HTTP event streams.

Specifically:

- `GET /sessions/{session_id}/events` must reject non-owner instances before registering a live event queue.
- The rejection contract for a non-owner or stale owner is `409 Conflict` with the existing `SessionOwnershipConflictError` detail, matching the current Phase 2 HTTP conflict surface.
- Once a stream is open, the keepalive loop must re-check ownership before emitting keepalive pings. If ownership is lost, the generator must terminate and allow queue cleanup to run.
- The first cut remains sticky-routing only. It does not add brokered event routing, redirect payloads, or cross-instance queue transfer.
- The owner check should be exposed through public `SessionManager` methods for event-stream authorization rather than calling `_assert_owner` directly from HTTP handlers.

This ADR intentionally keeps approval metadata persistence out of scope. It closes the narrowest, clearest routing gap first: only the current owner may serve the live SSE stream for a session.

## Alternatives Rejected

- Keep `/events` unfenced and rely on load-balancer stickiness alone — rejected because it leaves stale instances free to attach live streams even when PostgreSQL ownership already says they are not authoritative.
- Add brokered event routing now — rejected because it expands Phase 2 into cross-instance fan-out before the repository has fully closed the simpler owner-routed sticky-routing contract.
- Return a redirect payload instead of `409 Conflict` — rejected for the first cut because the HTTP API already uses `409` for Phase 2 ownership conflicts and there is no canonical owner-address discovery mechanism yet.
- Continue serving already-open streams after owner loss until disconnect — rejected because it weakens the owner-routed contract and lets stale instances keep presenting themselves as authoritative.

## Acceptance Criteria

- [ ] `test_get_events_rejects_stale_owner_before_stream_registration`
- [ ] `test_get_events_stops_stream_after_owner_change`
- [ ] `test_get_events_keeps_stream_alive_for_current_owner`
- [ ] `test_get_events_returns_404_before_owner_check_for_missing_session`
- [ ] `uv run pytest tests/ui/test_http_server_failover.py -v`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/ui/test_session_manager_owner_checks.py -k "events or owner or failover" -v`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/pg-phase2-multi-instance-checklist.md`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_http_server.py`
