# ADR-0085: Restage durable runtime activation through Phase F

**Status**: Accepted
**Date**: 2026-09-01
**Last amended**: 2026-09-01 (Phase E two-watermark recovery and atomic unstarted closeout)

Supersedes ADR-0018, ADR-0083, and ADR-0084. This record re-adopts every retained decision from those records, carries their earlier supersession chain, retains ADR-0010 (synchronize checkpoint restore with active turns), and replaces the D3b-through-F schedule.

## Context

ADR-0083 defined the persistence-free `AgentEngine`, standard `SegmentCoordinator`, frozen Phase C request/result contracts, typed effect graph, durable mailbox authority, canonical `EventRecord` facts, and the D-through-H migration. ADR-0084 then split Phase D into capability isolation, semantic grounding, mailbox admission, writer activation, and reconciliation.

Phase D1, D2, and D3a are complete. D3a added durable command admission and a session-wide `dispatch_generation`. The remaining ADR-0084 D3b slice is too broad for the current deployment boundary: it combines a store precondition, live approval and child routing, coordinator activation, effect-writer migration, and removal of legacy aliases. The repository still serves legacy `PipelineAdapter` sessions and has no immutable per-session runtime version. Removing their writer path before the version fence would break existing sessions; activating a new writer while old daemons remain live would permit mixed semantics.

Child execution adds two requirements that the earlier records did not fully compose. First, a foreground child is itself the execution of a dispatched parent subagent effect. Its blocked and recovery behavior must preserve the parent's claimed permit and PM-0028 settlement rule. Second, child facts share the parent session stream but must not appear as duplicate connected-chat facts. ADR-0018's owner-local contract remains correct, while its unconditional RuntimeMessageBus summary rule conflicts with the new runtime's canonical parent tool-result fact.

The activation sequence therefore needs one accepted authority that preserves the completed and frozen work, narrows D3b to an additive store primitive, defines child and recovery behavior before service, and introduces an explicit non-rolling Phase F activation barrier.

## Decision

### Governance and keep/replace matrix

Accepted ADR bodies remain historical records. Their status metadata changes to `Superseded`; this record owns the active decisions below.

| Source section | Disposition in ADR-0085 |
|---|---|
| ADR-0018 Context and owner-local invariant | Re-adopted. A child remains an execution-local implementation detail of the current parent session owner. |
| ADR-0018 environment, run-context, trace, tool-filter, write-lease, cloud-client, cancellation, and failure rules | Re-adopted under "Owner-local child contract" below. |
| ADR-0018 unconditional RuntimeMessageBus summary rule | Replaced by runtime-version-specific summary transport. |
| ADR-0018 cross-process child-worker deferral | Re-adopted. Owner takeover after durable old-owner quiescence is failover of the parent owner, not an independently scheduled child worker. |
| ADR-0018 implementation plan and completed acceptance criteria | Retained as historical implementation evidence. New-runtime child criteria are added below. |
| ADR-0083 Context and non-goals | Re-adopted, except the legacy `Pipeline` removal schedule is now governed by the Phase F version fence. |
| ADR-0083 Engine and coordinator public contracts | Re-adopted unchanged. Phase C public signatures and the four-member `EngineStepInput` union remain frozen. |
| ADR-0083 segment outcomes, cancellation, atomic state/log binding, commands, typed effect graph, permits, receipts, canonical facts, and checkpoint Option B | Re-adopted. |
| ADR-0083 post-stale "re-probes and re-proposes" sentence | Replaced by the narrow authorization retry loop below; the engine is not re-run after a prepared transition has committed. |
| ADR-0083 indeterminate final-fact behavior | Replaced by ADR-0084 D4 and re-adopted here: indeterminate settlement retains the plan and writes no final tool-result fact. |
| ADR-0083 phases and implementation plan | D1, D2, D3a, G, and H are retained. D3b, D4, E, and F are replaced by this record. |
| ADR-0083 relation to ADR-0001, ADR-0005, ADR-0006, and the two ADR-0010 records | Carried forward. The old checkpoint contracts stay superseded; ADR-0010 synchronize-checkpoint-restore remains retained for legacy restore and Phase G. |
| ADR-0083 preservation of ADR-0055 and ADR-0075 | Carried forward. Resume creates a new linked run; restore marks superseded runs without deleting them. Phase G reuses that lineage instead of creating a second scheme. |
| ADR-0083 acceptance criteria | Retained where their owning phase remains unchanged; D3b-through-F criteria are replaced below. |
| ADR-0084 capability-scoped hooks and D2 semantic grounding | Re-adopted as completed D1/D2 decisions. |
| ADR-0084 D3a mailbox admission and dispatch generation | Re-adopted as completed. Generation stays session-wide. |
| ADR-0084 D3b live activation and alias removal | Replaced. D3b becomes non-serving mailbox-cut enforcement; live activation and alias removal move to F. |
| ADR-0084 D4 reconciliation through `EffectSettled` | Re-adopted and extended with retained-plan, exact-replay, takeover, and child-recovery rules. |
| ADR-0084 implementation plan and D3b-through-D4 criteria | Replaced by this record. |
| ADR-0084 alternatives | Retained as historical rationale where they do not conflict with this restaging. |

