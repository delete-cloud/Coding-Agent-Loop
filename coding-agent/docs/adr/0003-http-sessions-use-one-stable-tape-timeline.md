# ADR-0003: HTTP sessions use one stable tape timeline

**Status**: Accepted
**Date**: 2026-04-13

## Context

The HTTP session path currently creates a fresh `Tape()` for each turn. That loses conversation continuity across turns, process restarts, and LRU eviction.

Checkpoint integration also depends on a session having one stable tape identity instead of a sequence of unrelated tapes.

## Decision

Each HTTP session owns one stable tape timeline keyed by a persistent `tape_id`.

Hot path requests reuse the in-memory pipeline, context, and adapter. Cold path recovery rebuilds a new runtime from persisted tape entries, but it still continues on the same stable `tape_id`.

## Alternatives Rejected

- Create a fresh tape for each HTTP turn — loses continuity immediately.
- Create a new tape id after cold recovery or eviction — breaks history lookup and checkpoint semantics.
- Store session history outside `TapeStore` — duplicates persistence responsibilities that already belong to the tape layer.

## Acceptance Criteria

- [ ] `test_run_agent_reuses_session_tape_id_across_hot_turns`
- [ ] `test_rehydrated_session_rebuilds_runtime_from_persisted_tape`
- [ ] `test_session_store_persists_tape_id_for_cold_recovery`
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py -k "tape_id or rehydrate" -v`

## References

- [`src/coding_agent/ui/session_manager.py`](../../src/coding_agent/ui/session_manager.py)
- [`src/coding_agent/plugins/storage.py`](../../src/coding_agent/plugins/storage.py)
- [`tests/ui/test_session_manager_runtime.py`](../../tests/ui/test_session_manager_runtime.py)
- [`tests/ui/test_session_manager_public_api.py`](../../tests/ui/test_session_manager_public_api.py)
- Archived design context: [`docs/specs/checkpoint-design-section2a.md`](../specs/checkpoint-design-section2a.md)
