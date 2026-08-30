# ADR-0083: Adopt a host-coordinated durable AgentKit runtime boundary

**Status**: Accepted
**Date**: 2026-08-29

Supersedes ADR-0001, ADR-0005, ADR-0006, and ADR-0010 (checkpoint restore contract stays narrow and explicit). ADR-0010 (synchronize checkpoint restore with active turns) is retained.

## Context

The public `Pipeline`/`PipelineContext` pair mixes engine logic, stores, the runtime message cursor, plugins, streaming, and effect execution in one mutable object (`src/agentkit/runtime/pipeline.py`). A single `PipelineContext` carries `storage`, `llm_provider`, `runtime_message_bus`, `runtime_message_cursor`, `toolset`, and directive execution, and the turn mutates that bag in place.

That shape cannot provide crash-safe write-ahead semantics. Effects execute before any durable intent commit, tool retries run inside the engine (`ToolExecutionOptions.max_retries`), and the coding_agent effect ledger still allows rank-based status replacement with a `settled` alias (`effect_status_may_replace` in `src/coding_agent/stores/rtstore/harness.py`). After a crash between dispatch and settlement there is no durable boundary that can say what was authorized, so retry and recovery cannot be made safe by local fixes.

ADR-0068 already defines transactional durable fencing, ADR-0076 defines the authoritative unit of work and the session `EventRecord` fact source, and ADR-0077 makes `EventRecord` the sole canonical chat fact source. What is missing is the runtime boundary itself: a persistence-free engine, a standard coordinator, and narrow host ports, so existing wire behavior migrates without a second engine API. AgentKit stays host-neutral; the coding_agent daemon owns the durable product lifecycle; SQLite and PostgreSQL share the same fenced unit-of-work contract.

Non-goals for this record: implementation code; same-segment live steer; `ViewManifest`/`TrajectoryProjection` details; `RestorePoint` wire/GC/UI; the package split itself; and a removal schedule for the legacy `Pipeline`.

## Decision

Adopt a host-coordinated durable runtime boundary. AgentKit provides a persistence-free `AgentEngine` plus a standard `SegmentCoordinator`; hosts implement the narrow `CommitPort`, `ModelAdapter`, and `EffectExecutor` ports.

### Engine and coordinator

- `AgentEngine` is persistence-free and performs no adapter I/O. It receives an `EngineStepRequest` pairing an immutable `OperationStateVersion` with exactly one consume-once `EngineStepInput`, then calculates a `TransitionProposal`. It cannot read or write stores, coordinate durable commits, access the mailbox or cursor, invoke adapters, or directly execute effectful tools.
- `EngineStepRequest` carries a consume-once `EngineStepInput` union. Each variant has a stable input identity and is consumed exactly once by the state transition that commits it; a variant can never be replayed into a second transition:
  - `Initial(command_batch, mailbox_cut)` opens a segment: the admitted command batch becomes pending commands awaiting disposition, and `mailbox_cut` is the mailbox lane generation/cut that later fences dispatch authorization for this segment.
  - `ModelGenerationCompleted(ModelGenerationResult)` re-enters the engine after a model round: the final assistant content and finalized thinking become pending durable facts, and the ordered tool calls become `EffectPlan` items in the next proposal; with no tool calls the engine proposes a terminal completion instead of another model request.
  - `EffectSettled(EffectSettlement)` re-enters the engine after an effect settlement: the settlement becomes a pending durable tool-result fact, and the engine proposes the next model request, a terminal outcome, or a blocked outcome.
  - `ApprovalResolved(ApprovalSettlement)` re-enters the engine after an approval decision: the approval state advances, an approval continues the prepared effect toward dispatch, and a denial becomes a pending durable tool-result fact carrying the stable rejection reason so the engine proposes the next model round.
- Phase C freezes these logical async contracts; no later phase may change their signatures:

  ```text
  FrameSink.emit(frame: StreamFrame) -> Awaitable[None]
  CommittedFactSink.emit(notice: CommittedFactNotice) -> Awaitable[None]
  ModelAdapter.generate(
      request: ModelRequest,
      frame_sink: FrameSink,
      cancellation: CancellationToken,
  ) -> ModelGenerationResult
  AgentEngine.propose(request: EngineStepRequest) -> TransitionProposal
  SegmentCoordinator.run(
      request: RunSegmentRequest,
      control_probe: ControlProbe,
      frame_sink: FrameSink,
      committed_fact_sink: CommittedFactSink,
  ) -> SegmentOutcome
  CommitPort.commit_transition(request: CommitTransitionRequest) -> CommitTransitionResult
  CommitPort.authorize_dispatch(request: DispatchAuthorizationRequest) -> DispatchAuthorizationResult
  CommitPort.commit_settlement(request: CommitSettlementRequest) -> CommitSettlementResult
  CommitPort.commit_reconciliation(request: CommitReconciliationRequest) -> CommitReconciliationResult
  EffectExecutor.execute(permit: DispatchPermit, cancellation: CancellationToken) -> EffectExecutionResult
  ControlProbe.observe() -> ControlSnapshot
  ControlProbe.wait(after: ControlGeneration) -> Awaitable[ControlSnapshot]
  ```

- Commit results explicitly distinguish committed, exact idempotent replay, CAS conflict, stale owner, stale mailbox cut, invalid transition, and storage failure.
- `EffectExecutionResult` distinguishes completed, failed, and indeterminate dispatch. Cancellation after dispatch cannot claim non-execution: when execution is uncertain the result is indeterminate and lands in the ledger's `unknown` state.
- Before the Phase C freeze is declared, `agentkit.runtime` exports every transitive public type: `StreamFrame`, `CancellationToken`, `OperationStateVersion`, the `EngineStepInput` variants, the `next_action` variants, `EffectPlan`, the settlement variants, `DispatchPermit`, `ReconciliationRecord`, the command dispositions, the commit request/result types, `ControlSnapshot`/`ControlGeneration`, `CommittedFactSink`/`CommittedFactNotice`, `ModelGenerationResult`, and the `SegmentOutcome` variants.

