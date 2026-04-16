# ADR-0005: Checkpoint restore uses truncate rollback

**Status**: Accepted
**Date**: 2026-04-13

## Context

Restoring a checkpoint needs one clear timeline policy. If restore keeps later tape entries around or creates a new tape id, the system ends up with ambiguous history semantics.

Checkpoint restore also has to keep the persisted tape and rebuilt runtime aligned so future commits do not append after stale history.

## Decision

Checkpoint restore uses controlled rollback on the existing stable `tape_id`.

In `coding_agent`, `restore(checkpoint_id)` means: stay in the same session, preserve the same stable `tape_id`, rebuild the runtime from the checkpoint snapshot, and continue future turns on that same active timeline from the restored point.

The current implementation realizes that contract by truncating the persisted tape to the checkpoint's `entry_count` and invalidating checkpoints that pointed past the restored point. Those details explain the current implementation, but the public contract is the same-session, same-stable-timeline controlled rollback semantics.

## Alternatives Rejected

- Restore by forking a new tape id — turns restore into branch creation instead of rollback.
- Keep later entries and only remember the checkpoint position logically — future commits would append after stale history.
- Restore from the current tape tail instead of the checkpoint snapshot — unsafe if persisted tape has already drifted or become inconsistent.

## Acceptance Criteria

- [ ] `test_restore_truncates_tape_store_to_checkpoint_entry_count`
- [ ] `test_restore_preserves_stable_tape_id`
- [ ] `test_restore_deletes_checkpoints_ahead_of_restore_point`
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py -k "checkpoint_restore or truncate or stable_tape_id" -v`

## References

- [`src/agentkit/storage/protocols.py`](../../src/agentkit/storage/protocols.py)
- [`src/coding_agent/plugins/storage.py`](../../src/coding_agent/plugins/storage.py)
- [`src/coding_agent/ui/session_manager.py`](../../src/coding_agent/ui/session_manager.py)
- Archived design context: [`docs/specs/checkpoint-design-section2b.md`](../specs/checkpoint-design-section2b.md)
