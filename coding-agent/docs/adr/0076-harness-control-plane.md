# ADR-0076: Adopt a local-daemon harness control plane

**Status**: Proposed
**Date**: 2026-08-19

## Context

`coding_agent` already has a local-daemon product shape (ADR-0058), per-session
transactional fencing (ADR-0068), session resume (ADR-0055), and a restore rule
that keeps runtime runs for audit while hiding rolled-back work from the active
timeline (ADR-0075). Those decisions are not enough for the next harness.

The current remote path still lets an external worker own the agent loop,
mailbox, continuation, and store writes (ADR-0051 through ADR-0054). Runtime
events are run-scoped. There is no session-level `EventRecord`, no IDL-first
control protocol, no compensation A/B ledger, and no per-session allocator.
JSONL remains easy to treat as a second source of truth. Bee workflow stores
exist beside coding-agent stores and must not be migrated by this work.

`origin/main` already used ADR-0075 for checkpoint-restore run visibility. This
record is therefore ADR-0076. It must compose with ADR-0075's superseded-run
fields rather than invent a second way to hide or delete restored runs.

This ADR is the P0 markdown decision. It names later IDL and spike artifacts. It
does not implement protocol, storage, daemon, or frontend code.

## Decision

Serve the local coding agent only. This packet does not migrate bee and does not
add bee capabilities. From P2, a cutover session must reject Bee mutations of
its tape or unit of work; that isolation is a contract test. Bee keeps writing
its legacy stores.

Cut remote loop ownership. The agent loop, mailbox, continuation, and
authoritative store writer live only in the local daemon. Remote execution is a
`CommandExecutor` implementation: tools and workspace only. It is not a loop
owner, mailbox owner, continuation owner, or store writer. Cloud-agent products
are out of scope and do not share this bus.

### Relation to prior ADRs

- **ADR-0051, ADR-0052, and ADR-0053 are superseded in direction.** Claim /
  heartbeat / finalize worker endpoints, derived worker-health surfaces, and a
  remote process that owns the loop are not the target architecture. Legacy
  HTTP aliases may remain only as compatibility until P1 removes the remote
  loop path. Do not flip their Status while this ADR is Proposed. When this
  ADR is Accepted and the P1 remote-loop cut lands, mark 0051–0053
  `Superseded`.
- **ADR-0054 is revised.** Keep executor/runtime terminology and the demotion of
  unmanaged `run --goal`. Drop the `local_attached` / `external_worker` loop
  that claims o6n-managed runs and executes the agent turn outside the daemon.
  Remote placement is workspace/tool execution through `CommandExecutor`.
- **ADR-0055 is composed and only partly superseded.** Keep resume creating a
  new run, refusing to reconnect a dead process, and treating
  `waiting_for_approval` as non-durable run status. Interrupted-run resume
  metadata remains valid for run lineage. Approval wait is durable waiting on
  the effect ledger. What this ADR drops is ADR-0055's local-attached resume
  dispatch: recording a new requested run on the same attached control-plane
  path as prompt continuation. That path is replaced by in-daemon loop
  ownership plus `CommandExecutor` remote placement.
- **ADR-0058 remains the local-daemon skeleton and is revised on placement.**
  Clients still do not own runtime execution. The control plane still must not
  *be* the loop. The loop stays inside the daemon process via
  `LocalDaemonExecutor`. ADR-0058's future attached-worker and managed-pool loop
  extensions are not taken. Singleton daemon, unix-socket discovery, and
  storage-instance writer lease land in P4, not earlier.
- **ADR-0068 remains the P1–P3 fencing contract.** Fencing is per-session:
  `{session_id, owner_id, epoch}` plus a live owner lease, checked in the same
  transaction as the protected mutation and target-ownership proof. Do not
  describe ADR-0068 as storage-instance fencing. P4 adds a storage-instance
  writer lease on top of that session fence. A shared PostgreSQL deployment
  uses a store-domain lease, not a machine-local instance lease.
- **ADR-0075 remains the restore timeline contract.** After checkpoint restore,
  run supersession and lineage reuse ADR-0075's nullable run fields
  `superseded_at` and `superseded_by_checkpoint_id`. Projection-epoch
  identifiers for delta/settled cursors are allocated by restore/bootstrap;
  they are not stored in those run fields. Restore must not delete runs or
  events.
  Active-timeline queries continue to hide superseded runs; audit queries
  continue to return them. New runs created after restore stay active because
  supersession is written only for rows present when restore commits. JSONL
  restore does not gain projection atomicity.

### Authoritative unit of work

The only authoritative unit of work is, in one fenced transaction:

- session-level `EventRecord`
- session/run state
- mailbox and disposition
- continuation / effect ledger
- operation receipts