- `ModelGenerationResult` carries the final assistant content, the finalized thinking payload if one was produced for product display, the ordered tool calls, usage, and provider stop metadata. The coordinator feeds it back into the next engine step as a `ModelGenerationCompleted` input: the final content and finalized thinking re-enter a `TransitionProposal` as pending durable facts, and the ordered tool calls become `EffectPlan` items in that proposal. A model tool call therefore has a defined terminal path into the effect ledger, and `StreamFrame` never carries tool calls or usage.
- `TransitionProposal.next_action` is an explicit union of five variants: `ModelGeneration`, `PreparedEffect` (a tool effect dispatch or an approval wait — approval is a prepared-effect variant per the approval contract below), `Terminal`, `Blocked`, and `SafeYield`.
- `SegmentCoordinator` owns the loop and settlement re-entry; host adapters never re-enter the engine themselves. It repeats `engine proposal -> fenced commit -> model/effect/approval action -> result/settlement commit -> engine proposal` until the proposal is terminal, blocked, interrupted, a safe yield, or a configured round limit is reached, feeding each post-action result back as the matching `EngineStepInput` variant, then returns a `SegmentOutcome` describing how the segment ended (the outcome union is defined below). The coordinator owns `ModelAdapter`, `CommitPort`, `EffectExecutor`, `ControlProbe`, `FrameSink`, and `CommittedFactSink`; the host supplies those ports.
- Awaiting `FrameSink.emit` provides bounded backpressure. A sink failure disables further ephemeral frames and is reported to the host; it does not roll back or fail an otherwise durable segment. Only `ControlProbe`/cancellation changes segment control flow.
- The coordinator derives a `CancellationToken` from `ControlProbe` and passes it to `ModelAdapter.generate` so a live model stream can stop.
- Plugins receive no store, executor, mailbox, cursor, or dispatch capability. Pure fact contributors return pending facts. Effectful extensions produce an `EffectPlan` or live behind the host `EffectExecutor`. Observers return nothing. Explicitly approved read-only grounding inputs are immutable host-provided inputs, not plugin-held stores.

### Segment outcome and cancellation

`SegmentOutcome` is a discriminated union with exact fields:

- `Completed`: committed state ref, `final_message`, `steps_taken`, `stop_reason`.
- `Blocked`: committed state ref, `reason`, the effect/approval reference when applicable, `steps_taken`. The segment waits on a durable approval or effect precondition; it is not terminal.
- `SafeYield`: committed state ref, `reason`, `steps_taken`; the pending durable control command remains undispositioned. Not terminal.
- `Cancelled`: committed state ref, the durable cancel command disposition/fact, `steps_taken`.
- `RoundLimit`: committed state ref, `steps_taken`, with the `MAX_STEPS_REACHED` compatibility mapping.
- `Failed`: the last committed state ref if any, a typed error/report, `steps_taken`.

The Phase C compatibility adapter maps outcomes to the existing `StopReason` values (`src/coding_agent/adapter/types.py`): `Completed` maps to `NO_TOOL_CALLS` and preserves the adapter's existing doom-loop derivation (`DOOM_LOOP`); `RoundLimit` maps to `MAX_STEPS_REACHED`; `Failed` maps to `ERROR`; `Cancelled` and an interrupt-driven `SafeYield` map to `INTERRUPTED`. The ADR-0077 durable root outcome mapping is: `Completed` and `RoundLimit` settle `completed`, `Failed` settles `failed`, `Cancelled` settles `cancelled`, and an interrupt settles `interrupted` when its durable command is dispositioned. `Blocked` and an undispositioned `SafeYield` produce no root outcome because the run has not settled.

A probe-triggered cancellation during `ModelAdapter.generate` discards uncommitted partial output and returns `SafeYield` with the interrupt mapping; the partial content never becomes a durable fact. A later engine step that consumes and dispositions the durable cancel command returns `Cancelled`. `FrameSink`/`CommittedFactSink` delivery failure is reported to the host but never rolls back durable work.

### Atomic transition and state/log binding

- The logical `OperationStateVersion` is `{run_id, revision, projection_epoch, commit_ref, value}`.
- The store stamps `commit_ref`: a `transition_id` plus optional `fact_seq_start`/`fact_seq_end`.
- `commit_ref` is a transition anchor, not the physical session event-log head. Mailbox admission may advance physical `session_seq` without advancing the engine revision.
- The host fenced unit of work atomically validates the compare-and-swap precondition, allocates sequence numbers, and commits the state version, `EventRecord`s, typed command dispositions, and the effect ledger mutation. One transition commits all four or none.
- The engine never predicts `session_seq`. A CAS conflict or failure writes no part of the transition.
- Commits are idempotent through an immutable transition receipt. The receipt is first-write-wins, keyed by `(session_id, projection_epoch, transition_id)`, and stores a canonical mutation fingerprint covering the state value, pending facts, dispositions, and the effect mutation. An exact same-epoch retry returns the stored committed result before the CAS check and before any write. A reuse of the key with a different fingerprint fails deterministically. A retry after a projection-epoch change takes the state-version CAS failure path and writes nothing. A retry that arrives after a later transition cannot overwrite any table. This replaces the current idempotent-event branch in both unit-of-work implementations (`src/coding_agent/stores/local_durable/uow.py`, `src/coding_agent/stores/pg_durable/uow.py`), which marks an operation idempotent but then continues to apply the supplied mutations.
- SQLite and PostgreSQL implement the same fenced unit-of-work contract (ADR-0068 parity).

### Commands

- Product commands first enter the durable host `CommandMailbox`. A request carries an ordered, immutable admitted command batch.
- A proposal may emit terminal dispositions:
  - `applied`: the command affected state or the transition decision.
  - `rejected`: invalid, unauthorized, or inapplicable; a stable `reason_code` is required.
  - `superseded`: previously valid but made obsolete by an explicit precedence/replacement relation; a stable `superseded_by_command_id` is required.
- Commands absent from the dispositions remain pending. Do not introduce `deferred`.
- `ControlProbe` is only a level-triggered wake/yield mirror. It cannot carry the only durable cancel, interrupt, or steer content; losing a probe must not lose a command. The coordinator polls `ControlProbe` before the engine proposal, before the proposal commit, before dispatch authorization, immediately before `EffectExecutor`, after settlement, and between model rounds. A raised probe causes a safe yield only; the durable command remains pending until a later transition dispositions it.