ADR-0055 and ADR-0075 remain the run-lineage authorities: resume creates a new linked run, and restore marks prior runs superseded without deletion. ADR-0068 remains the SQLite fencing authority. ADR-0076 remains the authoritative unit-of-work and session fact-source authority. ADR-0077 remains the connected-chat projection authority and is supplemented by the child-fact membership rules below.

### Owner-local child contract

The active child invariant is:

> A child agent executes only inside the current parent session owner. The parent owner is the only actor that may start, run, cancel, emit tool deltas for, or publish canonical terminal output from that child.

The following ADR-0018 rules remain normative:

- A child inherits the parent `Environment`. A cloud-bound child uses the same `CloudEnvironment` and provider-neutral `CloudWorkspaceClient` supplied by `coding_agent`.
- A child derives `AgentRunContext` from the parent: same `session_id`, deterministic new child `run_id`, child agent ID, `parent_run_id`, inherited context budget, and merged trace metadata.
- The dispatcher overwrites spoofed `subagent.*` metadata. ADR-0017 continues to own `cloud.*` sanitization and authoritative `cloud.workspace_id` injection.
- Nested subagent delegation remains disabled in child toolsets.
- Mutating child tool events remain serialized by the owner-local child write lease. This is not a distributed child lock.
- Concrete cloud clients, credentials, and workspace lifecycle stay in `coding_agent`, not `agentkit`.
- Independently scheduled child processes, queue workers, callback workers, non-owner tool deltas, shared independently owned workspaces, and distributed child write leases remain deferred to a later ADR.
- Parent-owner failover may resume a child only after the old executor is durably quiescent and the new process owns the parent session. The child is still owner-local.

Summary transport is versioned:

- Legacy-runtime sessions keep ADR-0018's parent `RuntimeMessageBus` terminal summary behavior.
- New-runtime foreground child terminal output is exactly the canonical tool-result fact produced by settling the parent subagent effect. It is never also published as a mailbox or RuntimeMessageBus summary.
- New-runtime `publish_subagent_message` is reserved for unsolicited external messages. Such messages enter the durable targeted mailbox and are consumed once by a later `Initial` input.

### Frozen AgentKit boundary and narrow coordinator changes

The public Phase C types and signatures stay unchanged. No `Continue` input or fifth `EngineStepInput` variant is added. `RunSegmentRequest`, `DispatchAuthorizationRequest`, `CommitPort`, `ControlProbe`, the outcome union, and all existing adapter signatures remain source compatible.

ADR-0085 changes only internal coordinator behavior needed to honor the frozen contracts:

- `ControlProbe` is constructed per new-runtime run. Its snapshot always exposes the latest session `dispatch_generation` as `ControlGeneration`.
- `raised` is true only when a pending, unconsumed, dispatch-invalidating command targets the current run, an ancestor parent run, or explicit global scope.
- New-runtime cancel, interrupt, and approval-denial payloads must carry a valid `target_run_id` or explicit global scope. Missing or malformed targeting fails closed before admission. Legacy-runtime command routing is unchanged.
- A child approval wait returns `BlockedOutcome`. The host child wrapper waits on the targeted mailbox and control wake without returning from the outer parent `EffectExecutor.execute` invocation. It re-enters with `ApprovalResolved`, not `Initial`.
- `Initial` is used only for a genuinely new segment with no unconsumed settlement input and no outstanding prepared authorization.

When `authorize_dispatch` returns `StaleMailboxCutCommitResult`, the coordinator enters a cancellable internal loop:

1. Observe the target-aware control snapshot.
2. If a pending target-matching invalidator is raised, return `SafeYieldOutcome` to the owner-local control path. A targeted cancel/interrupt terminates the child path; an approval denial commits `prepared -> rejected` plus its disposition and then continues through `ApprovalResolved`.
3. Otherwise calculate `candidate_cut = max(result.current_mailbox_cut, snapshot.generation.value)`.
4. Update the working cut used by every later authorization in this `run()` call.
5. Rebuild the same `DispatchAuthorizationRequest` and host UoW with the candidate cut. Do not call `AgentEngine.propose`, do not replay the consumed input into another engine transition, and do not replace the prepared effect plan.
6. Retry. A second or later stale result repeats this loop; stale never falls through to `_authorized_result` or a terminal failure.

The coding-agent host wraps every new-runtime root and child coordinator call in one `DurableSegmentRunner`. Concrete CommitPorts retain a typed process-local marker when authorization returns exact replay. The runner consumes it, resolves one exact `EffectSettled` from D4 evidence, rebuilds the segment request at current durable state/cut, and re-enters the same coordinator until it obtains a non-marker outcome. No permit is minted and callers never receive `exact_replay_requires_recovery`. A process crash discards the marker. In E, child takeover reconstructs the same recovery under `RecoveredChildExecutionLease`; no new-runtime root is serving. Before F activation, root startup recovery must scan current-owner `DISPATCHED` attempts and apply the same D4 rules.

The authorization proposal `transition_id` remains the permit and receipt identity across pre-commit stale retries. A typed stale refusal writes no state, fact, disposition, effect mutation, or receipt, so it does not establish an identity/cut binding. The first successful receipt binds that identity to the successful cut through the mutation fingerprint. An exact lost-ack retry uses that identity and cut. Once a receipt exists, using a different cut with the same identity is a fingerprint conflict.

`expected_mailbox_cut` is not stored in `EffectMutation.payload`. `authorization_transition_id` remains stable there, preserving permit and settlement validation.

### Phase D3b: host-private dispatch-cut enforcement

D3b is additive and non-serving.

Add host-private `AuthoritativeUnitOfWork.expected_mailbox_cut`:

- It is structurally optional because most transitions do not authorize dispatch.
- It is required exactly when `effect_mutation` is `PREPARED -> DISPATCHED`.
- It is forbidden on every other transition.
- It must parse as an unsigned 64-bit value.
- It is included in `snapshot_transition_unit` and `transition_mutation_fingerprint`, outside `EffectMutation.payload`.
- Missing, malformed, out-of-range, or misplaced values raise `InvalidDispatchAuthorizationError` before storage mutation.

Both durable backends enforce the same transaction order:

1. Start the fenced transaction and validate owner authority/projection epoch.
2. Load an existing transition receipt. An exact fingerprint match returns the stored commit before the current-cut comparison; a mismatch fails deterministically.
3. Lock/read `session_fact_source` (`BEGIN IMMEDIATE` on SQLite, `FOR UPDATE` on PostgreSQL).
4. Require `dispatch_generation == expected_mailbox_cut` before operation-state CAS, dispositions, effect mutation, facts, sequence allocation, or receipt insertion.
5. On mismatch raise host-private `StaleMailboxCutError` with expected/current cuts. Write nothing and no receipt.

The concrete `CommitPort.authorize_dispatch` added in E maps invalid shape to frozen `InvalidTransitionCommitResult` and stale cut to frozen `StaleMailboxCutCommitResult`. Direct store callers receive the host-private exceptions. Generic `commit_transition` may not construct `PREPARED -> DISPATCHED` in production.

D3b adds no production caller, does not route approvals or children, does not change the coordinator, does not remove `settled` or rank replacement, and does not serve sessions on the new path.

### Phase D4: reconciliation before service

D4 remains non-serving and lands before E/F activation:

- An indeterminate settlement has its own consume-once identity, commits `DISPATCHED -> UNKNOWN`, retains the original pending plan and authorization identity, and writes no final tool-result fact.
- A reconciliation actor supplies durable external evidence. `commit_reconciliation` validates owner epoch, attempt identity, retained authorization, and evidence before `UNKNOWN -> COMPLETED|FAILED`.
- The engine re-enters through the existing `EffectSettled` variant only after reconciliation commits.
- Exact settlement/reconciliation receipt replay is adopted before any new write.
- Takeover increments the owner epoch and cannot issue or resume execution until the old executor is durably quiescent.
- Automatic retry from `UNKNOWN` is forbidden.

