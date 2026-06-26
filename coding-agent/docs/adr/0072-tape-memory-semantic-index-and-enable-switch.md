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

The semantic vector index is a derived Coding Agent layer over authoritative
topic and review stores. It is not a tape primitive and must never become the
authority for memory text, provenance, review state, or lifecycle. Retrieval
must rehydrate semantic hits from the authoritative topic/review stores before
rendering. A missing topic/review, rejected, archived, or no-longer-accepted
memory, or any stale source drops the hit and may delete or schedule deletion
of the stale semantic document. Rendering must never use `MemoryHit.text`
directly as the user-facing memory body.

`[memory.semantic]` is a nested extension under the existing `[memory]` config
section. It defaults disabled and must not perturb the existing
`enabled`/`read_enabled`/`write_enabled` defaults or semantics.

The semantic index must not bypass the current recall-safe-text guard. Text
accepted by the vector backend must pass the same `_FORBIDDEN_TEXT_MARKERS`
boundary, or an equivalent shared guard, before indexing and before query
construction. Raw tape content, raw prompts, stdout/stderr, command output,
logs, env dumps, and secrets must never become vector-index payloads.
Index only finalized topic summaries and accepted reviewed memories. Sync and
rebuild paths must apply the safe-text guard immediately before embedding and
upsert.

Each semantic backend must persist enough schema identity to detect unsafe
reuse: schema version, embedding provider id, embedding model, vector
dimension, backend adapter id, backend schema/table format version, distance
metric, and score-normalization mapping. A mismatch must fail clearly or
require an explicit rebuild.

Hybrid result merging must be deterministic. When deterministic topic recall
and semantic recall both return the same memory, the merger deduplicates by a
stable memory identity such as memory id or source refs and emits that memory
once. Semantic recall is additive/refill: deterministic `TopicRangeIndex` hits
that pass filters remain included up to their configured limit. Semantic scores
may order semantic-only candidates and annotate or dedupe overlaps, but they
must not displace deterministic results solely because the vector score is
higher unless a later ADR changes ranking policy. `MemoryHit.score` is
normalized high-is-better across all vector backends, even when the backend's
native metric is a low-is-better distance.

Define only a small generic `MemoryIndex` / `MemoryHit` protocol and generic
models in AgentKit, analogous to the existing storage protocols such as
`DocIndex` and `ArtifactStore`. AgentKit must stay minimal: do not add
Chroma, Milvus, pgvector, provider-specific names, or operational backend
methods to `agentkit.storage.protocols.MemoryIndex`. Operational methods such
as `ensure_schema`, scoped `list_ids`, and `delete_scope` belong in Coding
Agent backend policy. Concrete embedding providers, vector backends such as
LanceDB, ranking/reranking, and KB-to-tape-memory result merging remain Coding
Agent plugin policy.

Semantic source references and backend scopes must use closed grammars with
validation before list or delete operations. Scoped deletion must be isolated
by construction: deleting one scope must not delete documents from another
profile, session, source kind, or source id.

Do not push product-specific memory types into AgentKit. `TopicDerivedMemoryCandidate`,
`TopicRangeIndex`, review states, Bee fields, profile fields, topic provenance,
and context-pack rendering semantics stay in `coding_agent`.

Full topic enumeration for sync/rebuild must be unbounded/paginated or use a
dedicated full-scan API, not an arbitrary small limit. A manifest cache may
accelerate sync, but is never the authority. Full rebuild must be idempotent.
Sync entrypoints and triggers include manual rebuild, startup reconciliation,
and post-finalize/post-review-change sync.

Destructive full rebuilds must be backed by the complete authoritative source
set before they clear or replace the derived semantic backend. The required
authorities are finalized topics from a durable `TopicStore` and accepted
reviewed memories from the review store. A deployment path that does not expose
a durable topic authority, including the current local SQLite durable bundle
before a `SQLiteTopicStore` exists, must fail the rebuild loudly before
clearing semantic documents. Read-only maintenance status may still report
`topic_store_available = false`, but that status is not permission to rebuild
from a partial source set.