### Effects and recovery

- The effect ledger is an explicit state graph: `prepared -> rejected|dispatched`, then `dispatched -> completed|failed|unknown`, and `unknown -> completed|failed` only through fenced reconciliation backed by durable evidence. No rank-based arbitrary replacement and no `settled` alias. This replaces `effect_status_may_replace` and `_EFFECT_STATUS_RANKS` in `src/coding_agent/stores/rtstore/harness.py`.
- Approval is part of the durable prepared-effect flow, not an after-dispatch callback. The unit of work that establishes an approval wait allocates the `effect_id`, commits the `prepared` ledger state and the `approval_requested` fact, and yields blocked without a `DispatchPermit`. The approval response first enters the durable `CommandMailbox`. An approved response is atomically dispositioned with `prepared -> dispatched`, and only then may a permit be issued. A rejected response is atomically dispositioned with `prepared -> rejected`; no permit exists.
- A `prepared -> rejected` denial produces a consume-once `ApprovalSettlement` carrying the tool-call identity and a stable rejection reason. The rejection disposition, the durable tool-result fact, and its `CommittedFactNotice` commit atomically in the same unit of work. The coordinator feeds the settlement into the next `EngineStepRequest` as `ApprovalResolved` and continues the loop, so a denial commits exactly one tool result and reaches the next model round — preserving the current deny-and-continue behavior in `src/agentkit/runtime/pipeline.py`.
- Approval facts, approval command dispositions, and effect identity survive crash/re-entry. Approve/resume cannot redispatch an already dispatched or unknown effect (ADR-0076). A manual choice to try again creates a new linked effect attempt/identity and requires external idempotency or durable executor-quiescence evidence.
- The required order is: commit prepared intent; commit dispatch authorization; issue an unforgeable `DispatchPermit`; execute; commit settlement. The `EffectExecutor` is never called before durable dispatch authorization exists.
- Dispatch authorization is fenced against concurrent control admission. `RunSegmentRequest` (via the `Initial` input) carries the mailbox lane generation/cut the proposal was computed against, and `DispatchAuthorizationRequest` carries that expected cut. In the same fenced unit of work as `prepared -> dispatched`, the commit rejects with the stale-mailbox-cut result when a precedence-bearing cancel, interrupt, or approval-denial command was admitted after the proposal's cut; the coordinator then re-probes and re-proposes instead of dispatching. The `OperationStateVersion` CAS stays independent of the physical event head. SQLite and PostgreSQL race tests cover control-command admission between the last pre-authorization probe and the authorization commit.
- `DispatchPermit` is opaque and single-use, bound to `session_id`, `effect_id`/`attempt_id`, the dispatch authorization `transition_id`, `owner_epoch`, and the idempotency key when present. ADR-0068 fences durable writes inside the transaction; the permit binding extends that fencing to the external execution that the transaction cannot contain. On owner takeover, every outstanding dispatched permit is treated as potentially executable: a new owner cannot record negative reconciliation or issue a new permit until the old executor is durably quiescent/revoked, unless all attempts share an externally enforced idempotency key. Old-owner settlements remain fenced.
- A durable `ReconciliationRecord` carries effect identity, observed outcome, evidence reference, actor/owner epoch, and transition identity. Only this record authorizes `unknown -> completed|failed`. Redispatch never reuses the unknown attempt identity.
- The default is no automatic retry after an indeterminate dispatch. Retry requires an explicit idempotency or reconciliation capability.
- `ModelAdapter` is distinct from `EffectExecutor`. Provider model request retry and cost semantics live behind the host `ModelAdapter` and are explicitly outside the effect ledger decision.
- A daemon crash retains the in-flight `run_id`; a new `owner_epoch` fences the old owner (ADR-0068). Crash alone is not `interrupted`.
- A durable terminal run may resume as a linked new run (ADR-0055). An `unknown` effect blocks until reconciliation or an explicit decision.

### Canonical facts

- `EventRecord` is the only authoritative session fact source (ADR-0076, ADR-0077). AgentKit `Tape` is a context/checkpoint projection and cache, not a physical `SessionTape`.
- `StreamFrame` is ephemeral: live token deltas, live thinking deltas, and heartbeat output only. Live thinking deltas are never persisted.
- The coordinator also exposes an ordered `CommittedFactNotice` surface through the `CommittedFactSink` port, separate from `StreamFrame`. It emits a notice only after a prepared/tool-call commit and after a settlement/tool-result commit, so transport never projects an uncommitted proposal. Delivery is ordered after the commits and is independently backpressured from `FrameSink`; a sink failure disables further notices and reports a host delivery error without mutating committed facts or failing the segment. The Phase C compatibility adapter maps those notices to the existing `ToolCallDelta` and `ToolResultDelta` wire events, keeping current transport behavior intact. From Phase F each notice carries or references the committed `EventRecord` identity.
- A finalized thinking payload produced for product display is a durable semantic fact: it re-enters the `TransitionProposal` as a pending fact and commits as an `EventRecord` in the same unit of work as the transition.
- Durable semantic facts travel through the proposal and the unit of work into `EventRecord`; they are never persisted from `StreamFrame`.

### Checkpoint capture and restore are disabled until Phase G (Option B)

Phase F may be stable only with the checkpoint capability explicitly unavailable for new-runtime sessions. Until Phase G completes the new restore contract, for every new-runtime session:

- Both checkpoint capture and checkpoint restore are disabled — enforced by the daemon, not merely hidden in clients.
- The daemon rejects capture and restore before any mutation with the stable errors `checkpoint_capture_not_supported_for_runtime_version` and `checkpoint_restore_not_supported_for_runtime_version`.
- Session and checkpoint records carry a runtime/checkpoint format version. Legacy checkpoints may be used only by matching legacy runtime sessions; no cross-version capture or restore is allowed.
- CLI, TUI, and Night Console disable the capture/restore entry points for new-runtime sessions, but server enforcement is authoritative.
- The version markers and the rejection path land no later than the first phase that serves sessions on the new runtime, and the Phase F stable release gate includes tests for deterministic rejection and zero mutation on both backends.
- The old Tape/`PipelineContext` restore is forbidden on the new runtime.

