# ADR-0072: Tape-native memory — semantic index v0 and read/write enable switch

**Status**: Accepted
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

Decision B: semantic tape index v0 is an optional, opt-in embedding/vector
retrieval backend that coexists with `TopicRangeIndex`.
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
a durable topic authority, such as custom, mixed, or non-durable storage modes
with no selected durable `TopicStore`, must fail the rebuild loudly before
clearing semantic documents. Read-only maintenance status may still report
`topic_store_available = false`, but that status is not permission to rebuild
from a partial source set.

Semantic maintenance HTTP APIs are product-layer operational APIs, not AgentKit
primitives. Because full rebuild clears and replaces a shared derived semantic
backend from global authoritative stores, the rebuild surface is admin-only and
must not be authorized solely by session ownership. The first HTTP surface is
session-anchored only to reuse runtime/config selection:
`GET /sessions/{session_id}/memory/semantic/status` and
`POST /sessions/{session_id}/memory/semantic/rebuild`. Both endpoints require
admin auth when auth is configured. The status endpoint is read-only and must
not repair, rebuild, clear, or mutate the backend.

The rebuild endpoint is an explicit destructive maintenance action. HTTP must
call a `SessionManager` maintenance method, not construct or call
`SemanticMemoryMaintainer.rebuild()` directly. That manager method must run
through `RuntimeMaintenanceAdmissionService.run_exclusive()` so active turns,
owner assertion, and fencing conflicts are checked before the derived semantic
backend is cleared. Active-turn admission failures return 409 and must leave
the backend untouched. Missing selected durable `TopicStore` and disabled
semantic memory also return conflict-style operational errors before any clear
or upsert. The `allow_rebuild` request field controls backend schema rebuild
permission; it is not a confirmation flag for ordinary destructive document
clearing. Because rebuild clears the selected semantic backend from global
authoritative topic/review sources, callers must also send an explicit global
confirmation (`confirm_global: true`) and CLI callers must use
`--confirm-global`. Responses identify the rebuild `scope` as `global` so
operators do not confuse the session path parameter with a session-local
reindex.

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

The stable read surface for reviewed-memory candidates is
`GET /sessions/{session_id}/memory/reviews`. It uses normal visible-session
authorization rather than admin authorization, reads the current session
runtime config's `MemoryReviewStore`, and returns only records scoped to that
session plus legacy records with no session provenance. The optional `status`
filter accepts `candidate`, `accepted`, `rejected`, or `archived`. This is a
read API only; state transitions remain on the candidate-specific POST API.

Ordinary remote execution paths are not guaranteed to create durable finalized
topics. For dogfood and recovery, the product exposes an admin-only maintenance
seed API at `POST /sessions/{session_id}/memory/semantic/dogfood-topic`. The
route verifies admin auth and visible-session access, then delegates to
`SessionManager`; route code must not write topic, review, semantic, or tape
stores directly. The manager method runs through
`RuntimeMaintenanceAdmissionService.run_exclusive()`, resolves the current
runtime tape/config, selected durable `TopicStore`, runtime `MemoryReviewStore`,
effective memory write flag, and configured semantic syncer, then creates and
finalizes one topic through `TopicLifecycle`. Tape anchors are committed by a
`ForkTapeStore` delta commit to the existing tape store; append-only stores must
not receive a full tape snapshot for this maintenance seed. After commit, the
live runtime context's tape is updated to the committed stable tape so later
turns see the anchors.

The implementation includes a reusable backend contract test suite against the
fake semantic backend and the LanceDB adapter. Chroma, Milvus, pgvector, or any
later adapter must run the same suite when added.

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

Implementation has landed across the read/write switch, semantic backend
contract, LanceDB backend, sync/review transition wiring, maintenance rebuild
guard, dogfood maintenance surface, Helm opt-in config, and semantic/KB recall
ordering. The checklist records the executable proof used to accept this ADR.
Production deployments, including o6n, still have to opt in explicitly through
`[memory.semantic]`; acceptance of this ADR does not enable semantic memory by
default or add vector adapters beyond the current fake and LanceDB backends.

