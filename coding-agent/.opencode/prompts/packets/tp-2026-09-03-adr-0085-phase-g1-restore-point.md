# Task Packet

packet_id: tp-2026-09-03-adr-0085-phase-g1-restore-point
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: 1ebe91f9efc2e777c731ab3abf8d179a3b775
branch: feat/adr-0085-phase-g-checkpoint

## Goal

Implement ADR-0083/0085 Phase G1: new-runtime checkpoint capture and restore as a RestorePoint over `OperationStateVersion` / `commit_ref` / committed `EventRecord` facts / effect+mailbox / projection epoch. Re-enable capture/restore for matching `agentkit-1` sessions only. Keep Option B rejection for cross-version and for Tape/`plugin_states` inputs on new-runtime.

## Scope

### Phase boundary

- Legacy sessions keep ADR-0001/0005/0006/0010 Tape + `plugin_states` capture/restore unchanged.
- New-runtime sessions (`runtime_version=agentkit-1`) capture and restore only the new RestorePoint format.
- Cross-version capture/restore still reject before mutation (`legacy` checkpoint onto `agentkit-1` session and the reverse).
- Do not flip the production activation flag.
- Do not start Phase H (legacy pipeline deletion).
- Do not remove the idempotent-commit epoch promotion SQL in this packet (G2). Capture/restore must still open a new projection epoch on restore, matching current restore behavior.

### RestorePoint format

New-runtime `CheckpointSnapshot` must satisfy:

- `checkpoint_format` (in `extra` or an equivalent persisted field) = `agentkit-1`
- `tape_entries` empty
- `plugin_states` empty
- RestorePoint payload includes:
  - `operation_state_version`
  - `commit_ref` (or equivalent durable commit identity already stored)
  - `projection_epoch` at capture
  - mailbox / effect-ledger pointers needed to resume
- Capture of non-empty `tape_entries` or `plugin_states` for a new-runtime session rejects before mutation.

### Capture

- `save_checkpoint` for `agentkit-1` sessions is allowed when the snapshot is a RestorePoint.
- HTTP `POST /sessions/{id}/checkpoints` succeeds for new-runtime sessions and returns checkpoint metadata.
- Daemon still rejects before mutation when the session/checkpoint versions do not match.

### Restore

- `restore_checkpoint_state` for `agentkit-1` rebuilds from RestorePoint + committed `EventRecord` facts + host immutable inputs.
- Restore remains serialized with active turns (ADR-0010 per-session turn lock).
- Hot provider reuse only when `provider_name`, `model_name`, and `base_url` all match persisted session metadata.
- Restore marks superseded runs without deleting them (ADR-0075). Resume still creates a new linked run (ADR-0055); do not invent a second lineage scheme.
- After restore, a same-epoch lost-ack retry must not rewrite committed facts; a retry after restore opened a new epoch fails the state-version CAS without writing.

### Activation / errors

- Replace the Phase F blanket `NewRuntimeCheckpointRejectedError` for matching new-runtime RestorePoints.
- Keep the same error type (or a dedicated cross-version error) for mismatched runtime/format.
- Error strings for remaining rejections stay stable enough that existing cross-version tests can be updated in place.

## Out of scope

- Phase G2: remove `_PROMOTE_SESSION_EVENT_EPOCH_SQL` / SQLite epoch promotion update; make `projection_epoch` immutable on committed `EventRecord`.
- Phase H package split / legacy pipeline deletion.
- Production `new_sessions_enabled` flag flip.
- AgentKit frozen request/proposal/result/outcome contracts.
- Independent child workers.
- CLI `daemon runtime-activation` operator command.

## Context

- ADRs:
  - `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`
  - `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md` (Phase G)
  - `docs/adr/0075-checkpoint-restore-active-run-timeline.md`
  - `docs/adr/0055-and-fork-tape-memory-semantic-index.md` (resume lineage; reuse, do not replace)
  - `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md` (if present; otherwise the synchronize-restore ADR retained by 0083)
- Relevant files:
  - `src/coding_agent/runtime_activation.py`
  - `src/coding_agent/stores/local_durable/checkpoint.py`
  - `src/coding_agent/stores/pg_durable/checkpoint.py`
  - `src/coding_agent/runs/checkpoint_capture.py`
  - `src/coding_agent/runs/checkpoint_restore.py`
  - `src/coding_agent/runs/checkpoint_runtime.py`
  - `src/coding_agent/runs/runtime_checkpoint_restore.py`
  - `src/coding_agent/harness/restore.py`
  - `src/coding_agent/server/session/restore.py`
  - `src/coding_agent/server/http/routes/checkpoints.py`
  - `src/agentkit/checkpoint/models.py`
  - `tests/coding_agent/test_phase_f_checkpoint_rejection.py`

## Target tests

- `uv run pytest tests/coding_agent/test_phase_f_checkpoint_rejection.py -v`
- `uv run pytest tests/coding_agent/test_checkpoint_restore_service.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/coding_agent/test_runtime_checkpoint_restore.py -v`
- New G1 tests (names may land in `tests/coding_agent/test_phase_g_restore_point.py`):
  - `test_new_runtime_restore_point_capture_persists_on_sqlite_and_pg`
  - `test_new_runtime_restore_point_restore_rebuilds_session_without_plugin_states`
  - `test_new_runtime_rejects_tape_or_plugin_state_snapshot`
  - `test_cross_version_checkpoint_still_rejects_before_mutation`
  - `test_legacy_checkpoint_capture_and_restore_unchanged`
- `uv run pytest tests/coding_agent/test_phase_f_checkpoint_rejection.py tests/coding_agent/test_phase_g_restore_point.py tests/coding_agent/test_checkpoint_restore_service.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/coding_agent/test_runtime_checkpoint_restore.py -v`

## Loop policy

- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

## Stop conditions

- At most one review/fix/retest cycle.
- Escalate if restore requires mutating AgentKit Tape as a physical session log, or if capture cannot be expressed without `plugin_states`.
- Escalate if G1 cannot land without the G2 immutable-epoch SQL change.
- Ignore non-blocking optimization suggestions.