Phase G replaces capture and restore with `OperationStateVersion`/`commit_ref`/`EventRecord`/effect/mailbox/projection-epoch semantics and re-enables the capability only after the SQLite/PostgreSQL parity and crash tests pass. The legacy runtime keeps the Accepted ADR-0001/0005/0006/0010 checkpoint behavior unchanged.

### Phases

- Phase C freezes the final request/proposal/outcome/port types — including the signatures and the `next_action` union above — hides `Pipeline`/`PipelineContext` behind an internal compatibility adapter, and preserves the current transport, wire, and stop-reason behavior, with the adapter projecting `CommittedFactNotice`s to the existing tool wire events. Phase C does not promise WAL or recovery semantics.
- Phase D is an internal, non-shipping coordinator WAL implementation: explicit effect state transitions, the durable approval prepared-effect flow, permit fencing, no automatic retry after an indeterminate dispatch, plugin capability isolation, fencing, and same-run recovery land inside the coordinator without a public release commitment. Phase D also migrates the coding_agent plugins to that capability boundary and removes the legacy effect-writer aliases atomically with the coordinator cutover.
- Phase E installs the durable host `CommitPort` and `EffectExecutor` ports on both SQLite and PostgreSQL. Phase F routes canonical `EventRecord` facts through the proposal and unit of work and completes integration; from Phase F each `CommittedFactNotice` carries or references committed `EventRecord` identity. Phase F is the first stable release gate, and SQLite and PostgreSQL must both pass before it.
- If the Phase C types cannot express dispositions, `commit_ref`, `EffectPlan`, the approval wait, and settlement without later signature changes, Phases C and D must be combined rather than shipping a Phase C surface that Phase D has to break.
- The MVP connected-chat product path (ADR-0077) does not wait for WAL: `dispatch_committed` WAL is outside MVP scope, lands internally in Phase D, and is stable only from the Phase F gate.

### Relation to prior ADRs

- **ADR-0001, ADR-0005, ADR-0006, and ADR-0010 (checkpoint restore contract stays narrow and explicit) are superseded by this accepted ADR.** Their implementations remain in service only for matching legacy-runtime checkpoints during migration. Their checkpoint capture (serialized tape plus `PipelineContext.plugin_states`), best-effort plugin-state hint injection, and same-timeline truncate-restore contracts describe the engine-coupled runtime this ADR replaces. Restore is re-expressed through `OperationStateVersion`, `commit_ref`, and projection epochs in Phase G; until Phase G, new-runtime sessions reject checkpoint capture and restore outright (see the Option B contract above), while the legacy runtime continues its matching legacy behavior. The ADR-0006 hint behavior has no successor mechanism: the new runtime does not restore through `PipelineContext.plugin_states`, Phase G removes that capture/injection path, and restore rebuilds runtime state only from the restored `OperationStateVersion` value, committed `EventRecord` facts, and host-provided immutable inputs.
- **ADR-0010 (synchronize checkpoint restore with active turns) is retained.** Restore stays serialized with active turns through the per-session turn lock, and hot provider reuse is allowed only when `provider_name`, `model_name`, and `base_url` all match the persisted session metadata. Phase G carries both guards onto the new boundary; until then, new-runtime sessions reject restore outright, so the guards bind legacy-runtime restore paths only.
- **ADR-0068 remains the fencing contract.** The fenced host unit of work checks `{session_id, owner_id, epoch}` in the same transaction as the protected mutation, in both SQLite and PostgreSQL.
- **ADR-0076 remains the harness fact-source and unit-of-work authority.** This ADR's unit of work is the runtime-transition slice of ADR-0076's authoritative unit of work; the P3 OpenRPC / P4 daemon track is unchanged.
- **ADR-0077 remains the connected-chat projection contract.** `EventRecord`, cursor, and epoch semantics are unchanged; this ADR changes how runtime transitions produce those records, not how chat reads them.
- **ADR-0055 and ADR-0075 are preserved.** Resume still creates a new linked run, and restore still marks superseded runs without deleting them; Phase G reuses those fields rather than inventing a second lineage scheme.

## Alternatives Rejected

- Keep the public `Pipeline`/`PipelineContext` — rejected because one mutable context that owns stores, cursor, streaming, and effect execution cannot draw a durable authorization boundary; effect-before-commit and in-engine tool retry are structural, not incidental.
- Give `AgentEngine` the whole `TransitionStore`/unit of work, or read access to it — rejected because a store-aware engine re-couples computation to persistence, leaks CAS and sequence allocation into AgentKit, and breaks host neutrality.
- Let every host hand-roll effect sequencing — rejected because the prepare/dispatch/settle order is exactly where crashes corrupt state; a standard `SegmentCoordinator` makes the safe order the default instead of per-host folklore.
- Separate a writable `FactSink` from state transition commit — rejected because state and facts can then diverge at a crash boundary; the fenced unit of work must commit state version, `EventRecord`s, dispositions, and the effect ledger atomically.
- Bind the `OperationStateVersion` CAS to the physical global event head — rejected because mailbox admission and other log writes advance `session_seq` without a state transition; coupling CAS to the head would fail valid transitions and serialize unrelated writes.
- Use the ephemeral `ControlProbe` as the only command source — rejected because a probe can be lost; cancel/interrupt/steer must be durable mailbox commands, with the probe as a wake/yield mirror only.
- Consume every request command blindly, or return only naked consumed IDs — rejected because rejection and supersession are decisions with stable codes that must commit atomically with the transition, and unaddressed commands must remain pending.
- Execute an effect before durable intent, or keep default automatic retry — rejected because an executor call without a committed permit cannot be fenced or reconciled after a crash, and blind retry of an indeterminate dispatch can duplicate externally visible effects.
- Let each host adapter own the post-settlement re-entry loop — rejected because max-round, step-count, stop-reason, crash-recovery, and unknown-effect blocking behavior would then diverge per host; the standard `SegmentCoordinator` must define the runtime behavior.
- Keep approval as an in-engine or in-plugin directive wait — rejected because it would give engine/plugin code a host interaction capability, block proposal calculation, and could create durable dispatch authorization before consent with no defined denial transition.
- Ship Phase F with the legacy Tape/`PipelineContext` checkpoint capture/restore still live on the new runtime — rejected because the old restore rewinds tape/session state without the runtime state version or effect-ledger reconciliation, so a restore between retries can resume mutually inconsistent durable inputs; the new runtime hard-disables the capability until Phase G (Option B) instead of pulling the whole Phase G restore cutover ahead of the first stable gate.

