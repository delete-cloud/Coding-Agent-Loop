# Task Packet

packet_id: tp-2026-09-01-adr-0085-phase-e-host-ports
packet_revision: 22
role: implementer
baseline_ref: origin/main
baseline_sha: facf2934274aad671a8545a2035b887ebd406454
branch: feat/adr-0085-phase-e-host-ports

## Goal

Implement ADR-0085 Phase E as a non-serving host boundary: concrete SQLite and PostgreSQL implementations of the frozen AgentKit `CommitPort`, durable local/remote `EffectExecutor` adapters, and owner-local parent/child execution and recovery with targeted mailbox fencing.

## Scope

### Phase boundary

- Keep every Phase C AgentKit request, proposal, result, outcome, port signature, and export unchanged.
- Keep the serving `RuntimeTurnService`, legacy `PipelineAdapter`, checkpoint path, approval path, and `RuntimeMessageBus` routing unchanged. Phase E may expose construction helpers in `turn_execution.py`, but no session selects the new runtime before Phase F.
- Preserve the D4 rule that every claimed permit receives a completed, failed, or indeterminate settlement attempt before its coordinator exits. Child cancellation waits for child executor quiescence and child permit settlement before the parent subagent permit settles.
- Do not remove the legacy `settled` effect alias or generic rank replacement in E; Phase F removes them after the version-fenced writer cutover.

### Review remediation contract

