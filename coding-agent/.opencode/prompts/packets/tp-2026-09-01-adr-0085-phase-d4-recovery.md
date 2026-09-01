# Task Packet

packet_id: tp-2026-09-01-adr-0085-phase-d4-recovery
packet_revision: 5
role: implementer
baseline_ref: origin/main
baseline_sha: b5bcd4b37cd5168c49c5f39dfa260d1776057481
branch: feat/adr-0085-phase-d4-recovery

## Goal

Implement ADR-0085 Phase D4 as a non-serving recovery contract: retain an indeterminate effect plan, commit evidence-backed reconciliation before engine re-entry, adopt exact settlement/reconciliation replay, and fence takeover on durable old-executor quiescence.

## Scope

- Preserve the frozen four-member `EngineStepInput` union and every Phase C public signature/export. Do not add a reconciliation input or public port.
- An indeterminate `EffectSettled` keeps the first pending `EffectPlan`, including its original dispatch authorization transition identity. It commits `DISPATCHED -> UNKNOWN`, records its own consume-once input identity in committed runtime state, returns `BlockedAction(reason="indeterminate_dispatch")`, appends no tool message, and proposes no final tool-result fact.
- Dispatch authorization writes a host-private `active_effect_authorization` marker beside the pending plan in the same committed state. It binds effect, attempt, tool identity, authorization transition ID, and dispatch owner epoch. A later unrelated state commit must preserve it. Normal and indeterminate settlement validation uses this retained identity when `commit_ref` has moved.
- The committed runtime state uses host-private unknown/reconciled markers. The unknown marker binds effect ID, attempt ID, tool identity, retained dispatch authorization transition ID, dispatch owner epoch, and indeterminate settlement input ID.
- Before reconciliation, a host evidence writer records canonical `EffectReconciliationEvidence` in durable storage. It binds session, effect, attempt, retained authorization, reconciliation owner epoch, outcome, and the complete terminal settlement payload. Completed evidence carries `result`; failed evidence carries `result`, `reason_code`, and `reason_message`. Exact duplicate insertion is idempotent; the same evidence identity with different content conflicts.
- `CommitReconciliationRequest` validates its record against the committed unknown marker and the locked evidence row. The row's session, effect, attempt, retained authorization, reconciliation owner epoch, outcome, and `evidence_ref` must exactly equal the unknown marker and record; a live foreign evidence row is rejected. The reconciliation transition identity must be fresh, and a non-unknown state cannot reconcile.
- Reconciliation is two commits. First, the host calls the existing `commit_reconciliation` contract and atomically commits `UNKNOWN -> COMPLETED|FAILED` plus a reconciled runtime marker copied from the locked canonical evidence row. The marker records the stable post-reconciliation `input_id`, old dispatch owner epoch, and historical reconciliation owner epoch. Only that committed/replayed state may enter `SegmentCoordinator.run` with the existing completed/failed `EffectSettled` variant.
- The post-reconciliation settlement reconstructs the canonical terminal payload and retained dispatch authorization byte-for-byte from the committed marker. Its consume-once `input_id` is the stable marker value and differs from the indeterminate input. Its `owner_epoch` is always the current fenced `RunSegmentRequest.owner_epoch`; the marker's reconciliation owner epoch is evidence, not a future request fence. A takeover after reconciliation but before the final state/fact commit therefore reuses the same input/transition identity under the new current epoch.
- Post-reconciliation engine re-entry removes the retained plan and atomically consumes and clears `active_effect_authorization`, `unknown_effect`, and `reconciled_effect` in the same proposal. It appends one final tool message and proposes exactly one final tool-result fact. The coordinator commits this as a state/fact-only `commit_transition`; it must not call `commit_settlement` or mutate the effect ledger a second time. Exact replay adopts the already-cleared committed state.
- Exact `commit_transition`, indeterminate `commit_settlement`, and reconciliation receipt replay adopts the returned committed state and continues at the already-determined next action. Dispatch-authorization replay remains outside D4 because an `ExactReplayCommitResult` does not carry a permit; E owns permit recovery.
- A recoverable `CASConflictCommitResult` is not terminal. Before any adapter/executor action is repeated, the coordinator adopts `current_state`, revalidates and re-proposes the same consume-once input, and retries the same commit identity. Settlement CAS recovery never calls `EffectExecutor` again, never creates a new attempt, and preserves `run_id`, steps, pending plan, and authorization marker.
- Reconciliation CAS recovery reloads the returned/current durable state and retries the same `ReconciliationRecord` and canonical evidence only while the effect remains the matching `UNKNOWN` attempt. A matching exact receipt is adopted; a terminal or mismatched effect without that receipt fails closed. Owner takeover supplies the already-incremented owner epoch and old owners remain fenced.
- `Initial` is rejected while committed runtime state contains an unknown effect, an unconsumed reconciled marker, or an outstanding pending plan. After the final state/fact commit consumes the marker and plan, a later legitimate `Initial` is accepted. Automatic retry and redispatch from `UNKNOWN` are impossible.
- Extend the host-private authoritative UoW with a retained-authorization precondition and canonical evidence identity for reconciliation. They are required exactly for `UNKNOWN -> COMPLETED|FAILED` with a `ReconciliationRecord`, forbidden otherwise, snapshotted, and fingerprinted outside `EffectMutation.payload`.
- Add host-private durable executor-attempt rows on both backends. Dispatch authorization atomically creates the exact row in `authorized_unclaimed` state, bound to session, effect, attempt, authorization, and dispatch owner epoch. Exact row creation/reservation/start/quiescence replay is idempotent and conflicting content is rejected.
- The Phase E concrete `EffectExecutor` is a host wrapper implementing the frozen `execute(permit, cancellation)` signature. Inside that wrapper, before invoking the underlying external executor, it uses host-private store APIs to reserve the exact row under the live owner and a bounded reservation lease, then atomically transitions `reserved -> started` under the still-live owner immediately before the external call. Calling the wrapper is not external execution; no underlying effect may start before the durable `started` record exists.
- Both stores adopt an exact transition receipt before recovery checks. Otherwise, under the existing transaction/row lock and before state CAS or any write, they lock the current effect slot, canonical reconciliation evidence row, and executor-attempt row as applicable. Wrong authorization/evidence/payload writes nothing.
- For same-run owner takeover, compare the reconciliation owner epoch with the dispatch owner epoch retained in the effect slot. A missing or mismatched executor row rejects. `authorized_unclaimed` is safe after the new owner epoch fences the old owner. A `reserved` row may be revoked to `quiescent` only after its lease expires and the reserving owner epoch is fenced, because `started` requires a live-owner transaction. A `started` row blocks takeover reconciliation until durable executor quiescence evidence transitions the exact row to `quiescent`. Same-owner reconciliation does not require takeover quiescence.
- D4 adds the non-serving durable evidence/claim APIs and schema that Phase E will consume. It adds no production caller, coordinator routing, concrete `CommitPort`, concrete executor, child recovery lease, alias removal, runtime version, or activation.