## Implementation Plan

Phase A — this accepted ADR, with no code changes. Acceptance supersedes ADR-0001, ADR-0005, ADR-0006, and ADR-0010 (checkpoint restore contract stays narrow and explicit) in the same lifecycle change; ADR-0010 (synchronize checkpoint restore with active turns) remains Accepted.

Phase B — typed contracts and unit of work:

- `src/agentkit/runtime/messages.py`: add `OperationStateVersion`, `TransitionProposal`, `StreamFrame`, `CommittedFactNotice`, command/disposition, `EffectPlan`, and `ReconciliationRecord` types beside the existing runtime message primitives.
- `src/coding_agent/stores/rtstore/harness.py`, `src/coding_agent/stores/local_durable/uow.py`, `src/coding_agent/stores/pg_durable/uow.py`, `src/coding_agent/stores/pg_durable/sql_harness.py`, and the required PG schema/row-codec paths: extend the fenced unit of work to carry the state version, typed dispositions, and the explicit effect graph on both backends. Phase B defines the new graph and types only; it must not remove the `settled` alias or rank-based replacement while live writers still emit them — `src/coding_agent/server/session/persist.py` still commits `EffectLedgerSlot(status="settled")` for approval decisions until Phase D migrates it.

Phase C — final API compatibility adapter:

- `src/agentkit/runtime/pipeline.py`, `src/agentkit/__init__.py`, and `src/agentkit/runtime/__init__.py`: freeze the final request/proposal/outcome/port types and move `Pipeline`/`PipelineContext` behind an internal compatibility adapter; no public signature may need a Phase D change. The final `agentkit.runtime` exports are `AgentEngine`, `SegmentCoordinator`, the request/proposal/outcome contracts (`EngineStepRequest`, the `EngineStepInput` variants, `TransitionProposal` and its `next_action` variants, `RunSegmentRequest`, `SegmentOutcome` and its variants, `ModelRequest`, `ModelGenerationResult`, `CommittedFactNotice`), the port types (`ModelAdapter`, `CommitPort`, `EffectExecutor`, `ControlProbe`, `FrameSink`, `CommittedFactSink`), and every transitive public type (`StreamFrame`, `CancellationToken`, `OperationStateVersion`, `EffectPlan`, the settlement variants, `DispatchPermit`, `ReconciliationRecord`, the command dispositions, the commit request/result types, `ControlSnapshot`, `ControlGeneration`). The internal compatibility adapter policy is documented but not exported. `Pipeline` and `PipelineContext` are not public exports — neither from `agentkit.runtime` nor from the top-level `agentkit` namespace and its `__all__` (`src/agentkit/__init__.py`).
- `src/coding_agent/adapter/pipeline.py` and `src/coding_agent/wire/protocol.py`: preserve current transport, wire, and `StopReason` behavior through the adapter, projecting coordinator `CommittedFactNotice`s to the existing `ToolCallDelta` and `ToolResultDelta` events.

Phase D — internal coordinator WAL implementation (non-shipping):

- `src/agentkit/runtime/hook_runtime.py`, `src/agentkit/plugin/registry.py`, and `src/agentkit/tools/toolset.py`: enforce plugin capability isolation (pending facts / `EffectPlan` / observer) and install the `SegmentCoordinator` WAL path with no automatic retry after an indeterminate dispatch.
- `src/coding_agent/plugins/core_tools.py`, `src/coding_agent/plugins/semantic_memory.py`, and `src/coding_agent/core/app.py`: migrate the coding_agent plugins onto the same boundary in this phase. Tool plugins become schema/`EffectPlan` producers only; `Environment` and the executable file, shell, web, and subagent functions move behind the host `EffectExecutor`; explicitly approved read-only grounding inputs (the semantic-memory review/topic stores) become immutable host-provided inputs rather than plugin-held stores.
- `src/coding_agent/server/session/persist.py` and the approval persistence tests (`tests/coding_agent/test_harness_p2_wrap.py`, `tests/coding_agent/test_harness_p2_fact_source.py`): migrate the live `settled` effect writer to the explicit legal transitions, then remove the `settled` alias and rank-based replacement from `src/coding_agent/stores/rtstore/harness.py`, `src/coding_agent/stores/local_durable/uow.py`, and `src/coding_agent/stores/pg_durable/uow.py` atomically with the coordinator cutover.
- `src/coding_agent/approval/runtime_messages.py`, `src/coding_agent/server/session/approval.py`, and `src/coding_agent/server/session/turn.py`: migrate the live approval and subagent command publishers/consumers onto the durable `CommandMailbox`. Approval decisions and subagent commands must be admitted through the mailbox, and the approval disposition plus the `prepared -> dispatched|rejected` transition must commit in the same fenced unit of work, before the legacy in-memory runtime-message path is removed.
- This phase is internal and ships no public release gate.

Phase E — durable host ports:

- `src/coding_agent/stores/rtstore/harness.py`, `src/coding_agent/stores/local_durable/uow.py`, `src/coding_agent/stores/pg_durable/uow.py`, `src/coding_agent/stores/pg_durable/sql_harness.py`, and the required PG schema/row-codec paths: implement the host `CommitPort` with the same fenced unit-of-work contract for SQLite and PostgreSQL. Both backends must land before Phase F; a SQLite-only `CommitPort` does not satisfy the gate. `src/coding_agent/executors/` and `src/coding_agent/runs/turn_execution.py` adapt local and remote tool execution behind `EffectExecutor`; execution requires a coordinator-issued `DispatchPermit`.

Phase F — canonical EventRecord and integration (first stable release gate):

