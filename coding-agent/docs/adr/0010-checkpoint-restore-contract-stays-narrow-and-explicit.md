# ADR-0010: Checkpoint restore contract stays narrow and explicit

**Status**: Accepted
**Date**: 2026-04-16

## Context

ADR-0003 established that each HTTP session owns one stable tape timeline keyed by a persistent `tape_id`. ADR-0005 defined checkpoint restore as controlled truncate rollback on that stable timeline, and ADR-0006 narrowed plugin-state restore to best-effort hints rather than full live-runtime rehydration.

Those decisions are correct, but they can still be misread if the surrounding wording treats “append-only” as a global restore invariant or treats fork/audit as implied current behavior. We need one explicit contract boundary that tells readers what is current behavior and what remains a future extension path.

## Decision

`coding_agent` treats checkpoint restore as product-layer policy on top of AgentKit primitives.

- Normal turn execution remains append-only on the active stable timeline.
- `restore(checkpoint_id)` performs controlled truncate rollback in the same session and on the same stable `tape_id`.
- Checkpoint `plugin_states` restore only as best-effort pre-mount hints.
- Audit/history retention and alternate exploration are explicit future extension paths and must be introduced as new capabilities, not by broadening `restore(...)`.

This keeps the public contract narrow while leaving the underlying framework primitives reusable for future policies.

## Alternatives Rejected

- Treat restore as global append-only with a logical cursor — rejected because it misstates the current behavior and pushes extra timeline complexity into the main contract.
- Treat fork/audit as implied current behavior — rejected because it conflates future extension work with the accepted restore contract.
- Move restore semantics into agentkit as a framework rule — rejected because restore policy belongs in `coding_agent`, not in the lower-level mechanism layer.

## Acceptance Criteria

- [ ] `docs/AGENTKIT-ARCHITECTURE.md` scopes append-only to normal forward execution and explains product-layer restore policy.
- [ ] `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md` links the stable timeline identity to checkpoint restore semantics.
- [ ] `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md` uses `restore(checkpoint_id)` and `tape_id` consistently.
- [ ] `docs/adr/0006-checkpoint-plugin-state-restores-as-best-effort-hints.md` keeps plugin-state restore narrowed to hints.
- [ ] `docs/specs/checkpoint-design.md` and `docs/specs/checkpoint-design-section2b.md` frame the accepted contract as historical/current source of truth and leave fork/audit as extension paths.
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py -k "checkpoint_restore or truncate or stable_tape_id or plugin_state" -v`

## References

- [`docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`](./0003-http-sessions-use-one-stable-tape-timeline.md)
- [`docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`](./0005-checkpoint-restore-uses-truncate-rollback.md)
- [`docs/adr/0006-checkpoint-plugin-state-restores-as-best-effort-hints.md`](./0006-checkpoint-plugin-state-restores-as-best-effort-hints.md)
- [`docs/AGENTKIT-ARCHITECTURE.md`](../AGENTKIT-ARCHITECTURE.md)
- [`docs/specs/checkpoint-design.md`](../specs/checkpoint-design.md)
- [`docs/specs/checkpoint-design-section2b.md`](../specs/checkpoint-design-section2b.md)
- [`src/coding_agent/ui/session_manager.py`](../../src/coding_agent/ui/session_manager.py)
