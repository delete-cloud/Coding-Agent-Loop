# ADR-0010: Synchronize checkpoint restore with active turns and guard hot provider reuse

**Status**: Accepted
**Date**: 2026-04-16

## Context

Checkpoint restore mutates the same session runtime state that `run_agent()` mutates: the runtime pipeline, runtime context, runtime adapter, stable tape id, and persisted session metadata.

`run_agent()` already serializes the full turn with a per-session turn lock. Before this decision, `restore_checkpoint()` only checked `turn_in_progress` / `task.done()` and then mutated the same state without acquiring that lock. That left a race where a turn could start after the check but before or during restore, which can corrupt the runtime replacement and tape truncation sequence.

Checkpoint restore also reuses an in-memory provider instance when a hot session already has one. Provider instances are created with restart-safe settings such as `model_name` and `base_url` baked into the instance. Reusing that instance based on `provider_name` alone is unsafe if the checkpoint rewinds model or endpoint configuration.

Not every provider implementation exposes a stable public `base_url` attribute, so the reuse gate must compare against the persisted session metadata that created the hot runtime, not against an arbitrary provider instance field.

This decision refines the restore semantics already established by:
- ADR-0003: HTTP sessions use one stable tape timeline
- ADR-0004: Cold restore provides degraded continuity
- ADR-0005: Checkpoint restore uses truncate rollback
- ADR-0006: Checkpoint plugin state restores as best-effort hints

## Decision

Checkpoint restore must be serialized with active turns using the same per-session turn lock as `run_agent()`.

`restore_checkpoint()` must:
1. acquire the per-session turn lock,
2. re-check that no turn is active while holding the lock,
3. refuse restore with `RuntimeError("turn already in progress")` if the session is busy,
4. continue with `_restore_checkpoint()` only after the lock is held.

Provider reuse during restore is allowed only when the restart-safe session metadata matches the restored checkpoint config. Reuse is safe only if all of the following match:
- `provider_name`
- `model_name`
- `base_url`

The `base_url` comparison uses the persisted session metadata (the session value that was used to build the hot runtime), not a provider-instance property.

If any of those values differ, the hot provider instance must be treated as stale and cleared so the next turn rebuilds runtime state from the restored configuration.

`session_restart_config` remains a reserved checkpoint metadata key and must not be accepted from caller-provided `extra`.

## Alternatives Rejected

- Check `turn_in_progress` only, without locking — still racy; restore and turn execution can overlap after the check.
- Allow restore to run while turns execute — risks concurrent runtime replacement and tape truncation.
- Reuse hot provider instances based only on `provider_name` — unsafe when restore rewinds model or endpoint settings.
- Require a new framework-level provider restore protocol — too much surface for the current runtime model.
- Always discard hot provider instances — simpler, but throws away safe continuity and hot-path reuse.
- Store live provider instances in checkpoint snapshots — violates checkpoint isolation and would not be serializable.

## Acceptance Criteria

- [ ] `test_restore_checkpoint_rejects_active_turn`
- [ ] `test_restore_checkpoint_rejects_when_turn_lock_is_held`
- [ ] `test_restore_rewinds_restart_safe_session_configuration_from_checkpoint_extra`
- [ ] `test_restore_clears_hot_provider_override_when_checkpoint_rewinds_provider_metadata`
- [ ] `test_restore_does_not_reuse_hot_provider_when_model_changes_with_same_provider`
- [ ] `test_restore_does_not_reuse_hot_provider_when_base_url_changes_with_same_provider`
- [ ] `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py -k "restore_checkpoint or hot_provider or session_restart_config" -v`

## Implementation Plan

- **`src/coding_agent/ui/session_manager.py`**
  - Wrap `restore_checkpoint()` in the per-session turn lock.
  - Re-check active-turn state inside the lock.
  - Reuse `session.provider` only when restart-safe metadata matches the restored checkpoint config.
  - Clear stale `session.provider` when provider name, model, or base URL diverge.
- **`tests/ui/test_session_manager_public_api.py`**
  - Keep/extend coverage for active-turn rejection and turn-lock rejection.
- **`tests/ui/test_session_manager_runtime.py`**
  - Keep/extend coverage for provider reuse, provider clearing, and metadata rewind.
- **Verification**
  - `lsp_diagnostics` on touched files must report zero errors.
  - Focused pytest command above must pass.

## References

- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_session_manager_runtime.py`
- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0004-cold-restore-provides-degraded-continuity.md`
- `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
- `docs/adr/0006-checkpoint-plugin-state-restores-as-best-effort-hints.md`