- Canonical child identity includes all durable tuple members: `child_run_id = "{session_id}:{parent_run_id}:child:{parent_effect_id}:{parent_attempt_id}"`. The binding's live parent-settlement transition ID is exactly the frozen engine transition formula applied to the parent run and `EffectSettled.input_id = "{authorization_transition_id}:settlement:{parent_attempt_id}"`; no custom `:parent-settlement` key remains.
- Recovery compares durable newest invalidator generation with persisted `resume_cut`, never the candidate request cut. On every stale classification, the recovery-bound CommitPort loads the latest locked mailbox batch and both watermarks, publishes them into the exact cached `TargetAwareChildControlProbe` it supplied to the coordinator, and only then returns stale. Synchronous `observe()` reads that cache. The first stale only refreshes it. On retry, sibling-only advancement rebases and returns stale; the next retry sees equality and authorizes. Matched invalidators leave the lease unchanged. Locked classification prevents a command racing after cache publication from being rebased past. Exact rebase replay reuses generation/snapshot.
- Add a host-private `terminal_action` bit to the typed UoW. Snapshot and fingerprint it. `child_terminal` requires bound child state, `terminal_action=true`, and no mutation of the bound parent effect. `parent_settlement` requires bound parent state, `terminal_action=false`, and exactly one total effect mutation: the terminal bound parent-effect mutation. Reject every extra mutation.
- The claimed-permit critical section begins at executor entry. Add `quiesce_claimed_executor_attempt(...)` with complete record/replay/conflict semantics. Cancellation during pre-backend await finishes/reloads the write, starts cleanup in a dedicated task, and repeatedly awaits it through `asyncio.shield`; repeated cancellation cannot interrupt cleanup. `authorized_unclaimed` becomes generation-one quiescent; reserved/started retain generation/reservation. Separately, post-authorization control before executor entry is owned by `commit_settlement`: a host-private `UnstartedDispatchCloseoutGuard` is snapshotted/fingerprinted and valid only for the matching `DISPATCHED -> UNKNOWN` settlement. Its UoW atomically verifies/quiesces `authorized_unclaimed`, settles the effect/state/facts, and writes the receipt before returning.
- Add non-serving `DurableSegmentRunner.run(request)` around every constructed new-runtime root/child coordinator call. It loops until a non-marker outcome. Concrete CommitPorts retain a typed process-local `AuthorizationReplayMarker` keyed by run/transition before returning exact replay. On the matching frozen failure, the runner consumes it, reloads committed operation/effect/attempt state, resolves D4 evidence to one exact `EffectSettled`, rebuilds `RunSegmentRequest` with the current committed state version and durable cut, and re-enters the same coordinator. It never returns `exact_replay_requires_recovery` to root/child callers. A process crash discards the marker; E child lease reconstructs from durable state; F owns root crash recovery before activation.
- Approval denial uses one authoritative UoW and stable `ApprovalResolved.input_id = "{child_run_id}:approval:{command_id}"`; the resolver returns original state plus exact input. Live denial re-enters at refreshed cut; recovered denial precommits then acquires a fresh lease. Approval allow never uses invalidator rebase: under lock the owner validates waiting request/effect, refreshes `resume_session_seq` and the snapshot at unchanged `resume_cut`, increments lease generation once, then enters `ApprovalResolved`; equality authorization atomically consumes the sole proposal disposition. Exact snapshot-refresh replay reuses generation/bytes. Malformed/stale approval is dispositioned rejected and waits.
- Map `CancelledOutcome` to one stable failed parent result. Replace every acceptance-test alias with an independent scenario that asserts its named behavior.
- Integrate child projection predicates into the real connected-chat projector and schema. Child internal facts are dropped from parent wire; targeted child `approval_requested` is the sole child-internal exception and carries child/effect target identity. Parent model-context projection calls the context predicate. The legacy publisher remains unchanged and is tested through `RuntimeMessageBus.SUBAGENT_MESSAGE`.
- Persist both authorization watermarks on the executor-attempt row: `authorization_mailbox_cut` for invalidating `dispatch_generation` and `authorization_mailbox_session_seq` for all commands. New-runtime cancel/interrupt commands carry exactly one target form: nonempty `target_run_id` or `target_scope: "global"`. Approval denial may use either; approval success requires `target_run_id`. Missing, malformed, or simultaneous target forms fail closed before mailbox admission. A recovery lease stores `prior_session_seq` and `resume_session_seq` and snapshots every pending/admitted matched mailbox entry with `prior_session_seq < admitted_session_seq <= resume_session_seq`; it never ranges by dispatch generation. Each entry retains both admitted sequence values, command ID/kind, target form, request/effect metadata, and approval decision. Under the same lock, validate approvals against the waiting plan. Matched malformed/stale approvals are rejected, never sibling traffic.
- Persist every issued recovery lease in a `session_recovery_leases` ledger keyed by `(session_id, lease_id)` with child ID, generation, cut, mailbox snapshot, owner epoch, and status. Same-child exact acquisition replays the ledger row; reuse by another child conflicts forever, including after supersession. SQLite's writer transaction and PostgreSQL's locked fact-source row serialize ledger insertion and active-binding replacement.
- Parent settlement uses one symmetric backend algorithm under the fact-source/binding lock. Derive the recovery ID exactly as `"{live_parent_settlement_transition_id}:recovery:{lease_id}:{resume_generation}:{resume_cut}"`. Load the current-ID, canonical-live, and active-lease-recovery receipts before returning any: both live and recovery receipts means corruption; an exact current receipt is adopted; exactly one cross-writer receipt is adopted; a terminal parent effect without either receipt is corruption; otherwise validate live CAS or the recovery guard and commit once under that writer's distinct ID. Ordinary non-parent exact receipts are adopted before writes. On a fresh transition validate state CAS, mailbox/recovery guards, child-binding conflicts, every ordered effect precondition, and dispatch/reconciliation preconditions before the first write.
- Exercise `PostgreSQLCommitPort` against `PGDurableStore` in the shared fake-PostgreSQL contract harness, not an SQLite store behind the PostgreSQL class name. The port, ordered-plan, recovery rebase/guard, denial, and projection matrices run through both concrete backend implementations.
- The live/recovery proof runs both settlement writers concurrently from the same pre-settlement state, asserts exactly one terminal parent mutation, then exact-retries both writer shapes and asserts both adopt the same winning receipt. A deterministic precommitted-live case proves adoption precedes stale recovery-guard validation; a deterministic precommitted-recovery case proves live retry adopts the recovery receipt instead of reporting terminal-without-live-receipt corruption.

### Concrete commit ports