### Phase E: concrete ports and owner-local child execution

Phase E adds non-serving concrete SQLite and PostgreSQL implementations for the frozen AgentKit ports, plus the owner-local parent/child coordinators.

Child identity and facts:

- Child and parent share `session_id`, session owner/epoch, and the session `EventRecord` sequence.
- A child has a deterministic `run_id` and durable binding in the parent's prepared subagent plan.
- Run targeting is carried inside frozen `RuntimeCommand.payload`: nonempty `target_run_id`, or the mutually exclusive explicit `target_scope: "global"` for commands that ADR-0085 permits globally.
- Child internal facts carry `run_id`, `parent_run_id`, `subagent_child=true`, and `skip_parent_context=true`. A child approval fact projected to the parent wire uses the parent run as envelope owner and retains child identity in target payload fields.
- Child external effects have their own attempts and permits and obey PM-0028 independently of the parent subagent permit.

Live child lifecycle:

- The parent subagent effect is authorized once and its `EffectExecutor.execute` call remains active while the child is blocked or safe-yielded.
- A child `BlockedOutcome` or nonterminal safe yield does not settle the parent.
- The wrapper waits for targeted invalidator generation or all-command session-sequence wake and invokes a new child segment with the legal existing input. Approval allow does not increment `dispatch_generation`.
- Child `CompletedOutcome` settles the parent completed. Child `FailedOutcome`, `CancelledOutcome`, or `RoundLimitOutcome` settles the parent failed with a stable result.
- Parent cancel/interrupt signals the child, settles or reconciles every claimed child permit, waits for durable child-executor quiescence, and only then settles the parent permit.
- Approval denial preserves deny-and-continue: atomically commit `prepared -> rejected` and its disposition, refresh the cut, then continue the child with `ApprovalResolved`. The parent stays `DISPATCHED`.

Recovery uses a host-private `RecoveredChildExecutionLease`:

- The lease is available only after durable old-executor quiescence, to the current parent session owner, for the deterministic child bound to the retained `DISPATCHED` parent authorization.
- It is not a generic `EffectExecutor` permit and cannot authorize arbitrary tools.
- Authorization persists two watermarks: invalidating `dispatch_generation` as `authorization_mailbox_cut`, and all-command `admitted_session_seq` as `authorization_mailbox_session_seq`.
- Acquisition locks `session_fact_source`, records `resume_cut` plus `prior_session_seq`/`resume_session_seq`, increments a fenced `resume_generation`, and snapshots target-matched mailbox entries in the admitted-session range. Approval allow admitted at an unchanged generation therefore remains recoverable.
- Pending parent/child/global cancel or interrupt is processed before continuation. Pending denial is rejected and continues with a fresh lease/cut. Approval allow wakes separately, refreshes the all-command snapshot, and validates the waiting plan.
- A sibling invalidator-generation change fences the lease. Recovery compares newest generation to persisted `resume_cut`; the coordinator request cut is only the desired rebase cut. Exact persisted rebase replay reuses its generation and snapshot.
- Every child `PREPARED -> DISPATCHED` authorization uses `expected_mailbox_cut=resume_cut`.
- If control is observed after authorization but before executor entry, coordinator emits its existing indeterminate settlement. `commit_settlement` loads the exact attempt before constructing the UoW; only an `authorized_unclaimed` row causes it to attach a host-private closeout guard. The same UoW verifies/quiesces the attempt and commits `DISPATCHED -> UNKNOWN` plus receipt. Started execution follows normal cancellation/quiescence. Exact replay adopts the atomic receipt; a crash before commit leaves both unchanged.

Recovery terminal writes carry a distinct host-private `expected_recovery_cut` and lease identity. The field is fingerprinted and valid only for recovery child-terminal or retained-parent-settlement UoWs. Each such transaction locks the fact source and requires `dispatch_generation == expected_recovery_cut` before terminal evidence, parent settlement, or state/fact mutation. Stale refusal writes nothing.

The durable child binding stores the canonical live parent-settlement identity. Recovery queries its atomic receipt/final evidence before writing. If present, recovery adopts it. If absent, the current fenced lease uses a distinct recovery transition identity whose exact retry reuses the same lease identity and cut. Live and recovery mutation shapes never share a transition key.

### ADR-0077 child-fact projection supplement

