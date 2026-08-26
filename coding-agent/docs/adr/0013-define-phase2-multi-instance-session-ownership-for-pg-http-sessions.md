# ADR-0013: Define Phase 2 multi-instance session ownership for PG HTTP sessions

**Status**: Proposed
**Date**: 2026-04-19

## Context

ADR-0012 closes the Phase 1 PostgreSQL persistence gap for the HTTP/UI stack by making session metadata, tape history, and checkpoint snapshots durable in PostgreSQL. But it keeps active runtime state (`task`, approval state, event queues, pipeline/context/adapter ownership) single-instance and local-authoritative.

That Phase 1 boundary is acceptable for restart-safe storage, but it is not sufficient for multi-pod HTTP deployment. Once multiple application instances can read the same persisted session metadata, the repository needs an explicit answer to three questions that Phase 1 intentionally left open: who owns execution for a session right now, how other instances know they are no longer allowed to act, and how approval/event flows reach the active owner.

Phase 2 is the ownership and coordination layer that sits on top of Phase 1 persistence. It does not primarily add “more PostgreSQL persistence”; it defines how multiple instances safely share a PostgreSQL-backed session/tape/checkpoint substrate without split-brain behavior.

## Decision

Define Phase 2 as a multi-instance coordination layer for HTTP sessions with four core properties:

- Each active HTTP session has one current runtime **owner** recorded in PostgreSQL.
- Ownership is time-bounded by a renewable **lease** rather than an infinite claim.
- Every ownership grant yields a monotonically increasing **fencing token** that must accompany owner-sensitive writes and commands.
- Runtime execution, approval handling, and event streaming are all **owner-routed** operations rather than implicit local-memory behavior.

Specifically, Phase 2 will include:

- A PostgreSQL-backed `session_owner` record keyed by `session_id`, storing `owner_id`, `lease_expires_at`, `fencing_token`, and timestamps.
- App-layer primitives to `acquire`, `renew`, and `release` session ownership atomically.
- Owner checks in `run_agent`, checkpoint restore, and close/shutdown flows before they perform runtime-sensitive work.
- Fencing-token validation so a stale owner cannot continue mutating session-associated state after ownership changes.
- An explicit routing rule for approval responses and SSE/event delivery so only the active owner drives runtime-visible side effects.
- A first failover contract of **at-most-once** execution for in-flight turns: after owner loss, a new owner may rebuild cold state from persisted data, but it does not resume partially executed runtime state.

Phase 2 should initially prefer a minimal operational model:

- sticky routing is acceptable as the first event/approval routing strategy,
- in-flight turn replay/resume is out of scope,
- only owner-sensitive actions need fencing in the first cut.

## Alternatives Rejected

- Keep Phase 2 as “just add more PG-backed state” — rejected because Phase 1 already made durable state explicit; the unresolved risk is multi-instance coordination, not missing tables.
- Let any instance read persisted session state and optimistically execute if local cache is empty — rejected because it allows concurrent turn execution, stale approval handling, and conflicting close/restore behavior.
- Solve multi-instance coordination with only sticky load balancing and no persisted owner record — rejected because it provides no correctness guarantee under pod restarts, manual retries, or load balancer drift.
- Attempt best-effort in-flight turn resume in the first Phase 2 slice — rejected because the repository still treats runtime objects as local-authoritative and not serializable across owners.

## Acceptance Criteria

- [ ] `test_session_owner_acquire_conflicts_with_live_lease`
- [ ] `test_session_owner_renew_rejects_stale_token`
- [ ] `test_run_agent_rejects_non_owner_instance`
- [ ] `test_restore_checkpoint_rejects_stale_owner`
- [ ] `test_close_session_rejects_stale_owner`
- [ ] `test_approval_response_is_rejected_after_owner_change`
- [ ] `test_session_failover_rebuilds_from_persisted_state_without_resuming_inflight_turn`
- [ ] `uv run pytest tests/agentkit/ tests/ui/ -k "owner or lease or fencing or failover" -v`

## References

- `docs/adr/0012-complete-phase1-postgresql-http-session-persistence.md`
- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_store.py`
- `src/agentkit/storage/pg.py`
- Historical design context: `docs/specs/checkpoint-design-section2a.md`