- Add concrete `SQLiteCommitPort` and `PostgreSQLCommitPort` classes in `coding_agent`. They share one conversion implementation and differ only in the concrete durable-store binding. Both implement the frozen four-method `CommitPort` protocol.
- Convert each request into one `OwnerAuthority` and one typed `AuthoritativeUnitOfWork`. The store remains the only allocator of `session_seq`, projection epoch, committed state revision, receipt, and committed fact notices.
- Convert every `PendingFact` into an `EventRecord` with the same stable `fact_id`/`fact_kind`; inject a clock for `created_at`. Return committed notices and receipts from the authoritative store response, never from the proposal.
- Map exact receipt replay to `ExactReplayCommitResult`; map state CAS, stale owner, stale mailbox cut, invalid transition, and storage failures to their frozen typed result variants. A CAS conflict reloads the current operation state. Error mapping uses a nonblank exception fallback.
- `authorize_dispatch` commits with both watermarks; only fresh commit issues a permit. Exact replay publishes the process-local marker. Runner recovery maps terminal evidence to exact completed/failed/indeterminate settlement; `authorized_unclaimed` to guarded atomic indeterminate settlement; reserved/started waits for quiescence and reconciles, defaulting indeterminate when execution cannot be excluded. It then re-enters coordinator with `EffectSettled` and returns only the eventual normal outcome. Recovered child does the same without marker after lease acquisition. Root in-process replay is E-tested; root process-crash recovery is F-gated.
- The opaque permit token is random host data and is never persisted as authority. The durable authorization transition, attempt row, owner epoch, and effect identity are the authority checked by the executor.
- `commit_reconciliation` passes the retained authorization and durable evidence preconditions through the existing D4 UoW fields.

### Ordered effect mutations

- A model completion may prepare multiple ordered `EffectPlan` values. Extend the host-private typed UoW to carry an ordered `effect_mutations` tuple so all newly prepared plans commit in the same state/fact transaction. Keep the singular legacy `effect_mutation` input only while legacy writers remain in E; reject simultaneous singular and plural use and normalize typed-transition logic to one ordered tuple.
- Fingerprint every ordered mutation. Reject duplicate effect IDs in one UoW. Apply all effect precondition checks before any write, then write all mutations and any dispatch executor-attempt row atomically on SQLite and PostgreSQL.
- `snapshot_transition_unit` explicitly reconstructs ordered mutations, `terminal_action`, every recovery-guard field, and every `UnstartedDispatchCloseoutGuard` field. Both backends snapshot before fingerprint/write. No field is outside the snapshot.
- Every typed-UoW predicate uses normalized mutations. The fingerprint serializes ordered mutations, `terminal_action`, complete recovery guard, and complete unstarted closeout guard.
- `commit_transition` prepares every new plan in `proposal.effect_plans`. A settlement UoW settles the current effect and prepares any newly exposed next plan in the same transaction. Authorization remains exactly one `PREPARED -> DISPATCHED` mutation.
- Transition receipts persist and restore the ordered `EffectPlan` values so an exact retry or child recovery can reconstruct the prepared action without process-local state.

### Durable effect execution

- Add a `DurableEffectExecutor` implementation of the frozen `EffectExecutor` protocol. It owns no durable authority beyond the supplied claimed permit.
- Before underlying execution it loads and validates the exact dispatched effect slot, reserves the D4 executor-attempt row with a bounded lease, then commits `reserved -> started` under the live owner. No local or remote effect starts before that durable `started` row exists.
- The executor receives a backend implementing one narrow effect-runner protocol. Provide local and remote backend adapters over existing host tool/external-executor abstractions; do not add a second tool registry or duplicate tool validation.
- After `DispatchPermit.claim()`, an ordinary backend exception maps to `EffectIndeterminateResult`; execution may have occurred. `asyncio.CancelledError` is different control flow: the executor records durable quiescence, then re-raises it so `SegmentCoordinator` commits the indeterminate settlement and only then propagates cancellation. A returned domain failure may map to `EffectFailedResult`. Every failure message is nonblank.
- The parent subagent backend is an owner-local child execution wrapper. Child tool effects use their own independent `DurableEffectExecutor`, attempt rows, permits, and D4 settlement path.

### Durable child identity and facts

