# ADR-0084: Stage Phase D capability inputs and recovery cutovers

**Status**: Accepted
**Date**: 2026-08-31

Supplements ADR-0083. It does not supersede or modify ADR-0083's accepted host-coordinated runtime boundary.

## Context

ADR-0083 requires plugins to receive no store, executor, mailbox, cursor, or dispatch capability. Phase D1 moved core tool execution behind a host-owned executor and added declared plugin capabilities, but the compatibility pipeline still filters plugins by hook name rather than by hook arguments. A declared `build_context` hook can request the mutable `PipelineContext`, whose configuration still contains host services and semantic-memory stores.

`SemanticMemoryPlugin` currently owns the semantic index, memory review store, topic store/index, and a derived-index cache. It reads those live sources during `build_context` and writes semantic hit markers and context-pack state into `PipelineContext.config`. Rebuilding context for the same user or admitted runtime input can therefore observe a later store state.

The Phase D persistence cutover also has two unresolved ordering constraints. SQLite and PostgreSQL mailbox rows have no durable dispatch generation/cut, while `SegmentCoordinator` already expects authorization to reject a stale cut. Approval decisions still pass through the in-memory runtime-message path and a later `settled` effect write. The legacy effect rank table cannot represent `prepared -> rejected`, so mailbox activation, approval transition migration, and removal of `settled`/rank replacement cannot be independently enabled.

Finally, an indeterminate `EffectSettled` currently removes the pending effect plan and commits an error tool-result fact before reconciliation. A later reconciliation result then has no pending plan through which to re-enter `AgentEngine`. ADR-0083 freezes the four-member `EngineStepInput` union, so Phase D must express reconciliation with the existing `EffectSettled` input unless a later ADR explicitly supersedes that freeze.

## Decision

### Capability-declared hooks receive capability-scoped values

Capability declaration changes both hook registration and hook invocation:

- A capability-declared hook never receives `PipelineContext`, `HookRuntime`, a mutable `Tape`, a store, an executor, a mailbox/cursor, or a dispatch permit.
- A `PENDING_FACT`/context-contribution hook receives one immutable, plugin-specific input produced by a host-owned input provider.
- An `EFFECT_PLAN` hook receives no execution function. It returns schemas or typed plans only.
- An `OBSERVER` hook receives a frozen event value and returns nothing.
- Undeclared plugins remain compatibility-only while their named Phase D slice is pending. The new runtime must reject undeclared plugins before its first stable release gate.

The internal compatibility `Pipeline` may invoke a host-owned context-input provider with `PipelineContext`; that provider is not registered as a plugin and is not reachable through `HookRuntime`. The provider snapshots all plugin inputs before invoking any `build_context` hook. It returns an immutable mapping keyed by plugin ID, and each value contains only that plugin's capability-scoped input. The host may derive a separate immutable compatibility view for a legacy hook that explicitly requests context inputs. For KB, that view contains only a frozen semantic hit summary; it never contains another plugin's messages, `ContextPack`, provider, or source stores.

### Phase D2 snapshots semantic grounding by stable source identity

Phase D2 introduces a host-owned semantic grounding provider and a frozen plugin input with these logical fields:

- `input_id`: session identity plus the currently selected source identity: the latest admitted `USER_STEER` or `SUBAGENT_MESSAGE` `message_id` when a runtime prompt is selected, otherwise the latest windowed user `Entry.id`.
- `query_digest`: SHA-256 digest of the exact recall query.
- `hit_count`: number of selected topic and accepted-memory hits.
- `messages`: immutable ordered `(role, content)` grounding messages.

The host provider owns the semantic index, review store, topic store/index, recall planner, and any snapshot cache. Runtime-prompt selection keeps precedence over tape user entries. For one `input_id`, the provider reads source stores at most once and returns the same frozen value for every incremental/full context rebuild. A change in the selected identity, from a new user entry, a new admitted runtime prompt, or a window/handoff that selects a different user entry, creates a new snapshot. Store changes alone never replace an existing snapshot.

`SemanticMemoryPlugin` declares `PENDING_FACT`, owns no store/index/cache, and only converts its frozen input to fresh message dictionaries. `SemanticMemoryGroundingInput` contains no `ContextPack`. The host retains the selected `ContextPack` outside the plugin-input mapping and records it with the existing host stash for run metadata. KB deferral receives only a frozen `{query_digest, hit_count}` summary for the selected identity; the semantic grounding marker in `PipelineContext.config` is removed.