New-runtime child facts are canonical session facts but are not automatically parent-chat facts:

- Child internal `EventRecord`s share the session sequence and remain queryable by child `run_id`.
- Connected-chat active-view, model-context, and wire projection exclude `subagent_child=true` or `skip_parent_context=true` records.
- `approval_requested` for a child projects to the parent UI with the child/effect target so the decision can be routed durably.
- The single parent subagent tool-result fact is the only child terminal output included in parent model context and connected-chat wire output.
- Projection membership is deterministic from durable event fields; it never depends on process-local child state.

### Phase F: version-fenced activation and projection

Phase F is the first serving gate for the new runtime.

Runtime versioning:

- Every session has immutable `runtime_version`.
- Existing sessions and additive migration backfills are `legacy`.
- Before activation, all newly created sessions are also `legacy`.
- After the durable activation flag flips, only new sessions receive the new runtime version. Child runs inherit the parent version.
- Unknown versions fail closed.
- Every store writer loads and fences the session version inside its transaction.

Activation is non-rolling:

1. Deploy code and schema with the new path disabled.
2. Quiesce and stop every old daemon; drain owner and executor leases.
3. Run additive migrations and backend parity checks.
4. Start only F-capable daemons and require capability reports.
5. Flip the durable new-session activation flag.

SQLite requires process restart across the barrier. PostgreSQL may have multiple new daemons after the barrier, but no old binary may remain live. Rollback disables new-session creation, drains new-runtime sessions, and restores legacy creation only after compatible daemons own the fleet. Runtime versions of existing sessions never change.

Legacy/new writer boundary:

- Legacy sessions keep `PipelineAdapter`, `RuntimeMessageBus`, legacy checkpoints, and UUID-era wire/fact behavior.
- A narrow legacy-only prepared-to-terminal writer remains version-fenced for existing legacy sessions.
- Generic rank replacement and the public `settled` alias are removed at F. New-runtime writers cannot use the legacy method or write `settled`.
- New-runtime sessions use only the typed graph, durable mailbox, standard coordinator, concrete ports, and committed facts.
- New-runtime checkpoint capture and restore reject before any mutation until Phase G.

Approval/UI projection:

- Committed facts are the only source. `CommittedFactNotice` is a wake hint.
- The projector is fenced by session owner/epoch and may run at least once.
- Each durable sink records an `event_id` receipt. Interaction/session projection and wire outbox writes are idempotent.
- The source cursor advances only after every required sink receipt commits.
- Takeover replays from the durable cursor; duplicate delivery creates no duplicate interaction or wire fact.
- UI reads durable interaction state. Wire delivery deduplicates by `event_id`.

Phases G and H remain as defined by ADR-0083: G introduces the new checkpoint/restore contract; H removes the legacy pipeline only after migration gates prove no legacy runtime/checkpoint remains.

## Implementation Plan

1. **D3b**: implement only `expected_mailbox_cut`, fingerprinting, SQLite/PostgreSQL locked comparison, host-private errors, and direct-store tests. No production caller.
2. **D4**: implement retained-plan unknown settlement, evidence-backed reconciliation, exact replay adoption, and takeover/quiescence tests.
3. **E**: implement non-serving concrete ports, target-aware probe, authorization stale loop, parent/child wrappers, recovered-child lease, and child projection behavior on both backends.
4. **F**: implement immutable runtime version, non-rolling activation barrier, version-fenced writers, new approval projector/outbox, checkpoint rejection, coordinator routing, and legacy alias removal. Run the complete coexistence and PM-0028 gate before activation.
5. **G/H**: continue the retained ADR-0083 plan.

Each phase uses a separate task packet and pull request. No phase may serve new-runtime sessions before every earlier phase has merged and F's activation gate passes.

## Alternatives Rejected

- **Execute ADR-0084 D3b literally**: rejected because it would remove or bypass the live legacy writer before immutable session versioning exists.
- **Activate coordinator routing in D3b without D4**: rejected because indeterminate dispatch would lose its plan or final-fact boundary before reconciliation exists.
- **Use effect-specific dispatch generations**: rejected because D3a deliberately established one session fact-source cut. Target-aware probes and stale rebasing preserve that authority without a second counter.
- **Add `Continue` or another engine input**: rejected because the prepared authorization can retry inside the coordinator without re-running the engine, and the existing settlement inputs cover child continuation.
- **Store mailbox cut in `EffectMutation.payload`**: rejected because it is an authorization precondition, not effect data, and must participate in receipt identity independently of effect payload.
- **Let recovery reuse a live settlement transition key with a different shape**: rejected because lost-ack replay would produce a fingerprint conflict.
- **Publish both a child summary and parent tool-result fact**: rejected because it duplicates canonical model-visible output.
- **Use rolling mixed-binary activation**: rejected because old writers cannot enforce new runtime-version and projection contracts.
- **Move children to independent workers in E**: rejected because owner-local execution remains the accepted safety boundary.