- Derive `child_run_id` deterministically from the durable tuple `(session_id, parent_run_id, parent_effect_id, parent_attempt_id)`. The derivation is stable across processes and backends.
- When a subagent plan enters `PREPARED`, store a child binding atomically with the effect preparation. The binding records parent run/effect/attempt, deterministic child run, canonical live parent-settlement transition identity, and both authorization watermarks. A different binding for the same parent authorization conflicts.
- At preparation, derive a stable subagent authorization transition identity from the prepared transition, parent effect, and attempt. Both immediate dispatch and an approval-gated `ApprovalResolved` dispatch reuse that exact identity. Derive the canonical live parent-settlement transition identity with the frozen engine formula from the parent run and the stable `EffectSettled.input_id`; store it in the binding, and require the live settlement commit/receipt to use that exact key. No frozen request field changes.
- Parent and child use the same `session_id`, owner ID/epoch, fact source, and session sequence. Child operation state remains keyed by `child_run_id`.
- Child-internal facts carry durable `run_id=child_run_id`, `parent_run_id`, `subagent_child=true`, and `skip_parent_context=true`, derived from the locked binding. The sole wire exception is child `approval_requested`: its envelope `run_id` is the bound parent run so the existing projector validates parent run ownership, while payload `target_run_id` and `target_parent_effect_id` retain child identity. No child `AgentRunRecord` is required.
- Child `approval_requested` facts carry the exact child/effect target required to admit the run-targeted approval decision.
- Child targeting uses `RuntimeCommand.payload.target_run_id`. Explicit global targeting uses `target_scope: "global"`, mutually exclusive with `target_run_id`. Parent/child/global cancel, interrupt, and denial raise the child probe; sibling run commands do not. Approved decisions are never global.

### Internal authorization stale loop

- Change `SegmentCoordinator.run` internally, without changing its signature or exports. On `StaleMailboxCutCommitResult`, observe the target-aware probe. If it is raised for this run, return the safe yield carrying `snapshot.reason` with no permit. Otherwise set the candidate cut to `max(result.current_mailbox_cut, snapshot.generation.value)`, rebuild the same `DispatchAuthorizationRequest` from the retained engine request, proposal, effect plan, and mutation, and retry inside the same coordinator call.
- A second or later stale result repeats the same cancellable loop. The loop never calls `AgentEngine.propose`, never replays a consumed `EngineStepInput`, never prepares a second effect, and never falls through to `_authorized_result`.
- The retry changes only `DispatchAuthorizationRequest.mailbox_cut` and the host UoW `expected_mailbox_cut`. Remove `mailbox_cut` from `EffectMutation.payload`; the authorization transition identity and mutation shape remain stable across retries.
- Recovery invalidator stale handling is separate from approval allow. First stale refreshes; retry compares newest invalidator generation to persisted `resume_cut`; sibling-only greater rebases and returns stale, matched invalidators leave unchanged, equality may authorize.
- Approval allow runs a host-private all-command snapshot refresh before coordinator entry. It leaves `resume_cut` unchanged, advances `resume_session_seq`, increments `resume_generation` once, and exact-replays without another increment. The subsequent `ApprovalResolved` proposal carries the sole consumable disposition and authorizes at cut equality in one UoW.
- The owner-local child wrapper supplies the target-aware `ControlProbe` and waits only after the coordinator returns a real blocked/nonterminal outcome. It does not retry a stale authorization by starting another segment.

### Live child lifecycle

- The parent subagent `EffectExecutor.execute` call stays active while the child is blocked or at a nonterminal safe yield. It does not return a parent settlement for either state.
- The wrapper waits for either all-command `admitted_session_seq` advancement or control-generation change, obtains the admitted command batch and both watermarks under store authority, and resumes through an existing legal input. Approval allows wake without incrementing `dispatch_generation`.
- When a target-raised safe yield or all-command wake returns, the wrapper inspects the already-observed parent/child/global batch immediately. It terminates for cancel/interrupt, precommits denial, enters exact approval allow, or rejects malformed/stale approval and keeps waiting. It never waits for another change to process the triggering command.
- A stale authorization caused by an unrelated sibling command keeps the same already-prepared effect and deterministic authorization identity. The coordinator rebases and retries inside the same segment; it does not prepare a second effect or settle indeterminate.
- A matched invalidator before authorization safe-yields without permit. After authorization but before executor entry, coordinator emits its existing indeterminate settlement. `commit_settlement` loads the attempt; only `authorized_unclaimed` attaches the closeout guard, whose UoW atomically quiesces it and commits `DISPATCHED -> UNKNOWN`. The driver does not interpose. Started execution uses normal cancellation/quiescence.
- Approval denial, whether run-targeted or global, atomically commits the denial disposition, `PREPARED -> REJECTED`, and one denial tool-result fact, then resumes the child through `ApprovalResolved` with a fresh cut. The parent subagent effect remains `DISPATCHED`.
- Child `CompletedOutcome` returns one successful parent subagent effect result. Child failed, cancelled, or round-limit outcomes return one stable failed parent effect result. Parent cancellation signals the child and waits for all child permits and executors to settle/quiesce before the outer durable executor returns.