Host maintenance services may remain attached to the legacy session context until their own cutover, but capability-declared hooks cannot receive that context. This Phase D2 compatibility allowance does not permit those services on the new runtime boundary.

### Phase D3 uses two activation gates

Phase D3 is implemented as two separately reviewed slices:

1. **D3a, additive mailbox infrastructure**
   - Add durable command admission and a monotonic dispatch generation/cut to SQLite and PostgreSQL.
   - Advance the dispatch generation only for commands that can invalidate dispatch authorization: cancel, interrupt, and approval denial.
   - Keep current approval/subagent consumers and legacy effect writers active.
   - Do not remove `settled` or rank replacement.

2. **D3b, atomic runtime activation**
   - Route approval and subagent commands through the durable mailbox.
   - Commit command disposition and `prepared -> dispatched|rejected` in one fenced unit of work.
   - Issue a `DispatchPermit` only after the approved transition commits.
   - Migrate every live effect writer to typed legal transitions.
   - Activate the coordinator path and remove the effect `settled` alias and rank-based replacement in the same change.

Historical `settled` rows are legacy-runtime terminal history. They are not rewritten because they do not preserve whether approval was accepted or denied. A session with a live ambiguous legacy approval is not migrated into the new runtime; runtime/checkpoint version fencing keeps it on the matching legacy path.

### Phase D4 reuses `EffectSettled` after reconciliation

Phase D4 preserves the frozen `EngineStepInput` union:

- This record narrows ADR-0083's `EffectSettled` tool-result rule: only a completed or failed `EffectSettled` becomes a pending durable tool-result fact.
- An indeterminate `EffectSettled` has its own consume-once `input_id`, commits `dispatched -> unknown`, and blocks, but does not remove the pending effect plan or commit a final tool-result fact.
- Internal coordinator/engine state retains the original dispatch authorization transition identity next to the pending plan. The public `EffectPlan` type does not change.
- `commit_reconciliation` validates a durable `ReconciliationRecord` and commits `unknown -> completed|failed`.
- The coordinator then creates a completed/failed `EffectSettled` whose `input_id` is distinct from the already-consumed indeterminate settlement input and whose authorization identity is the retained original identity.
- Settlement validation, including `RunSegmentRequest` construction, checks that retained authorization identity instead of the latest unrelated `commit_ref`.
- The completed/failed re-entry removes the plan and commits exactly one final tool-result fact through the facts-and-state transition. It does not apply a second effect-ledger mutation after `commit_reconciliation`.

Same-run recovery adopts exact idempotent replays, reloads and re-proposes after recoverable CAS conflict, retains `run_id`, increments `owner_epoch`, and fences the old owner. Crash alone never writes `interrupted`. A new owner cannot issue another permit or record negative reconciliation until the previous executor is durably quiescent/revoked, unless an external idempotency key covers all attempts.

If implementation cannot satisfy these rules with the existing `EffectSettled` type, work stops. A new ADR must supersede the Phase C input freeze before adding another `EngineStepInput` variant.

## Implementation Plan

1. Execute `.opencode/prompts/packets/tp-2026-08-31-adr-0083-phase-d2-grounding.md` for the capability-scoped context input and semantic grounding cutover.
2. Before D3 implementation, create separate D3a and D3b task packets. D3a must be additive; D3b owns the atomic writer/coordinator/alias cutover.
3. Before D4 implementation, create a task packet covering retained unknown plans, reconciliation re-entry, exact replay adoption, same-run takeover, and quiescence evidence.
4. Keep Phase E durable host ports and Phase F canonical `EventRecord` integration outside these Phase D packets.

## Alternatives Rejected

- **Modify ADR-0083 in place**: rejected because accepted ADR bodies are immutable in this repository.
- **Supersede ADR-0083 now**: rejected because the host-coordinated boundary, public ports, effect graph, and mailbox authority remain unchanged. This record adds implementation decisions.
- **Pass a read-only store object to semantic-memory plugins**: rejected because a live store can return different data for the same engine input and remains a hidden capability.
- **Keep `PipelineContext` but document that plugins should not use it**: rejected because the boundary would depend on convention and remain untestable.
- **Activate mailbox storage, approval routing, coordinator execution, and rank removal in one unreviewed slice**: rejected because the schema work is independently testable while the writer/alias removal must remain atomic.
- **Add `ReconciliationResolved` as a fifth engine input now**: rejected because the existing `EffectSettled` can represent a uniquely identified post-reconciliation final settlement if the pending plan and authorization identity are retained.
- **Rewrite historical `settled` rows**: rejected because the alias discarded the approved/denied distinction required by the explicit graph.

