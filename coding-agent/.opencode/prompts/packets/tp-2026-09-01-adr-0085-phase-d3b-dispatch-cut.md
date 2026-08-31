# Task Packet

packet_id: tp-2026-09-01-adr-0085-phase-d3b-dispatch-cut
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: 14c37a102667ec4ab159be3dab9406a7b89f312e
branch: feat/adr-0084-phase-d3b-activation

## Goal

Implement ADR-0085 Phase D3b only: add the host-private expected mailbox-cut precondition to typed dispatch-authorization units of work on SQLite and PostgreSQL, without activating the new runtime path.

## Scope

- Add host-private `AuthoritativeUnitOfWork.expected_mailbox_cut`.
- Require the field exactly for typed `PREPARED -> DISPATCHED` effect mutations; forbid it elsewhere.
- Parse the field as u64 and reject missing, malformed, out-of-range, or misplaced values with `InvalidDispatchAuthorizationError` before storage mutation.
- Preserve the field in `snapshot_transition_unit` and include it in `transition_mutation_fingerprint`, outside `EffectMutation.payload`.
- Add `StaleMailboxCutError` carrying expected/current cuts for direct durable-store callers.
- In both durable backends, perform exact receipt replay/fingerprint conflict handling first, then compare the locked `session_fact_source.dispatch_generation` to the expected cut before state CAS or any write.
- A stale refusal writes no state, fact, disposition, effect mutation, sequence, or transition receipt.
- Permit the same transition identity to retry with a new cut only after a typed stale zero-write/no-receipt refusal. The first successful receipt fixes the identity/cut fingerprint.

## Non-goals

- No concrete `CommitPort.authorize_dispatch` implementation or result mapping.
- No coordinator stale-retry changes.
- No approval, child, or subagent publisher/consumer cutover.
- No production `PREPARED -> DISPATCHED` caller.
- No effect-writer migration.
- Do not remove `settled`, effect ranks, or the legacy terminal writer.
- No D4 reconciliation, E ports/child wrappers, F runtime versioning/projection/activation, or G/H work.
- No Phase C public API changes.
- Do not add mailbox cut to `EffectMutation.payload`.

## Allowed production files

- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/pg_durable/uow.py`

Allowed tests:

- `tests/coding_agent/test_runtime_phase_b_uow.py`
- `tests/coding_agent/test_sqlite_local_durable_fencing.py`
- `tests/coding_agent/test_pg_durable_fencing.py`
- `tests/coding_agent/test_harness_p2_fact_source.py`

## Acceptance criteria

- `test_dispatch_authorization_requires_mailbox_cut`
- `test_mailbox_cut_is_forbidden_outside_dispatch_authorization`
- `test_dispatch_authorization_cut_changes_mutation_fingerprint`
- `test_dispatch_authorization_exact_replay_precedes_newer_cut_sqlite`
- `test_dispatch_authorization_exact_replay_precedes_newer_cut_postgresql`
- `test_stale_dispatch_authorization_writes_nothing_sqlite`
- `test_stale_dispatch_authorization_writes_nothing_postgresql`
- `test_stale_dispatch_authorization_writes_no_receipt`
- `test_fresh_cut_can_commit_after_stale_zero_write_refusal`
- Approval allow does not advance its own D3a cut; denial/cancel/interrupt admitted between probe and authorization make the old cut stale.
- Existing non-dispatch typed transitions and legacy units of work remain byte-for-byte behaviorally compatible.
- Existing production runtime builders still use the legacy path; D3b creates no serving caller.

## Verification

```bash
uv run pytest tests/coding_agent/test_runtime_phase_b_uow.py -q
uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py -q
uv run pytest tests/coding_agent/test_harness_p2_fact_source.py -q
uv run pytest tests/agentkit/ tests/coding_agent/ -q
uv run ruff check <changed-python-files>
uv run ruff format --check <changed-python-files>
```

## Loop policy

One bounded review/fix/retest cycle. Stop and return to ADR-0085 if implementation requires a Phase C signature change, a production caller, coordinator behavior, effect alias removal, runtime-version routing, or any serving activation.