### Recovered child execution lease

- Add host-private durable child binding/lease records and concrete SQLite/PostgreSQL store operations. `RecoveredChildExecutionLease` is a coding-agent host type, not an AgentKit permit and not accepted by general effect runners.
- Acquire a recovery lease only for the current owner, deterministic bound child, retained `DISPATCHED` parent authorization, and old parent executor attempt with durable quiescence. Under the fact-source/binding lock, snapshot matched commands by admitted session sequence, record both prior/resume watermarks, increment `resume_generation`, and persist the unique lease.
- The recovery-bound CommitPort owns the active lease. Authorization classification uses persisted `resume_cut`; the coordinator request cut is only a desired rebase cut. All-command snapshot refresh may advance `resume_session_seq` while `resume_cut` stays equal. A stale lease or concurrent rebase fails closed.
- Pending parent/child/global cancel or interrupt prevents acquisition/continuation. Pending denial is precommitted first. Approval allow refreshes the lease snapshot and validates the exact waiting plan before coordinator entry.
- Exact rebase replay with identical lease/desired watermarks/snapshot reuses generation/bytes. Sibling invalidator advancement rebases and returns stale. Approval all-command snapshot refresh is a distinct operation at unchanged `resume_cut`; exact refresh replay is also generation-stable.
- If authorization is stale, no permit exists. Matched control after authorization follows the explicit unstarted or started quiescence-and-indeterminate closeout above. Sibling-only admission leaves the probe unraised; a committed permit may execute once.

### Recovery terminal cut and settlement identity

- Extend the host-private UoW with a typed recovery guard containing `lease_id`, `child_run_id`, `resume_generation`, `expected_recovery_cut`, and kind `child_terminal` or `parent_settlement`. The guard is valid only for those recovery writes, is snapshotted and fingerprinted, and is absent from live writes.
- For guarded recovery writes, exact receipt replay is adopted first. Otherwise lock fact source/binding and require active lease/generation plus `dispatch_generation == expected_recovery_cut`. For unstarted closeout, exact receipt replay is also first; otherwise require the exact authority/effect/attempt/authorization identity and `authorized_unclaimed`, then quiesce the attempt and commit the `DISPATCHED -> UNKNOWN` settlement in the same transaction. Any stale/mismatched guard writes nothing.
- The child binding stores the canonical live parent-settlement transition identity. Recovery checks that transition receipt/final evidence first. If present, it adopts it. If absent, recovery uses a distinct transition identity derived from the durable recovery lease. Exact retries reuse that lease identity and cut. Live and recovery settlement shapes never share a transition key.

### Child projection supplement

- Add deterministic projection predicates based only on durable event payload. A production `build_parent_model_context_facts` helper filters every event with `subagent_child=true` or `skip_parent_context=true`; the non-serving Phase E turn-construction helper calls it, but no serving session selects that construction before Phase F.
- Adding `approval_requested` is an explicit additive serving-wire compatibility change in E: ADR-0077 contract `1.1.0`, fixture `2026-08-24.r3`. Existing event meanings stay unchanged. Payload always requires `approval_request_id`, `tool_call_id`, `tool_name`, object `arguments`, `effect_id`, `attempt_id`; child payload additionally requires nonempty `target_run_id` and `target_parent_effect_id`. Add one parent-owned child-approval fixture event with stable source ID, unused decimal `session_seq` within high water, parent `run_id`, and matching child targets. Update backend constants/types/schema/OpenAPI examples and byte-identical fixtures. Frontend parser/types, `timelineToMessages`, component, and locales show a noninteractive `approval` message retaining all identities. Existing `/approve` behavior is unchanged.
- Preserve the legacy subagent summary publisher and `RuntimeMessageBus.SUBAGENT_MESSAGE` behavior for legacy sessions. The new construction never calls that publisher.