## Acceptance Criteria

### Phase D2

- [ ] `test_capability_declared_build_context_hook_receives_only_plugin_input`
- [ ] `test_classified_plugin_cannot_receive_context_runtime_tape_store_executor_mailbox_or_cursor`
- [ ] `test_semantic_grounding_snapshot_is_reused_for_same_input_identity`
- [ ] `test_semantic_grounding_store_change_does_not_change_existing_snapshot`
- [ ] `test_new_user_entry_identity_creates_new_semantic_grounding_snapshot`
- [ ] `test_new_runtime_prompt_identity_creates_new_semantic_grounding_snapshot`
- [ ] `test_window_change_of_selected_user_entry_creates_new_semantic_grounding_snapshot`
- [ ] `test_semantic_memory_plugin_owns_no_store_index_or_snapshot_cache`
- [ ] `test_host_records_semantic_context_pack_run_metadata`
- [ ] `test_kb_context_inputs_are_hit_summary_only_and_exclude_context_pack`
- [ ] `test_kb_defer_reads_frozen_semantic_hit_count_without_context_marker`
- [ ] `test_incremental_and_full_context_rebuild_render_same_semantic_grounding`
- [ ] `uv run pytest tests/agentkit/plugin/test_registry.py tests/agentkit/runtime/test_pipeline.py tests/agentkit/test_incremental_context.py tests/coding_agent/plugins/test_semantic_memory.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_system_smoke.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_bootstrap.py tests/coding_agent/test_memory_switch.py tests/ui/test_session_manager_runtime.py -q`

### Phase D3

- [ ] `test_mailbox_admission_advances_dispatch_generation_sqlite`
- [ ] `test_mailbox_admission_advances_dispatch_generation_postgresql`
- [ ] `test_approval_allow_does_not_stale_its_own_dispatch_cut`
- [ ] `test_control_or_denial_between_probe_and_authorize_rejects_stale_cut_sqlite`
- [ ] `test_control_or_denial_between_probe_and_authorize_rejects_stale_cut_postgresql`
- [ ] `test_approval_disposition_and_prepared_transition_commit_atomically_sqlite`
- [ ] `test_approval_disposition_and_prepared_transition_commit_atomically_postgresql`
- [ ] `test_effect_ledger_has_no_settled_alias_or_rank_replacement_after_cutover`
- [ ] `uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_harness_p2_wrap.py tests/coding_agent/test_harness_p2_fact_source.py -q`

### Phase D4

- [ ] `test_indeterminate_settlement_keeps_pending_plan_and_commits_no_final_tool_fact`
- [ ] `test_reconciliation_reenters_with_existing_effect_settled_input`
- [ ] `test_reconciled_effect_commits_exactly_one_final_tool_result_fact`
- [ ] `test_post_reconciliation_settlement_input_differs_from_indeterminate_input`
- [ ] `test_run_segment_request_accepts_retained_authorization_after_commit_ref_moves`
- [ ] `test_post_reconciliation_reentry_does_not_mutate_effect_ledger_twice`
- [ ] `test_exact_replay_adopts_committed_state_and_continues`
- [ ] `test_crash_retains_run_and_does_not_write_interrupted`
- [ ] `test_same_run_takeover_fences_old_owner_with_new_owner_epoch`
- [ ] `test_takeover_blocks_new_permit_until_executor_quiescent`
- [ ] `uv run pytest tests/agentkit/runtime/test_engine.py tests/agentkit/runtime/test_coordinator.py tests/coding_agent/test_runtime_run_recovery.py -q`

## References

- `docs/adr/README.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0076-harness-control-plane.md`
- `docs/adr/0077-connected-chat-session-event-projection.md`
- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`
- `.opencode/prompts/packets/tp-2026-08-30-adr-0083-phase-d1-capabilities.md`
- `src/agentkit/plugin/registry.py`
- `src/agentkit/runtime/contracts.py`
- `src/agentkit/runtime/engine.py`
- `src/agentkit/runtime/coordinator.py`
- `src/agentkit/runtime/pipeline.py`
- `src/coding_agent/core/app.py`
- `src/coding_agent/plugins/semantic_memory.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/server/session/persist.py`
- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/pg_durable/uow.py`
