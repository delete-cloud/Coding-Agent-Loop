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
external KB RAG already has embeddings, LanceDB, and hybrid vector/full-text
retrieval in `src/coding_agent/kb/rag.py`, exposed through the independent
`src/coding_agent/plugins/kb.py` build-context path. Tape memory needs an
optional semantic index that can reuse that product-layer machinery without
replacing deterministic topic range search or moving product concepts into
AgentKit.

## Decision

Decision A: add a config-level `[memory]` section in `agent.toml` with separate
read and write switches. The fields are `enabled`, `read_enabled`, and
`write_enabled`, all defaulting to `true`. Effective behavior is:
`effective_read = enabled && read_enabled` and
`effective_write = enabled && write_enabled`.

Memory off is not tape off. Disabling memory must not disable tape persistence,
checkpointing, session logging, durable fencing, or session/runtime replay.
When effective read is false, memory recall and grounding injection are skipped
in `build_context`. When effective write is false, memory candidate production
is skipped in `on_turn_end`. Tape entries are still written.

The existing `TopicRecallPlannerInput.enabled` remains the read-half planning
gate for topic recall rendering. The new switch generalizes that behavior into
a config surface and adds the write half. It must not be coupled to the external
KB/RAG toggle: "do not remember me" must not mean "do not read project docs".

The switch boundary follows the AgentKit mechanism / Coding Agent policy split.
A small generic settings dataclass may live in AgentKit if hook plumbing needs
one, but the `[memory]` entry, persistence of that choice, defaults, and any UI
or CLI surface stay in `coding_agent`.

Decision B: after the switch PR, add semantic tape index v0 as an optional
embedding/vector retrieval backend that coexists with `TopicRangeIndex`.
Semantic retrieval is hybrid support, not substitution. Deterministic topic
range search remains available and continues to provide stable provenance,
filtering, and redaction behavior. This does not supersede ADR-0046: the
deterministic index defined there stays the default recall path, and the
semantic backend is additive and opt-in.

The semantic index must not bypass the current recall-safe-text guard. Text
accepted by the vector backend must pass the same `_FORBIDDEN_TEXT_MARKERS`
boundary, or an equivalent shared guard, before indexing and before query
construction. Raw tape content, raw prompts, stdout/stderr, command output,
logs, env dumps, and secrets must never become vector-index payloads.

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
- [ ] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_candidate_production`
- [ ] `tests/coding_agent/test_memory_switch.py::test_memory_off_leaves_tape_entries_intact`
- [ ] `tests/coding_agent/test_memory_switch.py::test_kb_toggle_is_independent_from_memory_toggle`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_tape_index_v0_contract_preserves_safe_hybrid_retrieval`
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
