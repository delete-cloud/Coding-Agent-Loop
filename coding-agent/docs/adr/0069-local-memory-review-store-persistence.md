# ADR-0069: Persist local memory review records

**Status**: Accepted
**Date**: 2026-06-13

## Context

ADR-0046 defines topic-derived memory candidates, review states, accepted memory,
and reference-only recall rendering. The current `MemoryReviewStore` keeps the
complete candidate -> accepted/rejected/archived lifecycle in memory, so reviewed
records disappear when the process exits.

`recall_context.py` already consumes accepted memories through
`accepted_memory_context_pack`. The missing boundary is durable local storage for
the review store, not a new recall planner or external memory backend.

## Decision

Persist `MemoryReviewStore` records as local JSONL at
`{data_dir}/{kb.db_path}/reviewed_memory.jsonl`. Each line is one
`ReviewedMemoryRecord.to_dict()` payload. The store loads this file during
initialization and rewrites it after candidate creation and after accept, reject,
or archive transitions.

Keep this as a Coding Agent product-layer local store. It does not introduce
Postgres, nmem, hosted vector stores, or AgentKit Core memory primitives. Accepted
records loaded from JSONL are available to the existing recall context path as
reference-only memory.

Topic finalization may add a derived memory candidate to a configured
`MemoryReviewStore` after the topic closes successfully. This does not auto-accept
memory; candidates still require the review lifecycle defined in ADR-0046.

## Alternatives Rejected

- Keep memory review in process memory only. Rejected because accepted memories
  disappear across process restarts and cannot reliably feed recall.
- Persist only accepted records. Rejected because candidate inbox state would be
  lost before review.
- Add a Postgres-backed review store now. Rejected because this local agent phase
  only needs deterministic local persistence; a future remote deployment can add
  another store implementation.
- Change recall planner behavior. Rejected because accepted memory rendering is
  already wired through `recall_context.py`.

## Acceptance Criteria

- [x] `test_memory_review_store_persists_records_as_jsonl`
- [x] `test_memory_review_store_loads_accepted_memory_for_recall`
- [x] `test_create_agent_installs_persistent_memory_review_store`
- [x] `test_finalize_topic_adds_memory_candidate_to_review_store`
- [x] `uv run pytest tests/coding_agent/test_memory_review.py tests/coding_agent/test_recall_context.py tests/coding_agent/test_topic_lifecycle.py tests/coding_agent/test_bootstrap.py -k "memory_review or accepted_memory or finalize_topic_adds" -v`

## References

- `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- `src/coding_agent/topic_memory.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/recall_context.py`
- `src/coding_agent/app.py`