## Expected production files

- `src/agentkit/runtime/coordinator.py`
- `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`
- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/runtime.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/local_durable/fact_source.py`
- `src/coding_agent/stores/local_durable/core.py`
- `src/coding_agent/stores/pg_durable/uow.py`
- `src/coding_agent/stores/pg_durable/fact_source.py`
- `src/coding_agent/stores/pg_durable/sql_harness.py`
- required SQLite/PostgreSQL schema and row-codec modules
- `src/coding_agent/stores/durable_commit_port.py` (new)
- `src/coding_agent/executors/durable.py` (new)
- `src/coding_agent/runs/child_execution.py` (new)
- `src/coding_agent/runs/turn_execution.py`
- `src/coding_agent/events/connected_chat.py` or one focused child-projection module
- `tests/agentkit/runtime/test_coordinator.py`
- `tests/fixtures/connected_chat/v1/connected-chat-contract.json`
- `webui/app-next/test/fixtures/connected-chat/v1/connected-chat-contract.json`
- `webui/app-next/src/lib/connected-chat/` parser, event types, and reducer files selected by existing symbols
- `webui/app-next/src/hooks/use-connected-chat.ts`
- `webui/app-next/src/components/business/timeline.tsx`
- connected-chat locale message files used by the timeline component
- corresponding backend OpenAPI examples and frontend fixture/parser/timeline tests
- `src/coding_agent/server/schemas.py`
- `src/coding_agent/server/http/events.py`
- backend connected-chat contract/OpenAPI/HTTP tests and frontend wire/timeline/component tests
- corresponding package exports

## Authority

- `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`, Phase E and E acceptance criteria.
- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`, Phase E host-port boundary.
- ADR-0068 owner fencing, ADR-0076 authoritative UoW/fact-source authority, and ADR-0077 connected-chat projection.
- `postmortem/patterns/PM-0028-settle-post-dispatch-outcomes-before-exit.md`.
- `postmortem/patterns/PM-0026-never-persist-blank-exception-messages-as-run-errors.md`.

## Non-goals

- No serving route, runtime-version selection, activation flag, projector worker, sink cursor, or checkpoint behavior change.
- No Phase F removal of legacy `PipelineAdapter`, legacy `RuntimeMessageBus`, UUID compatibility fact writer, `settled` alias, or rank replacement.
- No generic permit reconstruction, generic retry from `UNKNOWN`, automatic external-effect retry, or recovery lease usable by non-child tools.
- No new AgentKit public input, output, port, method signature, or export.
- No process-local child identity, mailbox authority, or projection-membership decision.