### Intended internal state

Names may move inside the allowed files, but the committed identity and validation rules are fixed:

```python
active_effect_authorization = {
    "effect_id": str,
    "attempt_id": str,
    "tool_call_id": str,
    "tool_name": str,
    "authorization_transition_id": str,
    "dispatch_owner_epoch": int,
}

unknown_effect = {
    "effect_id": str,
    "attempt_id": str,
    "tool_call_id": str,
    "tool_name": str,
    "authorization_transition_id": str,
    "dispatch_owner_epoch": int,
    "indeterminate_input_id": str,
}

reconciled_effect = {
    **unknown_effect,
    "reconciliation_transition_id": str,
    "evidence_ref": str,
    "reconciliation_owner_epoch": int,
    "reconciled_input_id": str,
    "outcome": "completed" | "failed",
    "result": JSONValue,
    "reason_code": str | None,
    "reason_message": str | None,
}
```

The final state/fact proposal removes `active_effect_authorization`,
`unknown_effect`, and `reconciled_effect`. Durable recovery rows bind the same
identity:

```python
EffectReconciliationEvidence(
    evidence_ref,
    session_id,
    effect_id,
    attempt_id,
    authorization_transition_id,
    reconciliation_owner_epoch,
    outcome,
    result,
    reason_code,
    reason_message,
)

ExecutorAttemptRecord(
    session_id,
    effect_id,
    attempt_id,
    authorization_transition_id,
    dispatch_owner_epoch,
    executor_id,
    claim_generation,
    reservation_lease_expires_at,
    status,  # authorized_unclaimed | reserved | started | quiescent
    quiescence_evidence_ref,
)

expected_reconciliation_authorization_transition_id: str | None
reconciliation_evidence_ref: str | None
```

## Allowed production files

