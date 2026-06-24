# ADR-0072: Tape-native memory — semantic index v0 and read/write enable switch

**Status**: Proposed
**Date**: 2026-06-24

## Context

Tape-native memory is not missing. `src/coding_agent/plugins/memory.py` already
wires the read and write halves through AgentKit hooks: `build_context` injects
evidence-backed memory references and `on_turn_end` produces `MemoryRecord`
directives from the current tape. `MemoryRecord` is an AgentKit directive in
`src/agentkit/directive/types.py`, and `src/agentkit/runtime/hookspecs.py` plus
`src/agentkit/runtime/pipeline.py` are the hook surface where `build_context`
and `on_turn_end` fire.

The topic-derived memory layer also already exists. `src/coding_agent/topics/recall.py`
scores topic recall with `RecalledTopic`; `src/coding_agent/topics/recall_context.py`
plans recall through `TopicRecallPlannerInput`; `src/coding_agent/topics/range_index.py`
provides a deterministic `TopicRangeIndex` and rejects unsafe recall text through
`_FORBIDDEN_TEXT_MARKERS`; `src/coding_agent/topics/context_pack.py` renders
grounding through `ContextPackRenderer` and `EvidenceRef`; and
`src/coding_agent/topics/provenance.py` records provenance through
`TopicEntryRange`. ADR-0069 and `src/coding_agent/topics/memory.py` add durable
local review persistence for candidate, accepted, rejected, and archived memory
records.

The narrow gaps are configurability and optional semantic retrieval. Users need
to disable recall reads without disabling learning, disable learning without
disabling recall, or disable both without disabling tape persistence. Separately,
external KB RAG already has embeddings and LanceDB-backed vector search in the
independent `src/coding_agent/plugins/kb.py` build-context path.
`src/coding_agent/kb/rag.py` also contains a hybrid vector/full-text search
helper, but the current KB build-context path calls `search_sync`, not
`hybrid_search`. Tape memory needs an optional semantic index that can reuse
that product-layer machinery without replacing deterministic topic range search
or moving product concepts into AgentKit.

## Decision

Decision A: add a config-level `[memory]` section in `agent.toml` with separate
read and write switches. The fields are `enabled`, `read_enabled`, and
`write_enabled`, all defaulting to `true`. Effective behavior is:
`effective_read = enabled && read_enabled` and
`effective_write = enabled && write_enabled`.

Memory off is not tape off. Disabling memory must not disable tape persistence,
checkpointing, session logging, durable fencing, or session/runtime replay.
When effective read is false, memory recall and grounding injection are skipped
in `build_context`. The effective read value is the only source of truth for
topic recall planning: `TopicRecallPlannerInput.enabled` must be derived from
`effective_read`, not treated as an independent override or second config
surface.

When effective write is false, all long-term memory production paths are
skipped. That includes `on_turn_end` `MemoryRecord` directives, the
`on_session_event("topic_end")` compacted long-term memory write in
`src/coding_agent/plugins/memory.py`, and topic finalization candidate writes
through `_close_topic -> MemoryReviewStore.add_candidate` in
`src/coding_agent/topics/lifecycle.py`. Tape entries, topic anchors, and topic
records are still written.

`MemoryRecord` and `TopicDerivedMemoryCandidate` are different write surfaces:
`MemoryRecord` is an AgentKit directive emitted from turn-end hooks, while
`TopicDerivedMemoryCandidate` is Coding Agent topic policy emitted from topic
finalization or artifact review. The write switch covers both surfaces without
moving topic-specific memory types into AgentKit. It must not be coupled to the
external KB/RAG toggle: "do not remember me" must not mean "do not read project
docs".

The switch boundary follows the AgentKit mechanism / Coding Agent policy split.
A small generic settings dataclass may live in AgentKit if hook plumbing needs
one, but the `[memory]` entry, persistence of that choice, defaults, and any UI
or CLI surface stay in `coding_agent`.

Decision B: after the switch PR, add semantic tape index v0 as an optional,
opt-in embedding/vector retrieval backend that coexists with `TopicRangeIndex`.
Semantic retrieval is off by default until explicit product configuration
enables it. Semantic retrieval is hybrid support, not substitution.
Deterministic topic range search remains available without embedding
credentials and continues to provide stable provenance, filtering, and
redaction behavior. This does not supersede ADR-0046: the deterministic index
defined there stays the default recall path, and the semantic backend is
additive and opt-in.