- `src/coding_agent/events/connected_chat.py` and the run/event producers: route every durable semantic fact, including finalized thinking produced for product display, through the proposal and unit of work into `EventRecord`; keep `StreamFrame` ephemeral.
- `src/coding_agent/server/session/persist.py` and `src/coding_agent/server/session/turn.py`: cut over the wire-to-fact path. Wire delivery of a `CommittedFactNotice` publishes already-committed facts and never writes another `EventRecord`; live token/thinking `StreamFrame`s never enter `persist_chat_wire_message`; final assistant/thinking/tool facts are written only by the proposal/settlement unit of work; the UUID-based duplicate fact writes in the compatibility projection path are removed or bypassed.
- This phase completes integration and is the first stable release gate. The gate includes the Option B checkpoint rejection tests (deterministic rejection with zero mutation on both backends) and the ADR-0077 connected-chat focused and release/postmortem aggregates listed in the Acceptance Criteria.

Phase G — RestorePoint / projection-epoch cutover:

- Re-express checkpoint capture and restore on `OperationStateVersion`/`commit_ref` and ADR-0076 projection epochs, reusing ADR-0075 supersession fields. The restore orchestration and runtime-builder boundary spans `src/coding_agent/runs/checkpoint_runtime.py`, `src/coding_agent/runs/runtime_checkpoint_restore.py`, `src/coding_agent/runs/checkpoint_restore.py`, `src/coding_agent/harness/restore.py`, `src/coding_agent/server/session/restore.py`, and the checkpoint stores `src/coding_agent/stores/local_durable/checkpoint.py` and `src/coding_agent/stores/pg_durable/checkpoint.py`, gated by their existing tests (`tests/coding_agent/test_checkpoint_restore_service.py`, `tests/coding_agent/test_checkpoint_runtime_builder.py`, `tests/coding_agent/test_runtime_checkpoint_capture_service.py`, `tests/coding_agent/test_runtime_checkpoint_restore_service.py`). `RestorePoint` wire, GC, and UI are later work.
- Capture and restore-side injection are separate cutover steps. Migrate or retire the capture contract: `src/coding_agent/runs/checkpoint_capture.py`, `src/agentkit/checkpoint/service.py`, `src/agentkit/checkpoint/models.py`, `src/agentkit/checkpoint/serialize.py`, and the snapshot codec/store paths (`src/agentkit/storage/sqlite.py`, `src/agentkit/storage/checkpoint_fs.py`). `src/coding_agent/runs/checkpoint_runtime.py` is the restore-side injection only. Neither serialized Tape nor `plugin_states` may remain a hidden new-runtime checkpoint input.
- Remove the `PipelineContext.plugin_states` capture/injection path — capture currently lives in `src/coding_agent/runs/checkpoint_capture.py` and `src/agentkit/checkpoint/service.py`, and restore-side injection in `src/coding_agent/runs/checkpoint_runtime.py`. Restore rebuilds runtime state only from the restored `OperationStateVersion` value, committed `EventRecord` facts, and host-provided immutable inputs; no hidden mutable plugin-state restore path may remain.
- Committed `EventRecord` `projection_epoch` is immutable. Remove the idempotent-commit epoch promotion update in both backends (`src/coding_agent/stores/local_durable/uow.py`; `_PROMOTE_SESSION_EVENT_EPOCH_SQL` in `src/coding_agent/stores/pg_durable/sql_harness.py` and its call site in `src/coding_agent/stores/pg_durable/uow.py`). A same-epoch lost-ack retry returns the stored original commit before the CAS check and before any write; a retry after restore opened a new epoch fails the state-version CAS without writing anything.
- Preserve ADR-0010 (synchronize checkpoint restore with active turns): restore remains serialized with active turns through the per-session turn lock, and hot provider reuse is allowed only when `provider_name`, `model_name`, and `base_url` all match the persisted session metadata that built the hot runtime.
- When the new capture/restore contract lands and the SQLite/PostgreSQL parity and crash tests pass, Phase G re-enables checkpoint capture and restore for new-runtime sessions under the new format version; the Option B rejection remains only for cross-version capture/restore.

Phase H — package split:

- Extract the host-neutral engine/coordinator package boundary after the Phase F stable gate. Not designed in this record.

Legacy `Pipeline` removal scheduling is out of scope for this record.

## Acceptance Criteria

Implementation is pending; these intended tests and commands gate the work.

