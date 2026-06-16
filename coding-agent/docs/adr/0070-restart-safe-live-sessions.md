# ADR-0070: Restart-safe live HTTP sessions for single-replica deployments

**Status**: Proposed
**Date**: 2026-06-16

## Context

The o6n deployment runs a single replica with all durable backends on SQLite
(`local.sqlite3`) on an RWO PVC (the cutover decision: sqlite over the shared
PostgreSQL, see ADR-0068 and the deployment history). ADR-0067/0068 made tape,
runtime, checkpoint, and session-owner state durable, and ADR-0055 defined
session-resume and interrupted-run semantics. The durable layer therefore
already exists.

Despite that, a routine image rollout (REVISION 6) was observed to lose live
sessions: `/healthz` reported 2 sessions before the rollout and the durable
`agent_http_sessions` table held only 1 row afterward. Root cause is that the
durable layer is not wired into a restart-recovery closed loop:

1. **Persistence is event-driven, not create-time.** `_persist_session_async`
   writes to the durable store on turn/mutation callbacks. A session created but
   not yet flushed at the moment of process termination disappears entirely.
2. **Boot does not rehydrate the runtime.** `lifespan` startup runs
   `backfill_owner_leases()` and `recover_stale_runtime_runs()` but does not
   reconstruct in-memory `Session.runtime_pipeline/ctx/adapter`; the live runtime
   is rebuilt lazily on the next prompt/resume.
3. **Shutdown drain is not guaranteed.** `lifespan` has a shutdown block
   (`shutdown_session_runtime` + `release_owned_sessions`), but the Helm chart
   sets no `terminationGracePeriodSeconds` (default 30s) and no `preStop` hook,
   so a rolling-update SIGTERM can be SIGKILLed before the flush completes.

This matters more now that ADR-driven config changes auto-roll the pod: the
config-checksum annotation (PR #606) restarts the pod on every config edit, so
restart safety is on the hot path, not an edge case. The maintainer has decided
**not** to gate the auto-roll behind a toggle; restart safety must instead come
from making restarts non-destructive.

## Decision

Make single-replica live HTTP sessions survive a process restart by wiring the
existing durable layer (ADR-0055/0067/0068) into a restart-recovery closed loop.
Scope is single-replica + sqlite-on-PVC; multi-replica HA failover is explicitly
out of scope (see Alternatives Rejected). Land in three slices:

**P1 — Create-time durability.** `POST /sessions` persists session metadata to
the durable store synchronously before returning, so a session is recoverable
the instant it exists. `GET /sessions` already reads the durable store; once
persistence is complete-on-create, the session list reflects durable truth and
cannot show fewer sessions than were created.

**P2 — Graceful drain on shutdown.** On SIGTERM the process must flush all
in-memory sessions, checkpoint in-flight turns, and release owner leases before
exit. The Helm chart sets an adequate `terminationGracePeriodSeconds` and a
`preStop` hook (or the container handles SIGTERM and delays exit) so Kubernetes
does not SIGKILL mid-drain. This makes both manual rollouts and the #606
auto-roll non-destructive.

**P3 — Boot rehydration + interrupted-turn resume.** On startup the server
reconstructs the session list from the durable store (reusing
`Session.from_store_data`) so sessions are listable and resumable immediately,
and an interrupted turn resumes from its last checkpoint per ADR-0055 semantics
rather than being discarded.

## Alternatives Rejected

- **Multi-replica HA failover (ADR-0013 ownership leases, 2+ replicas).** True
  zero-loss handoff during a rollout requires 2 replicas on shared storage
  (PostgreSQL or RWX). That directly contradicts the deliberate cutover from
  shared PostgreSQL to single-replica sqlite-on-PVC, and is overkill for a
  single-user personal agent. The owner-lease machinery stays for correctness,
  not for HA.
- **Gate the #606 config-checksum auto-roll behind a default-off toggle.**
  Rejected by maintainer decision: keep auto-roll so config edits reliably take
  effect; achieve safety via graceful drain (P2) instead of suppressing rollouts.
- **Accept live-session loss on restart as expected behavior.** Rejected: the
  durable layer (ADR-0055/0067/0068) already exists, so the loss is a wiring gap,
  not an inherent limitation. Tapes and run history survive; only the live
  session handle is dropped, which is recoverable by design.
- **Persist everything synchronously on every mutation.** Rejected: SQLite is a
  single writer and ADR-0068 requires short protected transactions. Create-time
  persistence (P1) plus checkpoint-based resume (P3) gives durability without
  forcing every in-memory mutation through a synchronous write.

## Acceptance Criteria

Implementation pending. Intended tests and gating command:

- [ ] `test_session_persisted_synchronously_on_create` — durable store holds the
  session row immediately after `POST /sessions` returns, before any turn.
- [ ] `test_session_list_survives_simulated_restart` — sessions created, store
  reopened (new SessionManager), `list_sessions_async` still returns them.
- [ ] `test_graceful_shutdown_flushes_inflight_sessions` — lifespan shutdown
  flushes un-persisted sessions and releases leases.
- [ ] `test_boot_rehydrates_session_list_from_durable_store` — fresh process
  lists durable sessions without requiring a prompt first.
- [ ] `test_interrupted_turn_resumes_from_last_checkpoint` — a turn interrupted
  by restart resumes from its last checkpoint (ADR-0055).
- [ ] `test_helm_deployment_sets_graceful_drain` — chart renders a non-default
  `terminationGracePeriodSeconds` and a `preStop`/SIGTERM drain path.
- [ ] `uv run pytest tests/coding_agent/test_session_restart_durability.py tests/deploy/test_helm_chart.py -k "restart or drain or rehydrate" -q`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0067-local-sqlite-durable-tape-runtime.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0071-argocd-gitops-deployment.md` (P2 graceful drain is a prerequisite for comfortable GitOps auto-deploy)
- `src/coding_agent/server/session_manager.py` (`_persist_session_async`, `release_owned_sessions`, `renew_owner_leases`, `from_store_data`)
- `src/coding_agent/server/http_server.py` (`lifespan`, `GET /sessions`)
- `helm/templates/deployment.yaml` (config-checksum annotation, PR #606; no `terminationGracePeriodSeconds`/`preStop` today)