JSONL tape is a derived export, not a second writer and not a restore authority.
`project(EventRecord) -> [ItemDelta]` is a pure function applied before
transport. Transport is a thin encoder.

`session_seq` is monotonic per session on the physical log. It does not reset
because a restore opened a new projection epoch.

Checkpoint capture records mailbox lane cuts. Restore linearizes mailbox replay
and in-flight effects in the same fenced transaction that applies ADR-0075
supersession and restores tape, session state, topics, and the checkpoint set.

### Cursors and epochs

A restore or other lineage break opens a new projection epoch. It does not
rewrite or delete the physical log.

- **raw** cursors follow the physical log and remain valid across epochs.
- **delta** and **settled** cursors bind `(projection, epoch)` and must not be
  applied to a different projection or epoch.

Cross-host `key_expired` is a P2 fact-source contract: replay in full from the
retention floor, or accept a trusted handoff. It is not deferred to the P4
singleton/socket work.

### Effects and approval

Allocate `effect_id` when the approval wait is first established, and persist
it on the effect ledger. After checkpoint restore, a later approve or resume
must reuse that same `effect_id`. It must not allocate a second id and must not
dispatch a second attempt.

If the effect is `dispatched` or `unknown`, ordinary approval or resume must
not dispatch again. Compensation is a separate effect. The only admission
exception is the compensation contract below.

`ExecutionHandle` stays an in-process agentkit Python `Protocol`. It is not a
wire type. The IDL may carry `EffectRef` and must not carry `ExecutionHandle`.

### Compensation

Compensation is three records, not one status field.

1. **Client operation receipt.** Identity `{generation, compensation_effect_id}`
   is immutable and first-write-wins. Replay of the same client key returns that
   identity. Status fields are always read live from current A/B. The first
   response's `unknown` must not be frozen onto later reads.
2. **A — attempt-state**, keyed by `compensation_effect_id`, evolvable:
   `prepared → dispatched → unknown | failed | completed`. `unknown` is a
   transient attempt state, not an immutable receipt.
3. **B — resolving settlement**, written only after a successful completed
   check. Only `B(resolved)` lifts the quiescent gate and forbids further
   compensation.

Classification invariants:

- Latest `A=completed` and `B` absent is **repair-only**. Refuse every new
  compensate admission, including a new client key (C2). Do not open
  `generation+1`. The only legal writer is a repair unit of work that completes
  B.
- `A=failed` does not lift quiescent. It allows `generation+1`.
- Admission, repair, and C2 are linearized on the original-effect
  `compensation_cas` (row lock plus CAS counter). That counter is not the
  original effect's status. A CAS conflict means re-read and reclassify. The
  write must pass ADR-0068 session fencing and target ownership in the same
  transaction.

Crash-repair atomic write set:

- Unobserved: write neither A terminal state nor B; leave `dispatched` /
  `unknown`.
- Observed failed, A not yet terminal: write only `A(failed)`; do not write B.
- Observed completed, A not yet written: write `A(completed)` and B in the same
  unit of work.
- Observed completed, `A(completed)` already written, B absent: write only B;
  do not rewrite A; do not open C2.
- B already written: return B idempotently.

### Protocol, frontend, and phasing

IDL-first comes after this ADR: OpenRPC plus JSON Schema Draft 7 as the later
source of truth, generating Pydantic and TypeScript. Do not hand-write a
parallel wire type system. `u64` sequences travel as decimal strings.

P5 frontend is React only: a three-pane shell plus a node registry. Do not port
Cordis. A single-file `index.html` UI is not the target.

| Phase | Scope |
| --- | --- |
| P0 | This ADR. Name, do not implement, later IDL plus compensation / restore / bootstrap spikes. |
| P1 | Retire the remote loop path. Do not migrate bee. |
| P2 | Authoritative fact source, cursors, `key_expired`, Bee isolation. Every authoritative mutation uses the unit of work above. |
| P3 | Control protocol. Unix socket is marked unavailable until P4. |
| P4 | Singleton daemon, storage-instance writer lease, unix socket. Shared PG uses a store-domain lease. |
| P5 | React three-pane UI and node registry. |

P1–P3 use only ADR-0068 per-session fencing. Do not implement or document a
storage-instance writer lease before P4.

Intended later artifacts (not part of this change):

- `protocol/harness/openrpc.yaml` and JSON Schema Draft 7 documents
- a compensation spike covering A/B/receipt, `compensation_cas`, and C2
- a restore spike covering mailbox lane cuts, effect reuse, and ADR-0075
  supersession
- a bootstrap spike covering first-session allocator and epoch 0

## Alternatives Rejected

- Migrate bee or share this unit of work with Bee stores — rejected so cutover
  isolation stays a contract, not a dual-writer migration.
