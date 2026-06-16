# ADR-0070: Durable session retention and graceful drain

**Status**: Proposed
**Date**: 2026-06-16

## Context

The o6n deployment runs a single replica with all durable backends on SQLite
(`local.sqlite3`) on an RWO PVC (the cutover decision: sqlite over the shared
PostgreSQL, see ADR-0068). ADR-0067/0068 made tape, runtime, checkpoint, and
session-owner state durable, and ADR-0055 defined session-resume and
interrupted-run semantics.

A routine image rollout (REVISION 6) appeared to lose live sessions: `/healthz`
reported 2 sessions beforehand and the durable `agent_http_sessions` table held
only 1 row afterward. Investigation (corroborated by an independent code review)
showed the rollout was a red herring and the real cause is destructive idle-TTL
garbage collection:

- **Create-time persistence already exists.** `POST /sessions` →
  `SessionManager.create_session` persists synchronously inside the lock with
  rollback on failure (`session_manager.py:2754`), through the fenced SQLite/PG
  durable store. A created session is durable before the call returns.
- **Restart does not delete session rows.** Lifespan shutdown calls
  `shutdown_session_runtime` (explicitly "releases runtime resources without
  deleting metadata", `session_manager.py:2945`), `release_owned_sessions` (lease
  release only), then `close`. Startup runs owner-lease backfill and stale-run
  recovery; neither deletes session rows.
- **The actual deleter is idle GC.** A background task runs every 60s and calls
  `cleanup_idle_sessions(30)` (`http_server.py:2308`, `SESSION_IDLE_TIMEOUT_MINUTES
  = 30`). For any session idle > 30 minutes it calls `close_session`
  (`session_manager.py:3232`), which routes through `_remove_session_async_no_lock`
  → `delete_session` (`session_manager.py:2502`/`2509`) and **deletes the durable
  metadata row**. This is restart-independent: any session left idle for 30
  minutes is destroyed; a restart merely triggers the next 60s sweep immediately
  against already-stale `last_activity`.

So "durable session" today means *persisted until explicit close OR idle GC*, not
*persisted until the user/admin deletes it*. For a single-user personal agent
that is the wrong contract: idle is resource pressure, not an instruction to
destroy the user's conversation.

Separately, the chart sets no `terminationGracePeriodSeconds` and no `preStop`
hook, so a rolling-update SIGTERM can SIGKILL the process before in-flight turns
drain — a real risk now that the config-checksum annotation (PR #606) auto-rolls
the pod on every config change.

## Decision

**D1 — Idle GC must not destructively delete durable sessions (primary).**
Idle cleanup is resource reclamation, not deletion. `cleanup_idle_sessions` stops
routing idle sessions through `close_session`/`delete_session` and instead only
shuts down the runtime and releases the owner lease, preserving the durable
session metadata, tape, runs/events, and checkpoints. After idle cleanup the
session is still listed by `GET /sessions` and its runtime is lazily rebuilt on
the next prompt. Destructive deletion happens **only** on an explicit user/admin
close/delete, never from a TTL sweep.

**D2 — Graceful drain on shutdown (in-flight-turn hardening).** On SIGTERM the
process flushes in-flight session state, marks interrupted turns, and releases
owner leases before exit; the Helm chart sets an adequate
`terminationGracePeriodSeconds` and a `preStop` hook (or the container handles
SIGTERM) so Kubernetes does not SIGKILL mid-drain. This is hardening for the
config-checksum auto-roll and manual rollouts — it is **not** the cause of the
lost session row (D1 is), and must not be documented as such.

**D3 — Optional runtime rehydration / resume semantics.** Store-backed listing
(`list_sessions_async`) and lazy metadata hydration (`get_session_async`,
`Session.from_store_data`) already exist, so listing survives restart once D1
stops deleting rows. The remaining gap — eagerly rebuilding the in-memory runtime
at boot and resuming an interrupted turn from its last checkpoint (ADR-0055) — is
optional. This ADR does **not** promise recovery of a turn whose in-process work
was killed mid-execution; resume is best-effort from the last durable checkpoint.

Scope is single-replica + sqlite-on-PVC. Multi-replica HA failover is out of
scope.

## Alternatives Rejected

- **Only lengthen the idle TTL.** Rejected: deferring the destructive sweep does
  not change that idle GC deletes durable user state; it just moves the bug
  later. The contract, not the timeout, is wrong.
- **Disable idle GC entirely.** Rejected: idle runtimes would stay resident in
  memory indefinitely. D1 keeps reclamation (runtime shutdown + lease release)
  while removing only the destructive deletion.
- **Keep the original "P1 create-time persistence" as the fix.** Rejected: that
  premise was factually wrong — create-time persistence already exists
  (`session_manager.py:2754`); it was never the gap.
- **Multi-replica HA failover (ADR-0013, 2+ replicas on shared storage).**
  Rejected: contradicts the deliberate single-replica sqlite-on-PVC cutover and
  is overkill for a single-user agent. The owner-lease machinery stays for
  correctness, not HA.
- **Gate the #606 config-checksum auto-roll behind a default-off toggle.**
  Rejected by maintainer decision: keep auto-roll; achieve safety via D1 (no
  destructive idle delete) + D2 (graceful drain) instead.

## Acceptance Criteria

Implementation pending; landed as separate PRs (D1 then D2) after this docs PR.
Intended tests and gating command:

- [ ] `test_idle_cleanup_preserves_durable_session` — after idle cleanup, the
  session row, tape, runs, and checkpoints still exist and `GET /sessions`
  returns the session.
- [ ] `test_idle_cleanup_shuts_down_runtime_and_releases_lease` — idle cleanup
  frees the in-memory runtime and releases the owner lease.
- [ ] `test_idle_cleaned_session_rebuilds_runtime_on_next_prompt` — a prompt
  after idle cleanup lazily rebuilds the runtime and proceeds.
- [ ] `test_explicit_close_still_deletes_session` — explicit close/delete still
  removes the durable row (D1 only changes the idle path).
- [ ] `test_graceful_shutdown_drains_inflight_turn` — SIGTERM flushes/marks an
  in-flight turn and releases leases within the grace period.
- [ ] `test_helm_deployment_sets_graceful_drain` — chart renders a non-default
  `terminationGracePeriodSeconds` and a `preStop`/SIGTERM drain path.
- [ ] `uv run pytest tests/ui/test_http_server.py tests/coding_agent/test_session_manager*.py tests/deploy/test_helm_chart.py -k "idle or drain or rehydrate" -q`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0067-local-sqlite-durable-tape-runtime.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0071-argocd-gitops-deployment.md` (depends on D1 idle-GC fix + D2 graceful drain)
- `src/coding_agent/server/http_server.py` (`_cleanup_idle_sessions`, `SESSION_IDLE_TIMEOUT_MINUTES`, `lifespan`, `GET /sessions`)
- `src/coding_agent/server/session_manager.py` (`create_session`, `cleanup_idle_sessions`, `close_session`, `_remove_session_async_no_lock`, `shutdown_session_runtime`, `_persist_session_async`)
- `src/coding_agent/stores/durable_local.py` (`delete_session`)
- `helm/templates/deployment.yaml` (config-checksum annotation, PR #606; no `terminationGracePeriodSeconds`/`preStop` today)
