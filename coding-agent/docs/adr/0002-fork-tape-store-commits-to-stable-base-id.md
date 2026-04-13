# ADR-0002: ForkTapeStore commits to stable base id

**Status**: Accepted
**Date**: 2026-04-13

## Context

`ForkTapeStore.commit()` currently persists deltas under the fork tape id. Because each turn creates a new fork, persisted history drifts across transient ids and no single tape id contains the full conversation.

That breaks any feature that expects one stable conversation timeline, including session continuity and checkpoint restore.

## Decision

`ForkTapeStore.commit()` persists deltas under the base tape's stable id, returns that stable id, and only finalizes bookkeeping after the backing store save succeeds.

After commit, the pipeline rebinds the working tape identity back to the stable base id so future turns continue on the same persisted timeline.

## Alternatives Rejected

- Persist under each fork id and reconstruct history later — makes normal tape load semantics indirect and fragile.
- Save under the base id but keep the working tape on the transient fork id — the next turn would drift back to unstable identities.
- Mark the fork finalized before save — a save failure would leave transaction bookkeeping in a corrupted state.

## Acceptance Criteria

- [ ] `test_commit_persists_delta_under_base_tape_id`
- [ ] `test_commit_returns_stable_base_tape_id`
- [ ] `test_second_commit_appends_to_same_base_tape_id`
- [ ] `uv run pytest tests/agentkit/tape/test_store.py -k "base_tape_id or stable_base_tape_id" -v`

## References

- [`src/agentkit/tape/store.py`](../../src/agentkit/tape/store.py)
- [`src/agentkit/runtime/pipeline.py`](../../src/agentkit/runtime/pipeline.py)
- [`tests/agentkit/tape/test_store.py`](../../tests/agentkit/tape/test_store.py)
- Archived design context: [`docs/specs/checkpoint-design-section2a.md`](../specs/checkpoint-design-section2a.md)