- Keep ADR-0051 through ADR-0053 remote loop ownership — rejected because a
  remote worker that writes mailbox, continuation, or store state is a second
  control plane.
- Treat ADR-0068 as storage-instance fencing, or land an instance lease in
  P1–P3 — rejected because 0068 is `{session_id, owner_id, epoch}` only. Instance
  and store-domain leases are P4 / shared-PG concerns.
- Put `ExecutionHandle` on the wire — rejected because it is an in-process
  Python protocol. IDL stops at `EffectRef`.
- Keep JSONL as an authoritative tape or restore authority — rejected because
  it cannot participate in the fenced unit of work. ADR-0075 already treats
  JSONL as legacy/debug.
- Delete post-checkpoint runs, or add a second "hide these runs" scheme —
  rejected because ADR-0075 already preserves audit rows and marks them
  superseded. Epoch/lineage must reuse those fields.
- Port Cordis or keep a single-file HTML UI as the product frontend —
  rejected. The browser UI consumes a network contract; React plus a node
  registry is the P5 target.
- Implement protocol, daemon, or frontend in this packet — rejected. P0 is the
  decision record plus named follow-up artifacts.
- Freeze receipt status on first `unknown`, or treat `A(completed) ∧ B absent`
  as a new-admission window — rejected. Receipt identity is immutable; receipt
  status is live. Completed-without-B is repair-only.
- Defer cross-host `key_expired` to P4 — rejected. Cursor correctness is a P2
  fact-source contract, independent of singleton socket work.

## Acceptance Criteria

These tests do not exist yet. They are the intended gate for later
implementation packets, not for this markdown-only change.

- [ ] `test_cutover_session_rejects_bee_tape_or_uow_mutation`
- [ ] `test_bee_legacy_store_writes_remain_outside_harness_uow`
- [ ] `test_remote_execution_is_command_executor_only`
- [ ] `test_remote_path_has_no_loop_mailbox_continuation_or_store_writer`
- [ ] `test_p1_p3_fencing_is_adr_0068_session_owner_epoch_only`
- [ ] `test_p4_storage_instance_writer_lease_is_distinct_from_adr_0068`
- [ ] `test_shared_pg_uses_store_domain_lease`
- [ ] `test_idl_exposes_effect_ref_and_not_execution_handle`
- [ ] `test_authoritative_uow_commits_event_record_state_mailbox_effects_and_receipts`
- [ ] `test_jsonl_tape_is_derived_export_not_authoritative`
- [ ] `test_session_seq_is_monotonic_per_session_across_restore_epochs`
- [ ] `test_raw_cursor_follows_physical_log_across_epochs`
- [ ] `test_delta_and_settled_cursors_bind_projection_and_epoch`
- [ ] `test_cross_host_key_expired_contract_lands_at_p2`
- [ ] `test_effect_id_allocated_when_approval_wait_is_established`
- [ ] `test_restore_then_reapprove_reuses_same_effect_id`
- [ ] `test_dispatched_or_unknown_blocks_normal_approval_dispatch`
- [ ] `test_compensation_receipt_identity_is_immutable_and_status_is_live`
- [ ] `test_a_completed_without_b_is_repair_only_and_rejects_c2`
- [ ] `test_repair_and_c2_concurrent_when_a_completed_b_absent_rejects_c2_and_writes_b_only`
- [ ] `test_failed_a_does_not_lift_quiescent_and_allows_generation_plus_one`
- [ ] `test_only_b_resolved_lifts_quiescent_and_forbids_further_compensate`
- [ ] `test_admission_repair_and_c2_linearize_on_compensation_cas`
- [ ] `test_crash_repair_atomic_write_set_for_unobserved_failed_and_completed`
- [ ] `test_restore_marks_adr_0075_superseded_runs_and_does_not_delete_them`
- [ ] `test_restore_linearizes_mailbox_lane_cuts_and_effects`
- [ ] `uv run pytest tests/coding_agent/test_harness_control_plane_adr_0076.py tests/coding_agent/test_compensation_cas.py tests/coding_agent/test_cutover_bee_isolation.py tests/coding_agent/test_sqlite_runtime_store.py tests/coding_agent/test_pg_runtime_store.py -k "harness_control_plane or compensation_cas or cutover_bee or superseded or effect_id or session_seq or key_expired" -v`

## References

- `docs/adr/0051-external-worker-execution-control-plane.md`
- `docs/adr/0052-external-worker-usable-control-plane.md`
- `docs/adr/0053-advanced-external-worker-control-plane-foundations.md`
- `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0070-restart-safe-live-sessions.md`
- `docs/adr/0075-checkpoint-restore-active-run-timeline.md`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/durable_local.py`
- `src/coding_agent/executors/`