- [ ] `test_agent_engine_is_persistence_free_and_cannot_execute_effectful_tools`
- [ ] `test_engine_and_plugins_receive_no_store_executor_mailbox_cursor_or_dispatch_capability`
- [ ] `test_model_adapter_is_host_provided_and_distinct_from_effect_executor`
- [ ] `test_final_model_response_and_finalized_thinking_reenter_proposal_as_pending_facts`
- [ ] `test_segment_coordinator_sequences_commit_port_and_effect_executor`
- [ ] `test_stream_frame_carries_only_ephemeral_token_thinking_heartbeat`
- [ ] `test_phase_c_adapter_preserves_wire_events_and_stop_reasons`
- [ ] `test_phase_c_adapter_hides_pipeline_context_from_public_api`
- [ ] `test_uow_commits_state_facts_dispositions_and_effect_ledger_atomically_sqlite`
- [ ] `test_uow_commits_state_facts_dispositions_and_effect_ledger_atomically_postgresql`
- [ ] `test_uow_commits_finalized_thinking_event_record_with_transition`
- [ ] `test_cas_conflict_or_failure_writes_no_part_of_transition`
- [ ] `test_host_allocates_continuous_session_sequences`
- [ ] `test_commit_ref_is_transition_anchor_not_session_log_head`
- [ ] `test_mailbox_admission_advances_session_seq_without_engine_revision`
- [ ] `test_command_survives_control_probe_loss`
- [ ] `test_dispositions_commit_atomically_with_transition`
- [ ] `test_rejected_disposition_requires_stable_reason_code`
- [ ] `test_superseded_disposition_requires_superseded_by_command_id`
- [ ] `test_absent_command_disposition_remains_pending_and_no_deferred_exists`
- [ ] `test_effect_executor_not_called_before_durable_dispatch_authorization`
- [ ] `test_crash_after_dispatch_marks_unknown_without_automatic_retry`
- [ ] `test_unknown_effect_blocks_until_reconciliation_or_explicit_decision`
- [ ] `test_same_run_takeover_fences_old_owner_with_new_owner_epoch`
- [ ] `test_crash_alone_does_not_write_interrupted`
- [ ] `test_phase_g_restore_serializes_with_active_turn_lock`
- [ ] `test_phase_g_restore_reuses_provider_only_when_provider_model_and_base_url_match`
- [ ] `test_transition_proposal_next_action_is_explicit_union`
- [ ] `test_engine_step_input_is_a_consume_once_union_with_stable_input_identity`
- [ ] `test_engine_step_reentry_model_only_commits_assistant_and_finalized_thinking_facts`
- [ ] `test_engine_step_reentry_model_with_tool_produces_pending_facts_and_effect_plan`
- [ ] `test_engine_step_reentry_effect_settlement_commits_tool_result_fact_and_next_model_request`
- [ ] `test_engine_step_reentry_approval_denial_commits_tool_result_fact_and_continues_loop`
- [ ] `test_segment_outcome_is_a_discriminated_union_with_exact_fields`
- [ ] `test_segment_outcome_maps_to_compatibility_stop_reasons`
- [ ] `test_control_probe_cancellation_during_generate_discards_uncommitted_partial_output_and_returns_safe_yield`
- [ ] `test_durable_cancel_disposition_returns_cancelled_segment_outcome`
- [ ] `test_frame_sink_and_fact_sink_delivery_failure_does_not_roll_back_durable_work`
- [ ] `test_commit_port_results_distinguish_committed_exact_replay_cas_conflict_stale_owner_stale_mailbox_cut_invalid_transition_and_storage_failure`
- [ ] `test_effect_executor_result_distinguishes_completed_failed_and_indeterminate_dispatch`
- [ ] `test_cancellation_token_after_dispatch_cannot_claim_non_execution`
- [ ] `test_model_adapter_generate_returns_model_generation_result`
- [ ] `test_agent_engine_performs_no_adapter_io`
- [ ] `test_segment_coordinator_owns_loop_and_settlement_reentry_until_limit`
- [ ] `test_frame_sink_emit_await_bounds_backpressure`
- [ ] `test_frame_sink_failure_disables_ephemeral_frames_without_failing_segment`
- [ ] `test_control_probe_polled_at_all_safe_points`
- [ ] `test_raised_probe_causes_safe_yield_and_command_remains_pending`
- [ ] `test_cancellation_token_from_control_probe_stops_live_model_stream`
- [ ] `test_approval_wait_allocates_effect_id_commits_prepared_and_yields_blocked_without_permit`
- [ ] `test_approved_response_dispositions_prepared_to_dispatched_before_permit_issue`
- [ ] `test_rejected_response_dispositions_prepared_to_rejected_and_no_permit_exists`
- [ ] `test_approval_denial_commits_exactly_one_tool_result_and_reaches_next_model_round_without_permit`
- [ ] `test_approval_facts_dispositions_and_effect_identity_survive_crash_reentry`
- [ ] `test_approve_resume_cannot_redispatch_dispatched_or_unknown_effect`
- [ ] `test_manual_retry_creates_new_linked_attempt_with_idempotency_or_quiescence_evidence`
- [ ] `test_unknown_to_terminal_requires_fenced_reconciliation_record`
- [ ] `test_redispatch_never_reuses_unknown_attempt_identity`
- [ ] `test_dispatch_permit_is_opaque_single_use_and_bound_to_session_attempt_transition_and_epoch`
- [ ] `test_takeover_blocks_negative_reconciliation_and_new_permits_until_executor_quiescent`
- [ ] `test_old_owner_settlements_remain_fenced`
- [ ] `test_stale_mailbox_cut_rejects_dispatch_authorization_sqlite`
- [ ] `test_stale_mailbox_cut_rejects_dispatch_authorization_postgresql`
- [ ] `test_control_command_admission_between_probe_and_dispatch_authorization_is_fenced_by_mailbox_cut_sqlite`
- [ ] `test_control_command_admission_between_probe_and_dispatch_authorization_is_fenced_by_mailbox_cut_postgresql`
- [ ] `test_committed_fact_notice_emitted_only_after_prepared_and_settlement_commits`
- [ ] `test_phase_c_adapter_maps_notices_to_tool_call_and_tool_result_deltas`
- [ ] `test_phase_f_notice_carries_or_references_committed_event_record_identity`
- [ ] `test_uncommitted_proposal_never_projects_tool_wire_events`
- [ ] `test_committed_fact_notice_delivery_is_ordered_after_commits`
- [ ] `test_committed_fact_notice_sink_failure_disables_notices_and_reports_host_delivery_error_without_mutating_committed_facts`
- [ ] `test_committed_fact_notice_backpressure_is_independent_of_frame_sink`
- [ ] `test_committed_event_record_projection_epoch_is_immutable_sqlite`
- [ ] `test_committed_event_record_projection_epoch_is_immutable_postgresql`
- [ ] `test_lost_ack_same_epoch_retry_returns_original_commit_sqlite`
- [ ] `test_lost_ack_same_epoch_retry_returns_original_commit_postgresql`
- [ ] `test_lost_ack_then_restore_retry_fails_state_version_cas_without_writing_sqlite`
- [ ] `test_lost_ack_then_restore_retry_fails_state_version_cas_without_writing_postgresql`
- [ ] `test_transition_receipt_is_first_write_wins_keyed_by_session_projection_epoch_and_transition_id`
- [ ] `test_transition_receipt_same_epoch_retry_returns_stored_commit_before_cas_and_before_any_write_sqlite`
- [ ] `test_transition_receipt_same_epoch_retry_returns_stored_commit_before_cas_and_before_any_write_postgresql`
- [ ] `test_transition_receipt_fingerprint_mismatch_fails_deterministically`
- [ ] `test_transition_receipt_retry_after_later_transition_writes_nothing`
- [ ] `test_phase_b_keeps_settled_alias_while_live_writers_remain`
- [ ] `test_phase_d_removes_settled_alias_and_rank_replacement_atomically_with_cutover`
- [ ] `test_sqlite_and_postgresql_both_pass_before_phase_f_stable`
- [ ] `test_agentkit_runtime_exports_exclude_pipeline_and_pipeline_context`
- [ ] `test_phase_g_restore_never_injects_pipeline_context_plugin_states`
- [ ] `test_no_hidden_mutable_plugin_state_restore_path_remains`
- [ ] `test_approval_and_subagent_command_publishers_admit_through_command_mailbox_before_legacy_path_removal`
- [ ] `test_approval_disposition_and_prepared_transition_commit_in_same_fenced_uow`
- [ ] `test_committed_fact_notice_wire_delivery_never_writes_another_event_record`
- [ ] `test_live_token_and_thinking_stream_frames_never_enter_persist_chat_wire_message`
- [ ] `test_phase_f_final_facts_are_written_only_by_proposal_or_settlement_uow`
- [ ] `test_phase_f_compatibility_projection_removes_uuid_based_duplicate_fact_writes`
- [ ] `test_new_runtime_checkpoint_capture_rejected_with_checkpoint_capture_not_supported_for_runtime_version`
- [ ] `test_new_runtime_checkpoint_restore_rejected_with_checkpoint_restore_not_supported_for_runtime_version`
- [ ] `test_new_runtime_checkpoint_rejection_commits_zero_mutation_sqlite`
- [ ] `test_new_runtime_checkpoint_rejection_commits_zero_mutation_postgresql`
- [ ] `test_checkpoint_format_version_binds_legacy_checkpoints_to_matching_legacy_runtime_sessions`
- [ ] `test_cross_version_checkpoint_capture_and_restore_are_rejected`
- [ ] `test_old_tape_pipeline_context_restore_is_forbidden_on_new_runtime`
- [ ] `test_phase_g_checkpoint_capture_and_snapshot_codec_paths_are_migrated_or_retired`
- [ ] `test_agentkit_public_exports_exclude_pipeline_and_pipeline_context`
- [ ] `uv run pytest tests/agentkit/runtime/test_pipeline.py tests/agentkit/runtime/test_runtime_messages.py tests/agentkit/tools/test_toolset.py -k "agent_engine or segment_coordinator or stream_frame or frame_sink or control_probe or cancellation_token or next_action or model_generation or adapter_io or phase_c_adapter or capability or model_adapter or finalized_thinking or reentry or public_exports or engine_step_input or segment_outcome or commit_port or effect_executor or committed_fact_notice or runtime_exports" -v`
- [ ] `uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_harness_p2_wrap.py tests/coding_agent/test_harness_p2_fact_source.py -k "uow or cas or disposition or owner_epoch or session_seq or commit_ref or effect_id or approval or settled_alias or permit or reconciliation or lost_ack or projection_epoch or manual_retry or old_owner_settlements or phase_f_stable or mailbox_cut or transition_receipt or not_supported_for_runtime_version" -v`
- [ ] `uv run pytest tests/coding_agent/test_runtime_run_recovery.py tests/coding_agent/test_runtime_wire_event_recorder.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py -k "unknown or dispatch_authorization or owner_epoch or probe or phase_c or phase_g or stop_reason or crash_alone or finalized_thinking or restore_checkpoint or turn_lock or hot_provider or plugin_state or committed_fact_notice or uncommitted_proposal or not_supported_for_runtime_version or persist_chat_wire_message or phase_f" -v`
- [ ] `uv run pytest tests/coding_agent/test_checkpoint_restore_service.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/coding_agent/test_runtime_checkpoint_capture_service.py tests/coding_agent/test_runtime_checkpoint_restore_service.py -k "restore or checkpoint or projection_epoch or operation_state_version or plugin_state" -v`
- [ ] `uv run pytest tests/coding_agent/test_connected_chat_contract.py tests/coding_agent/test_connected_chat_projection.py tests/coding_agent/test_connected_chat_admission.py tests/ui/test_connected_chat_lifecycle.py tests/ui/test_connected_chat_follow.py tests/ui/test_connected_chat_http.py -q`
- [ ] `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py -k 'PM_0021 or PM_0022 or PM_0023 or registration or publication or ownership or teardown or connected_chat or clean or close or shut' -q`

