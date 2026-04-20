# PostgreSQL Phase 2 — Multi-instance checklist

This document is an implementation-oriented companion to ADR-0013. It does not replace the ADR; it expands the concrete pieces that Phase 2 needs so implementation can be planned incrementally.

## Scope Summary

Phase 1 made these durable in PostgreSQL:

- HTTP session metadata
- tape history
- checkpoint snapshots/catalogs

Phase 2 defines who is allowed to act on that persisted state when more than one HTTP instance exists.

## 1. Ownership data model

Add a dedicated owner/lease table, for example:

```sql
CREATE TABLE session_owners (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    fencing_token BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Recommended semantics:

- `owner_id`: stable process/pod identifier
- `lease_expires_at`: absolute lease expiry
- `fencing_token`: monotonically increasing per successful acquire
- `updated_at`: audit/debug support

## 2. Ownership primitives

Define one narrow app-layer interface for ownership:

```python
class SessionOwnerStore(Protocol):
    async def acquire(self, session_id: str, owner_id: str, lease_ttl_seconds: float) -> OwnerLease: ...
    async def renew(self, session_id: str, owner_id: str, fencing_token: int, lease_ttl_seconds: float) -> OwnerLease: ...
    async def release(self, session_id: str, owner_id: str, fencing_token: int) -> None: ...
    async def get(self, session_id: str) -> OwnerLease | None: ...
```

Acquire rules:

- succeed if no live lease exists
- fail if another owner still holds a live lease
- increment fencing token on every successful acquire

Renew rules:

- only current owner + current token may renew
- stale token must fail

Release rules:

- only current owner + current token may release
- stale release must be ignored/rejected

## 3. Runtime owner checks

Guard these actions behind ownership validation:

- `SessionManager.run_agent`
- `SessionManager.capture_checkpoint`
- `SessionManager.restore_checkpoint`
- `SessionManager.close_session`
- `SessionManager.shutdown_session_runtime`

Minimum rule:

- if this instance is not the active owner for the session, do not execute runtime-sensitive work
- return a conflict/error that callers can treat as a retry or routing problem

Ownership checks answer whether this instance may perform the action at all. They do not decide which workspace/tools configuration the session should use. Workspace resolution remains an execution-binding step that happens after ownership is validated, so the order is always ownership first, execution binding second.

## 4. Fencing points

Fencing is only useful if stale owners are rejected at side-effect boundaries.

Phase 2 MVP should require fencing on:

- turn start (`run_agent`)
- checkpoint restore
- session close
- approval response submission

Phase 2 later can extend fencing to:

- event publishing metadata
- checkpoint capture metadata
- owner-sensitive session metadata writes

## 5. Event routing

Current HTTP event queues are local in-memory structures. Phase 2 must choose a routing model.

### MVP recommendation: sticky routing

- clients reconnect to the same owner instance
- only owner instance serves live event stream for a session
- non-owner instance returns a conflict/redirect-style error or terminates the stream cleanly

### Future option: brokered routing

- owner publishes events to shared event transport
- any edge instance can proxy SSE

Do not attempt brokered routing in the first Phase 2 slice unless ownership is already stable.

## 6. Approval routing

Current approval state is runtime-local. Phase 2 needs explicit routing:

- approval requests are owned by the current session owner
- approval responses must include session + request identity and be rejected if owner/token changed

Suggested persisted approval metadata:

- `request_id`
- `session_id`
- `owner_id`
- `fencing_token`
- `status`
- `created_at`

## 7. Failover semantics

Phase 2 MVP should choose **at-most-once** for in-flight turns.

That means:

- if the owner dies during a turn, the turn is not resumed mid-flight
- a new owner may rebuild cold state from persisted session metadata, tape history, and checkpoints
- clients must retry or reconnect after failover

This is consistent with the current repository boundary where runtime objects remain local-authoritative and are not serializable.

## 8. Restore / close consistency

Define these invariants:

- `run_agent`, `restore_checkpoint`, and `close_session` are mutually exclusive owner-sensitive operations
- non-owner instances cannot execute them locally
- stale owners must fail before mutating shared state

For `run_agent`, `restore_checkpoint`, and `close_session`, the intended order is: validate ownership first, then resolve execution binding, then perform the operation. This checklist follows the same boundary as ADR-0013: ownership chooses the actor, execution binding chooses the workspace/tools context.

## 9. Suggested implementation order

1. Add `session_owners` table + PG store
2. Add acquire / renew / release APIs + tests
3. Add owner checks to `run_agent`
4. Add owner checks to restore / close / shutdown
5. Add approval request/response ownership metadata
6. Add sticky-routing behavior for SSE and approval endpoints
7. Add failover tests and operational metrics

## 10. Suggested tests

- `test_session_owner_acquire_conflicts_with_live_lease`
- `test_session_owner_renew_rejects_stale_token`
- `test_session_owner_release_rejects_stale_token`
- `test_run_agent_rejects_non_owner_instance`
- `test_close_session_rejects_non_owner_instance`
- `test_restore_checkpoint_rejects_stale_owner`
- `test_approval_response_is_rejected_after_owner_change`
- `test_failover_rebuilds_runtime_from_persisted_tape_without_resuming_inflight_turn`