Reviewed-memory transition APIs are session-scoped product APIs. The HTTP
surface is `POST /sessions/{session_id}/memory/reviews/{candidate_id}` with a
target status of `accepted`, `rejected`, or `archived` and an optional safe
reason. It must authorize through the visible session, ensure that session's
runtime, and read the review store/service from that runtime context. When the
semantic review sync service exists, the API uses it so transitions update the
semantic index. When semantic memory is disabled and only the review store
exists, the same session-scoped API may transition the local store directly.
The review store transition is authoritative; semantic sync is a derived
follow-up. Invalid or raced store transitions return 400, while failures during
semantic sync return 500 because the authoritative review state may already
have changed and the derived index is now stale. Same-status terminal replays
are idempotent repair requests: they must not mutate the review store or
overwrite the original review reason, but may rerun semantic sync to repair a
stale derived index. Terminal records with a different target status still
return 400. There is no global review-store mutation endpoint.

PR2 must create a reusable backend contract test suite against a fake or
in-memory semantic backend. LanceDB, Chroma, Milvus, pgvector, or any later
adapter must run the same suite when added.

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

Implementation is staged. The read/write switch, semantic backend contract,
LanceDB backend, sync/review transition wiring, and maintenance rebuild guard
have started landing across PRs. The checklist records the required proof for
accepting this ADR; checked items are landed and were re-run during this ADR
update, while unchecked items remain required by the ADR even if later
verification finds that some have already landed.

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
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_schema_identity_mismatch_fails_clearly`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_schema_identity_mismatch_requires_explicit_rebuild`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_disabled_semantic_config_does_not_initialize_provider_or_backend`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_disabled_semantic_config_does_not_require_credentials`
- [ ] `tests/coding_agent/test_memory_switch.py::test_nested_semantic_config_preserves_existing_memory_switch_defaults`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_source_grammar_rejects_invalid_sources`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_scope_grammar_rejects_invalid_scopes`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_scoped_delete_cannot_delete_across_scopes`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_full_topic_scan_handles_more_than_100_topics`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_stale_hit_is_dropped_during_rehydration`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_rehydration_never_renders_memory_hit_text_directly`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_ranking_is_additive_refill`
- [ ] `tests/coding_agent/test_semantic_tape_index.py::test_memory_hit_score_is_normalized_high_is_better`
- [ ] `tests/agentkit/storage/test_protocols.py::test_memory_index_protocol_stays_provider_agnostic`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_sync_indexes_only_finalized_topic_summaries`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_sync_indexes_only_accepted_reviewed_memories`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_sync_skips_candidate_rejected_and_archived_memories`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_manifest_cache_is_not_sync_authority`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_full_rebuild_is_idempotent`
- [ ] `tests/coding_agent/test_semantic_sync.py::test_manual_rebuild_startup_and_event_triggers_share_sync_contract`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_scans_topic_store_in_pages`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_requires_topic_store_before_clearing`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_rejects_duplicate_topic_scan`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_status_counts_documents_and_review_states`
- [ ] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_fake_backend_registers_plugin_and_exposes_index_by_default`
- [ ] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_with_read_disabled_exposes_index_without_registering_plugin`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_accept_candidate_updates_semantic_index`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_reject_candidate_deletes_stale_semantic_index`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_missing_candidate_returns_404_without_index_side_effects`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_terminal_transition_returns_400_without_index_side_effects`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_same_status_terminal_transition_resyncs_existing_record`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_unsafe_reason_returns_400_without_store_or_index_side_effects`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_disabled_updates_review_store_directly`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_sync_failure_returns_500`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_transition_race_value_error_returns_400`
- [ ] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_sync_value_error_after_transition_returns_500`
- [ ] `tests/coding_agent/test_memory_index_backend_contract.py::test_fake_backend_satisfies_memory_index_contract`
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