## References

- `docs/adr/0001-checkpoint-captures-serialized-tape-and-plugin-state.md`
- `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
- `docs/adr/0006-checkpoint-plugin-state-restores-as-best-effort-hints.md`
- `docs/adr/0010-checkpoint-restore-contract-stays-narrow-and-explicit.md`
- `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0075-checkpoint-restore-active-run-timeline.md`
- `docs/adr/0076-harness-control-plane.md`
- `docs/adr/0077-connected-chat-session-event-projection.md`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/runtime/messages.py`
- `src/agentkit/runtime/hook_runtime.py`
- `src/agentkit/runtime/__init__.py`
- `src/agentkit/plugin/registry.py`
- `src/agentkit/providers/protocol.py`
- `src/agentkit/providers/models.py`
- `src/agentkit/tools/toolset.py`
- `src/agentkit/__init__.py`
- `src/agentkit/checkpoint/service.py`
- `src/agentkit/checkpoint/models.py`
- `src/agentkit/checkpoint/serialize.py`
- `src/agentkit/storage/sqlite.py`
- `src/agentkit/storage/checkpoint_fs.py`
- `src/coding_agent/adapter/pipeline.py`
- `src/coding_agent/adapter/types.py`
- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/local_durable/checkpoint.py`
- `src/coding_agent/stores/pg_durable/uow.py`
- `src/coding_agent/stores/pg_durable/sql_harness.py`
- `src/coding_agent/stores/pg_durable/checkpoint.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/plugins/semantic_memory.py`
- `src/coding_agent/core/app.py`
- `src/coding_agent/runs/checkpoint_capture.py`
- `src/coding_agent/runs/checkpoint_runtime.py`
- `src/coding_agent/runs/runtime_checkpoint_restore.py`
- `src/coding_agent/runs/checkpoint_restore.py`
- `src/coding_agent/harness/restore.py`
- `src/coding_agent/approval/runtime_messages.py`
- `src/coding_agent/server/session/approval.py`
- `src/coding_agent/server/session/persist.py`
- `src/coding_agent/server/session/restore.py`
- `src/coding_agent/server/session/turn.py`
- `src/coding_agent/events/connected_chat.py`
- `src/coding_agent/wire/protocol.py`