The semantic index must not bypass the current recall-safe-text guard. Text
accepted by the vector backend must pass the same `_FORBIDDEN_TEXT_MARKERS`
boundary, or an equivalent shared guard, before indexing and before query
construction. Raw tape content, raw prompts, stdout/stderr, command output,
logs, env dumps, and secrets must never become vector-index payloads.

Hybrid result merging must be deterministic. When deterministic topic recall
and semantic recall both return the same memory, the merger deduplicates by a
stable memory identity such as memory id or source refs and emits that memory
once. The merged result must use a single deterministic ordering key, with the
`TopicRangeIndex` order as the tie-break for equal or missing semantic scores.
Provenance, filtering, and redaction are authoritative from the deterministic
path; semantic scores may influence product ranking policy only inside those
invariants.

Define only a small generic `MemoryIndex` / `MemoryHit` protocol and generic
models in AgentKit, analogous to the existing storage protocols such as
`DocIndex` and `ArtifactStore`. Concrete embedding providers, vector backends
such as LanceDB, ranking/reranking, and KB-to-tape-memory result merging remain
Coding Agent plugin policy.

Do not push product-specific memory types into AgentKit. `TopicDerivedMemoryCandidate`,
`TopicRangeIndex`, review states, Bee fields, profile fields, topic provenance,
and context-pack rendering semantics stay in `coding_agent`.

OpenDAL is out of scope for this decision. If it is introduced later, it can be
a `BlobStore`, artifact, or archive layer, but not a `TapeStore` replacement:
tape storage has ordered query, truncate, replay, and fencing semantics that a
blob abstraction does not provide.

## Alternatives Rejected

- Single `enabled` bool — rejected because it loses useful privacy modes such
  as recall-without-learn and learn-without-recall.
- Replace `TopicRangeIndex` with a pure vector index — rejected because that
  loses deterministic ordering, exact filters, stable provenance, and the
  existing redaction guard.
- Push topic and memory product models into AgentKit — rejected because it
  violates the mechanism/policy split in `docs/AGENTKIT-ARCHITECTURE.md`.
- Couple the KB toggle to the memory toggle — rejected because users may want
  "do not remember me" while still allowing project-document retrieval.
- Use OpenDAL as a `TapeStore` replacement — rejected because OpenDAL is a blob
  layer, while `TapeStore` has query, ordering, truncate, replay, and fencing
  semantics.

## Acceptance Criteria

Implementation is pending. The later PRs must add these tests and pass the
repo gate:

- [ ] `tests/coding_agent/test_memory_switch.py::test_read_off_suppresses_grounding_injection`
- [ ] `tests/coding_agent/test_memory_switch.py::test_recall_planner_enabled_is_derived_from_effective_read`
- [ ] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_turn_end_memory_record`
- [ ] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_topic_end_long_term_memory`
- [ ] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_topic_finalization_review_candidate`
- [ ] `tests/coding_agent/test_memory_switch.py::test_memory_off_leaves_tape_entries_intact`
- [ ] `tests/coding_agent/test_memory_switch.py::test_memory_switch_truth_table`
- [ ] `tests/coding_agent/test_memory_switch.py::test_kb_toggle_is_independent_from_memory_toggle`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_forbidden_text_before_indexing`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_forbidden_text_before_query_embedding`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_hybrid_merge_is_deterministic_and_dedups`
- [ ] `uv run pytest -q`

## References

- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- `docs/adr/0067-local-sqlite-durable-tape-runtime.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0069-local-memory-review-store-persistence.md`
- `docs/AGENTKIT-ARCHITECTURE.md`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/kb/rag.py`
- `src/coding_agent/topics/recall.py`
- `src/coding_agent/topics/recall_context.py`
- `src/coding_agent/topics/range_index.py`
- `src/coding_agent/topics/memory.py`
- `src/coding_agent/topics/context_pack.py`
- `src/coding_agent/topics/provenance.py`
- `src/agentkit/directive/types.py`
- `src/agentkit/storage/protocols.py`
- `src/agentkit/runtime/hookspecs.py`
- `src/agentkit/runtime/pipeline.py`