## Acceptance Criteria

### D3b

- `test_dispatch_authorization_requires_mailbox_cut`
- `test_mailbox_cut_is_forbidden_outside_dispatch_authorization`
- `test_dispatch_authorization_cut_changes_mutation_fingerprint`
- `test_dispatch_authorization_exact_replay_precedes_newer_cut_sqlite`
- `test_dispatch_authorization_exact_replay_precedes_newer_cut_postgresql`
- `test_stale_dispatch_authorization_writes_nothing_sqlite`
- `test_stale_dispatch_authorization_writes_nothing_postgresql`
- `test_stale_dispatch_authorization_writes_no_receipt`
- `test_fresh_cut_can_commit_after_stale_zero_write_refusal`
- Production runtime behavior remains on the legacy path.

### D4

- `test_indeterminate_settlement_keeps_pending_plan_and_commits_no_final_tool_fact`
- `test_reconciliation_reenters_with_existing_effect_settled_input`
- `test_reconciliation_exact_replay_is_adopted`
- `test_reconciliation_rejects_wrong_attempt_authorization_or_epoch`
- `test_takeover_waits_for_old_executor_quiescence`
- `test_unknown_effect_is_never_automatically_retried`

### E

- SQLite and PostgreSQL concrete ports pass the same frozen-contract suite.
- `test_unrelated_stale_cut_retries_same_prepared_authorization`
- `test_targeted_stale_cut_safe_yields_without_dispatch`
- `test_approval_denial_disposition_clears_probe_and_continues_child`
- `test_sibling_denial_rebases_without_indeterminate_child_settlement`
- `test_child_blocked_keeps_parent_execute_and_permit_live`
- `test_child_cancel_settles_child_permits_before_parent`
- `test_recovered_child_lease_rejects_pending_parent_cancel`
- `test_recovered_child_denial_rejects_then_continues_with_fresh_cut`
- `test_recovery_terminal_cut_stale_refusal_writes_nothing`
- `test_live_settlement_receipt_is_adopted_before_recovery_write`
- `test_new_runtime_child_terminal_output_is_one_parent_tool_result_fact`
- `test_legacy_child_terminal_output_uses_runtime_message_bus`
- `test_child_internal_facts_are_excluded_from_parent_context_and_wire`

### F

- Existing and migrated sessions remain `legacy`; new sessions switch versions only after the durable activation flag.
- Unknown versions and cross-version writes fail before mutation on SQLite and PostgreSQL.
- No old daemon or executor lease remains at activation.
- Legacy sessions preserve legacy wire, child summary, and checkpoint behavior.
- New-runtime sessions cannot call the legacy terminal writer or write `settled`.
- Projector crash/takeover replay creates no duplicate interaction, fact, or wire event.
- Checkpoint capture and restore reject before mutation for new-runtime sessions.
- Before activation, root startup recovery proves authorization-commit/process-crash recovery from durable attempts without any process-local marker.
- SQLite/PostgreSQL parity covers approval, child wait/cancel/recovery, reconciliation, projector receipts, activation rollback, and coexistence.
- PM-0028 focused regression passes:

  ```bash
  uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or executor_exception or executor_task_cancellation" -q
  ```

- Relevant AgentKit, coding-agent, CLI, and migration suites pass before activation.

## References

- `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
- `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0076-harness-control-plane.md`
- `docs/adr/0077-connected-chat-session-event-projection.md`
- `docs/adr/0075-checkpoint-restore-active-run-timeline.md`
- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`
- `docs/adr/0084-stage-phase-d-capability-inputs-and-recovery-cutovers.md`
- `postmortem/patterns/PM-0028-settle-post-dispatch-outcomes-before-exit.md`
- `src/agentkit/runtime/contracts.py`
- `src/agentkit/runtime/coordinator.py`
- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/pg_durable/uow.py`