- `src/agentkit/runtime/contracts.py`
- `src/agentkit/runtime/engine.py`
- `src/agentkit/runtime/coordinator.py`
- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/pg_durable/uow.py`
- `src/coding_agent/stores/local_durable/core.py`
- `src/coding_agent/stores/pg_durable/sql_harness.py`

Allowed tests:

- `tests/agentkit/runtime/test_engine.py`
- `tests/agentkit/runtime/test_coordinator.py`
- `tests/agentkit/runtime/test_runtime_messages.py`
- `tests/coding_agent/test_runtime_phase_b_uow.py`
- `tests/coding_agent/test_sqlite_local_durable_fencing.py`
- `tests/coding_agent/test_pg_durable_fencing.py`
- `tests/coding_agent/test_harness_p2_fact_source.py`

## Authority

- `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`, Phase D4 and D4 acceptance criteria.
- Re-adopted D4 rules in `docs/adr/0084-stage-phase-d-capability-inputs-and-recovery-cutovers.md`.
- Re-adopted effect recovery rules in `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`.
- ADR-0068 owner fencing and ADR-0076 authoritative UoW/fact-source authority.
- `postmortem/patterns/PM-0028-settle-post-dispatch-outcomes-before-exit.md`.

## Non-goals

- No new `EngineStepInput`, public method, request/result field, port signature, or export.
- No live legacy `PipelineAdapter`, `RuntimeMessageBus`, approval, child, checkpoint, wire, or session routing change.
- No generic retry from `UNKNOWN`, no new effect attempt, and no redispatch of the retained attempt.
- No concrete SQLite/PostgreSQL `CommitPort` adapter or `EffectExecutor`; those are Phase E.
- No recovered-child lease or target-aware stale authorization loop; those are Phase E.
- No runtime-version, activation, projector/outbox, checkpoint rejection, or alias removal; those are Phase F.
- Do not change the legacy `RuntimeRunRecoveryService`; without the Phase F version fence it must retain legacy behavior.

## Acceptance criteria

- `test_indeterminate_settlement_keeps_pending_plan_and_commits_no_final_tool_fact`
- `test_reconciliation_reenters_with_existing_effect_settled_input`
- `test_reconciled_effect_commits_exactly_one_final_tool_result_fact`
- `test_post_reconciliation_settlement_input_differs_from_indeterminate_input`
- `test_run_segment_request_accepts_retained_authorization_after_commit_ref_moves`
- `test_post_reconciliation_reentry_does_not_mutate_effect_ledger_twice`
- `test_reconciliation_exact_replay_is_adopted`
- `test_exact_replay_adopts_committed_state_and_continues`
- `test_reconciliation_rejects_wrong_attempt_authorization_or_epoch`
- `test_recoverable_settlement_cas_reloads_and_reproposes_without_reexecution`
- `test_reconciliation_cas_reloads_and_retries_same_record`
- `test_reconciliation_wrong_authorization_writes_nothing_sqlite`
- `test_reconciliation_wrong_authorization_writes_nothing_postgresql`
- `test_reconciliation_evidence_reconstructs_terminal_settlement_after_crash`
- `test_reconciliation_evidence_identity_conflict_is_rejected`
- `test_takeover_waits_for_authoritative_executor_quiescence_row`
- `test_takeover_waits_for_old_executor_quiescence`
- `test_same_run_takeover_fences_old_owner_with_new_owner_epoch`
- `test_takeover_after_reconciliation_reuses_input_under_current_owner_epoch`
- `test_dispatch_creates_authorized_unclaimed_executor_row_atomically`
- `test_authorized_unclaimed_takeover_is_safe_only_after_owner_fence`
- `test_started_or_mismatched_executor_claim_blocks_takeover`
- `test_reserved_executor_revokes_only_after_owner_fence_and_lease_expiry`
- `test_executor_reserve_start_and_quiescence_replay_is_idempotent`
- `test_reconciliation_evidence_and_takeover_rows_are_fingerprinted_and_replayed`
- `test_unknown_effect_is_never_automatically_retried`
- `test_reconciled_marker_is_consumed_and_later_initial_is_accepted`
- `test_crash_retains_run_and_does_not_write_interrupted`
- Phase C public signatures and exports are byte-for-byte unchanged.
- The diff contains no production caller and stays inside the allowlist.

## Target tests

- `uv run pytest tests/agentkit/runtime/test_engine.py tests/agentkit/runtime/test_coordinator.py tests/agentkit/runtime/test_runtime_messages.py -q`
- `uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or executor_exception or executor_task_cancellation" -q`
- `uv run pytest tests/coding_agent/test_runtime_phase_b_uow.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_harness_p2_fact_source.py -q`
- `uv run pytest tests/agentkit/ tests/coding_agent/ -q`
- `uv run ruff check` and `uv run ruff format --check` for every changed Python file.

## Loop policy

- Write each behavioral test first and observe the focused failure.
- Implement the smallest change that satisfies the accepted D4 contract.
- Run the exact target tests.
- Obtain PASS from GPT-5.6 Sol medium, Grok 4.6 high, and Kimi K3 high before implementation.
- After implementation, obtain PASS from the same three reviewers. Any FAIL requires a fix and a complete three-review rerun.

## Stop conditions

- Stop and revise ADR-0085 if D4 requires a fifth `EngineStepInput`, a changed public Phase C signature/export, or a concrete Phase E port.
- Stop if exact dispatch replay would require synthesizing a permit; leave it to E.
- Stop if durable quiescence cannot be represented as a separately persisted, identity-bound executor-attempt row checked inside the reconciliation transaction without changing live legacy recovery.
- Stop if implementation would route a production session onto the new coordinator before Phase F.