## Acceptance criteria
- `test_executor_cancellation_during_reservation_quiesces_claim`
- `test_executor_cancellation_during_effect_load_quiesces_unclaimed_attempt`
- `test_executor_cancellation_during_attempt_load_quiesces_unclaimed_attempt`
- `test_executor_cancellation_during_start_quiesces_claim`
- `test_child_terminal_guard_rejects_nonterminal_transition`
- `test_recovery_lease_persists_authorization_cut_mailbox_snapshot`
- `test_recovery_lease_id_collision_across_children_conflicts`
- `test_exact_receipt_precedes_fresh_transition_writes`
- `test_effect_preconditions_precede_first_transition_write`
- `test_snapshot_preserves_every_recovery_field_explicitly`
- `test_child_identity_uses_full_durable_tuple_and_frozen_settlement_formula`
- `test_first_recovery_stale_refreshes_probe_without_rebasing_lease`
- `test_recovery_retry_rebases_only_after_unraised_refreshed_probe`
- `test_parent_settlement_guard_rejects_terminal_action`
- `test_parent_settlement_guard_rejects_wrong_run`
- `test_parent_settlement_guard_requires_exactly_one_terminal_effect_mutation`
- `test_precommitted_denial_reentry_exact_replays_then_continues_once`
- `test_targeted_command_through_stale_cut_refreshes_probe_before_retry`
- `test_concurrent_live_recovery_parent_settlement_commits_once`
- `test_live_retry_adopts_precommitted_recovery_receipt`
- `test_recovery_rebase_newest_above_request_returns_stale_without_authorization`
- `test_recovery_parent_settlement_id_includes_lease_generation_and_cut`
- `test_recovery_lease_ledger_prevents_superseded_id_reuse`
- `test_recovery_snapshot_keeps_targeted_child_approval_and_validates_waiting_plan`
- `test_targeted_stale_approval_safe_yields_without_rebase`
- `test_connected_chat_projector_filters_child_internals_and_keeps_targeted_approval`
- `test_parent_model_context_builder_filters_child_facts`
- `test_new_runtime_command_admission_rejects_missing_malformed_or_dual_targeting`
- `test_global_cancel_interrupt_and_denial_fence_live_child_on_sqlite_and_pg`
- `test_global_cancel_interrupt_and_denial_fence_recovered_child_on_sqlite_and_pg`
- `test_approval_requested_payload_requires_root_fields_and_child_targets`
- `timelineToMessages` and timeline component tests prove the visible noninteractive approval message retains request/effect/attempt/target identities.
- `test_recovery_snapshot_uses_admitted_session_seq_and_keeps_allow_at_prior_generation`
- `test_approval_allow_wakes_without_dispatch_generation_change`
- `test_exact_rebase_replay_reuses_generation_and_snapshot`
- `test_authorization_lost_ack_quiesces_and_settles_indeterminate`
- `test_post_authorization_control_quiesces_unstarted_attempt_and_settles_unknown`
- `test_stale_port_publishes_locked_batch_before_sync_probe_observe`
- `test_control_racing_after_probe_publish_cannot_rebase_past_target`
- `test_unstarted_closeout_guard_is_snapshotted_fingerprinted_and_atomic`
- `test_root_and_child_authorization_replay_use_durable_segment_runner`
- `test_authorization_replay_marker_is_process_local_and_consumed_by_matching_runner`
- `test_recovered_child_reconstructs_authorization_crash_from_durable_attempt_without_marker`
- `test_replay_runner_reenters_with_effect_settled_and_returns_eventual_child_outcome`
- `test_replay_runner_reenters_with_effect_settled_and_returns_eventual_root_outcome`
- `test_child_approval_projects_under_parent_run_without_child_run_record`
- `test_recovery_rebase_newest_below_request_fails_closed`
- `test_recovery_rebase_targeted_denial_leaves_lease_unchanged`
- `test_recovery_approval_refreshes_snapshot_then_authorizes_at_same_cut`
- `test_quiesce_unclaimed_attempt_persists_complete_record`
- `test_quiesce_claimed_attempt_replay_and_conflicts`
- `test_connected_chat_contract_includes_targeted_approval_requested`
- `test_child_terminal_guard_rejects_parent_effect_mutation`
- `test_parent_settlement_guard_rejects_additional_mutation`
- `test_quiescence_cleanup_survives_repeated_task_cancellation`
- Cancellation/quiescence transition tests run through both `SQLiteLocalDurableStore` and `PGDurableStore`, not only a recording store.
- Backend OpenAPI/HTTP tests validate `1.1.0`, the approval payload schema, SSE event examples, and existing response/error shapes.
- Frontend: `pnpm --dir webui/app-next test`, `pnpm --dir webui/app-next typecheck`, `pnpm --dir webui/app-next verify:i18n`, and `pnpm --dir webui/app-next build`.
- Recovery lease acquisition/rebase tests run through both stores and assert `prior_session_seq` exclusivity, `resume_session_seq` inclusivity, approval allow retained at unchanged dispatch generation, disposed/out-of-range exclusion, stable admitted-session ordering, complete command/target/approval fields, exact rebase replay, and atomic publication of both watermarks.
- Concurrent live/recovery settlement and both deterministic adoption directions run through both concrete stores.
- Instrumented SQLite/PG pre-write matrices cover state CAS, mailbox cut, recovery guard, child binding, every effect mutation, dispatch attempt, and reconciliation preconditions before the first create/update.
- Connected-chat integration tests call `project_chat_event`, both active-view scans, `_publish_chat_commit`, and the parent model-context builder; predicate-only tests are insufficient.
- `test_fixture_covers_complete_connected_chat_contract` proves the `1.1.0` backend fixture contains targeted `approval_requested`.
- Frontend fixture/parser/timeline tests parse and reduce that event without a contract violation.
- `cmp` proves the backend and frontend fixtures byte-identical; `jq empty` and canonical cursor round trips remain green.
- The exact packet-name coordinator tests construct their own coordinator scenarios or call a non-test scenario builder; they never invoke another `test_*` function.
- `test_sibling_denial_rebases_without_indeterminate_child_settlement` constructs a real denial race, asserts no indeterminate settlement, and runs through both durable backends; it never aliases the sibling-stale test.

