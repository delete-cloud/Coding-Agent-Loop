# Task Packet

packet_id: tp-2026-09-01-adr-0085-phase-f-stable-gate
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: 34ed68ef6446d7e509678a1bfd5678aabd21f756
branch: feat/adr-0085-phase-f-stable-gate

## Goal

Implement ADR-0085 Phase F as the first serving gate for the new runtime: immutable session `runtime_version`, non-rolling activation, version-fenced writers, removal of the public `settled` alias and generic rank replacement for new-runtime sessions, new-runtime checkpoint rejection, root crash recovery from durable attempts, and SQLite/PostgreSQL coexistence proofs. Do not flip the durable activation flag in this PR.

## Scope

### Phase boundary

- Keep every Phase C AgentKit request, proposal, result, outcome, port signature, and export unchanged.
- Existing and migrated sessions remain `legacy`. Newly created sessions remain `legacy` until a durable activation flag is flipped after this code ships and the non-rolling barrier is executed.
- Child runs inherit the parent session `runtime_version`.
- Unknown versions fail closed before any store mutation on SQLite and PostgreSQL.
- Phases G/H stay out of this packet: no new checkpoint/restore contract, no legacy pipeline deletion.

### Runtime versioning

- Persist immutable `runtime_version` on the session row (`legacy` or the new-runtime version string defined in code, not a free-form client value).
- Every store writer loads and fences that version inside the same transaction as the write.
- Cross-version writes fail before mutation.
- Additive schema/backfill treats missing version as `legacy`.

### Activation barrier (code, not the flag flip)

- Add a durable new-session activation flag that is off by default after deploy.
- While off, `create_session` always writes `legacy`.
- While on, only new sessions receive the new runtime version; existing session versions never change.
- SQLite requires process restart across the barrier. PostgreSQL may run multiple F-capable daemons after the barrier, but no old binary may remain live.
- Rollback path in code: disable new-session creation of the new version, drain new-runtime sessions, restore legacy creation only after compatible daemons own the fleet. Do not mutate existing session versions.
- This PR ships the flag, fences, and tests. It does not enable the flag in production config.

### Legacy / new writer boundary

- Legacy sessions keep `PipelineAdapter`, `RuntimeMessageBus`, legacy checkpoints, and UUID-era wire/fact behavior.
- A narrow legacy-only prepared-to-terminal writer remains version-fenced for existing legacy sessions.
- At F, remove generic rank replacement and the public `settled` effect alias from new-runtime writers. New-runtime sessions cannot call the legacy terminal writer or write `settled`.
- New-runtime sessions use only the typed graph, durable mailbox, `SegmentCoordinator`, concrete CommitPorts from E, durable executors, and committed facts.
- Before activation, root startup recovery scans current-owner `DISPATCHED` attempts and reconstructs authorization-commit / process-crash recovery from durable attempt rows without any process-local `AuthorizationReplayMarker`.

### Checkpoint rejection

- New-runtime checkpoint capture and restore reject before any mutation until Phase G.
- Legacy sessions keep current checkpoint behavior.

### Approval / UI projection

- Committed facts are the only source. `CommittedFactNotice` is a wake hint.
- The projector is fenced by session owner/epoch and may run at least once.
- Each durable sink records an `event_id` receipt. Interaction/session projection and wire outbox writes are idempotent.
- The source cursor advances only after every required sink receipt commits.
- Takeover replays from the durable cursor; duplicate delivery creates no duplicate interaction or wire fact.
- UI reads durable interaction state. Wire delivery deduplicates by `event_id`.

## Out of scope

- Phase G checkpoint/restore contract.
- Phase H legacy pipeline removal.
- Flipping the production activation flag.
- Changing AgentKit frozen contracts.
- Independent child workers.

## Context

- ADRs:
  - `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`
  - `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`
  - `docs/adr/0077-connected-chat-session-event-projection.md`
- Postmortem:
  - `postmortem/patterns/PM-0028-settle-post-dispatch-outcomes-before-exit.md`
- Phase E landed in `34ed68ef` / PR #728.
- Relevant files:
  - `src/coding_agent/stores/rtstore/harness.py` (`settled` rank alias, UoW)
  - `src/coding_agent/stores/local_durable/uow.py`
  - `src/coding_agent/stores/pg_durable/uow.py`
  - `src/coding_agent/stores/durable_commit_port.py`
  - `src/coding_agent/runs/turn_execution.py`
  - `src/coding_agent/runs/child_execution.py`
  - `src/coding_agent/server/session/lifecycle.py`
  - `src/coding_agent/server/session/restore.py`
  - `src/coding_agent/events/connected_chat.py`
  - `src/agentkit/runtime/coordinator.py`

## Target tests

```bash
uv run pytest tests/coding_agent/test_phase_f_runtime_version.py tests/coding_agent/test_phase_f_activation.py tests/coding_agent/test_phase_f_checkpoint_rejection.py tests/coding_agent/test_phase_f_root_recovery.py -q
uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/coding_agent/test_durable_commit_ports.py tests/coding_agent/test_durable_effect_executor.py tests/coding_agent/test_owner_local_child_execution.py -q
uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or executor_exception or executor_task_cancellation" -q
uv run pytest tests/cli/ tests/coding_agent/test_package_import_contract.py -q
```

Frontend (if projector/wire changes):

```bash
pnpm --dir webui/app-next test
pnpm --dir webui/app-next typecheck
```

## Acceptance criteria

- `test_existing_and_migrated_sessions_remain_legacy`
- `test_new_sessions_stay_legacy_until_activation_flag`
- `test_new_sessions_receive_new_runtime_version_after_flag`
- `test_child_run_inherits_parent_runtime_version`
- `test_unknown_runtime_version_fails_closed_before_mutation_sqlite`
- `test_unknown_runtime_version_fails_closed_before_mutation_postgresql`
- `test_cross_version_write_fails_closed_before_mutation`
- `test_legacy_session_keeps_pipeline_adapter_and_message_bus`
- `test_new_runtime_cannot_write_settled_or_use_legacy_terminal_writer`
- `test_new_runtime_checkpoint_capture_rejects_before_mutation`
- `test_new_runtime_checkpoint_restore_rejects_before_mutation`
- `test_legacy_checkpoint_capture_and_restore_unchanged`
- `test_root_startup_recovers_dispatched_attempt_without_process_marker`
- `test_projector_takeover_replay_creates_no_duplicate_wire_event`
- `test_activation_rollback_restores_legacy_creation_without_mutating_versions`
- SQLite/PostgreSQL parity covers approval, child wait/cancel/recovery, reconciliation, projector receipts, activation rollback, and coexistence.
- PM-0028 focused regression above is green.

## Loop policy

- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

## Stop conditions

- At most one review/fix/retest cycle.
- Do not flip the durable activation flag in this PR.
- Escalate if activation would require a mixed-binary fleet or a rolling writer cutover.
- Ignore non-blocking optimization suggestions.