- [x] `tests/coding_agent/test_memory_switch.py::test_read_off_suppresses_grounding_injection`
- [x] `tests/coding_agent/test_memory_switch.py::test_recall_planner_enabled_is_derived_from_effective_read`
- [x] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_turn_end_memory_record`
- [x] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_topic_end_long_term_memory`
- [x] `tests/coding_agent/test_memory_switch.py::test_write_off_suppresses_topic_finalization_review_candidate`
- [x] `tests/coding_agent/test_memory_switch.py::test_memory_off_leaves_tape_entries_intact`
- [x] `tests/coding_agent/test_memory_switch.py::test_memory_switch_truth_table`
- [x] `tests/coding_agent/test_memory_switch.py::test_kb_toggle_is_independent_from_memory_toggle`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_forbidden_text_before_indexing`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_forbidden_text_before_query_embedding`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_hybrid_merge_is_deterministic_and_dedups`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_schema_identity_mismatch_fails_clearly`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_schema_identity_mismatch_requires_explicit_rebuild_and_clears_stale_docs`
- [x] `tests/coding_agent/test_memory_switch.py::test_disabled_semantic_config_does_not_initialize_provider_or_backend` covers disabled semantic config without credentials.
- [x] `tests/coding_agent/test_memory_switch.py::test_nested_semantic_config_preserves_existing_memory_switch_defaults`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_invalid_source_ref_before_indexing`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_semantic_index_rejects_backend_hit_with_invalid_source_ref`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_source_scope_grammar_rejects_invalid_scopes`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_scoped_delete_cannot_delete_across_scopes`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_lancedb_scoped_delete_cannot_delete_across_scopes`
- [x] `tests/coding_agent/test_semantic_sync.py::test_full_topic_scan_handles_more_than_100_topics`
- [x] `tests/coding_agent/test_semantic_recall.py::test_stale_semantic_topic_doc_id_is_dropped`
- [x] `tests/coding_agent/test_semantic_recall.py::test_missing_unfinalized_no_summary_and_source_topic_hits_are_dropped`
- [x] `tests/coding_agent/test_semantic_recall.py::test_accepted_memory_hit_is_rehydrated_and_other_statuses_are_dropped`
- [x] `tests/coding_agent/test_semantic_recall.py::test_semantic_topic_hit_is_rehydrated_from_authoritative_topic`
- [x] `tests/ui/test_session_manager_runtime.py::test_sqlite_durable_semantic_rebuild_recalled_by_later_build_context_without_backend_hit_text`
- [x] `tests/coding_agent/test_semantic_recall.py::test_deterministic_topics_stay_first_and_semantic_hits_refill_after`
- [x] `tests/coding_agent/test_semantic_recall.py::test_semantic_refill_does_not_expand_beyond_configured_limit`
- [x] `tests/coding_agent/test_semantic_tape_index.py::test_hybrid_merge_keeps_deterministic_topics_before_high_score_semantic_refill`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_memory_hit_score_is_normalized_high_is_better_by_result_ordering`
- [x] `tests/agentkit/storage/test_protocols.py::test_memory_index_protocol_stays_provider_agnostic`
- [x] `tests/coding_agent/test_semantic_sync.py::test_sync_indexes_only_finalized_topic_summaries`
- [x] `tests/coding_agent/test_semantic_sync.py::test_sync_indexes_only_accepted_reviewed_memories`
- [x] `tests/coding_agent/test_semantic_sync.py::test_sync_skips_candidate_rejected_and_archived_memories`
- [x] `tests/coding_agent/test_semantic_sync.py::test_manifest_cache_is_not_sync_authority`
- [x] `tests/coding_agent/test_semantic_sync.py::test_full_rebuild_is_idempotent`
- [x] `tests/coding_agent/test_semantic_sync.py::test_manual_rebuild_startup_and_event_triggers_share_sync_contract`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_scans_topic_store_in_pages`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_requires_topic_store_before_clearing`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_rebuild_rejects_duplicate_topic_scan`
- [x] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_status_counts_documents_and_review_states`
- [x] `tests/ui/test_session_manager_runtime.py::test_rebuild_semantic_memory_uses_maintenance_admission_and_indexes_sources`
- [x] `tests/ui/test_session_manager_runtime.py::test_rebuild_semantic_memory_active_turn_leaves_backend_untouched`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_status_requires_admin`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_status_returns_counts_without_mutating_backend`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_rebuild_requires_admin`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_rebuild_returns_report`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_rebuild_active_turn_returns_409_without_clearing_backend`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_rebuild_missing_topic_store_returns_409_without_clearing_backend`
- [x] `tests/ui/test_http_server.py::TestSemanticMemoryMaintenance::test_semantic_rebuild_owner_conflict_maps_to_http_conflict`
- [x] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_fake_backend_registers_plugin_and_exposes_index_by_default`
- [x] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_with_read_disabled_exposes_index_without_registering_plugin`
- [x] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_lancedb_backend_uses_configured_local_path`
- [x] `tests/coding_agent/test_memory_switch.py::test_semantic_enabled_lancedb_backend_uses_configured_embedding_schema`
- [x] `tests/deploy/test_helm_chart.py::test_helm_default_config_does_not_render_or_enable_memory_semantic`
- [x] `tests/deploy/test_helm_chart.py::test_helm_semantic_memory_enabled_renders_config`
- [x] `tests/deploy/test_helm_chart.py::test_helm_semantic_memory_runs_before_kb_when_both_enabled`
- [x] `tests/deploy/test_helm_chart.py::test_helm_semantic_memory_enabled_bootstraps_runtime`
- [x] `tests/deploy/test_helm_chart.py::test_helm_kb_enabled_can_defer_when_semantic_memory_hits`
- [x] `tests/coding_agent/test_semantic_recall.py::test_current_query_prefers_recent_semantic_topic_over_stale_deterministic_topic`
- [x] `tests/coding_agent/test_semantic_recall.py::test_derived_current_query_prefers_recent_semantic_topic`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_accept_candidate_updates_semantic_index`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_reject_candidate_deletes_stale_semantic_index`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_missing_candidate_returns_404_without_index_side_effects`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_terminal_transition_returns_400_without_index_side_effects`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_same_status_terminal_transition_resyncs_existing_record`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_unsafe_reason_returns_400_without_store_or_index_side_effects`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_disabled_updates_review_store_directly`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_sync_failure_returns_500`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_transition_race_value_error_returns_400`
- [x] `tests/ui/test_http_server.py::TestMemoryReviewTransitions::test_semantic_sync_value_error_after_transition_returns_500`
- [x] `tests/coding_agent/test_memory_index_backend_contract.py::test_fake_backend_satisfies_memory_index_contract`
- [x] `uv run pytest -q`

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