- SQLite and PostgreSQL concrete ports pass the same frozen-contract suite.
- `test_unrelated_stale_cut_retries_same_prepared_authorization`
- `test_targeted_stale_cut_safe_yields_without_dispatch`
- `test_approval_denial_disposition_clears_probe_and_continues_child`
- `test_sibling_denial_rebases_without_indeterminate_child_settlement`
- `test_child_blocked_keeps_parent_execute_and_permit_live`
- `test_approval_gated_parent_subagent_reuses_prepared_settlement_identity`
- `test_child_cancel_settles_child_permits_before_parent`
- `test_recovered_child_lease_rejects_pending_parent_cancel`
- `test_recovered_child_denial_rejects_then_continues_with_fresh_cut`
- `test_recovery_terminal_cut_stale_refusal_writes_nothing`
- `test_live_settlement_receipt_is_adopted_before_recovery_write`
- `test_new_runtime_child_terminal_output_is_one_parent_tool_result_fact`
- `test_legacy_child_terminal_output_uses_runtime_message_bus`
- `test_child_internal_facts_are_excluded_from_parent_context_and_wire`
- Multi-plan preparation commits every effect atomically on both backends.
- Child binding creation, lease acquisition/rebase, targeted-control rejection, and recovery-guard stale-zero-write behavior pass one shared contract matrix against both SQLite and PostgreSQL.
- Run the focused frontend connected-chat fixture/parser/timeline tests and the existing frontend typecheck/build command selected by package scripts.
- `test_recovered_child_sibling_stale_rebases_lease_before_authorization` passes against both SQLite and PostgreSQL and proves the authorization cut, active lease cut/generation, and later recovery guard remain identical.
- Exact transition receipts restore ordered effect plans.
- No production caller selects the Phase E path.
- Frozen AgentKit signatures and exports remain unchanged.

## Target tests

- `uv run pytest tests/coding_agent/test_durable_commit_ports.py -q`
- `uv run pytest tests/coding_agent/test_durable_effect_executor.py -q`
- `uv run pytest tests/coding_agent/test_owner_local_child_execution.py -q`
- `uv run pytest tests/coding_agent/test_child_fact_projection.py -q`
- `uv run pytest tests/coding_agent/test_runtime_phase_b_uow.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py -q`
- `uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or crash_retains_run or executor_task_cancellation or stale_mailbox_cut" -q`
- `uv run pytest tests/agentkit/ tests/coding_agent/ -q`
- `uv run ruff check` and `uv run ruff format --check` for every changed Python file.

## Loop policy

- Write each behavioral test first and observe the focused failure.
- Implement the smallest change that satisfies the accepted Phase E contract.
- Run the exact target tests.
- Obtain PASS from GPT-5.6 Sol medium, Grok 4.6 high, and Kimi K3 high before implementation.
- After implementation, obtain PASS from the same three reviewers. Any FAIL requires a fix and a complete three-review rerun.

## Stop conditions

- Stop and revise ADR-0085 if Phase E requires a fifth `EngineStepInput`, a changed frozen AgentKit signature/export, or serving activation before F.
- Stop if multiple prepared effects cannot commit in one authoritative transaction on both backends.
- Stop if recovered child execution would require minting a generic `DispatchPermit` without a fresh `PREPARED -> DISPATCHED` authorization.
- Stop if a claimed child permit can exit without settlement or durable quiescence.
- Stop if recovery terminal evidence or parent settlement can write without a matching active lease and exact fact-source cut.
