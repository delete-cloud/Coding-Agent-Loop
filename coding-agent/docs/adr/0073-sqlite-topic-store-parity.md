# ADR-0073: Add SQLite TopicStore parity for local durable storage

**Status**: Proposed
**Date**: 2026-06-26

## Context

ADR-0039 keeps Topic as a Coding Agent product abstraction, not an AgentKit
primitive. ADR-0068 makes the local SQLite bundle the durable store for local
session, tape, checkpoint, runtime, and owner state, and requires protected
local mutations to check owner authority and target ownership in the same SQLite
transaction.

ADR-0072 establishes semantic tape memory as a derived cache. Destructive
semantic rebuilds must have the complete authoritative source set before
clearing the derived backend: durable finalized topics plus accepted reviewed
memories. If no complete durable TopicStore authority is selected, destructive
rebuild must fail before clearing semantic backend documents.

The current durable TopicStore authority is PostgreSQL-only.
`src/coding_agent/topics/store.py` provides `PGTopicStore`, and console topic
helpers in `src/coding_agent/server/http_server.py` still construct that store
directly for PG-backed deployments. Local SQLite durable deployments already
persist session, tape, checkpoint, and runtime state in `local.sqlite3`, but
they do not have durable topic tables or a selected durable topic store.

That gap prevents topic-backed semantic rebuild from becoming complete on
SQLite, o6n, and local durable deployments. This ADR clarifies ADR-0072 by
defining SQLite TopicStore parity and the product-layer selection boundary
required before local/o6n semantic maintenance can rely on durable topics. It
does not supersede ADR-0072 and does not mark ADR-0072 accepted.

## Decision

No implementation starts until this ADR draft and the matching task packet pass
P1/P2 review. This ADR may move from `Proposed` to `Accepted` only after the
implementation PR proves the acceptance criteria below. The repository supports
only `Proposed`, `Accepted`, and `Superseded` ADR statuses.

Add SQLite-backed TopicStore parity for the local SQLite durable path. The store
and its selection are Coding Agent product-layer policy; TopicStore selection is
not an AgentKit core primitive.

Add one product-layer selected store surface, such as
`SessionManager.selected_topic_store()`:

- local SQLite durable bundle selects `SQLiteTopicStore` on the same normalized
  `local.sqlite3` file used for session, tape, checkpoint, runtime, and owner
  state;
- PostgreSQL durable mode selects the existing `PGTopicStore` from the existing
  PostgreSQL pool;
- custom, non-durable, and mixed storage modes select no durable TopicStore.

When no selected durable TopicStore exists, console topic helpers may continue
to fall back to sanitized run metadata for read-only inspection, but semantic
destructive rebuild remains unavailable and must fail before clearing the
semantic backend. Console helpers must use the product selected-store surface
instead of constructing `PGTopicStore` directly.

Add a lower-level `SQLiteTopicStore` in the Coding Agent topic layer with the
same model and validation semantics as `PGTopicStore`. The SQLite schema owns:

- `topics`
- `topic_anchors`
- `topic_recall_links`
- `topic_costs`

SQLite metadata values are stored as deterministic JSON text and must not
require the SQLite JSON extension. SQLite datetimes are serialized as
UTC-aware, fixed-width text that sorts lexicographically for `(created_at,
topic_id)` cursor pagination and round-trips to aware `datetime` values. Use a
format equivalent to `YYYY-MM-DDTHH:MM:SS.ffffffZ`.

Every SQLite connection involved in topic operations must enable
`PRAGMA foreign_keys=ON`. The schema must enforce parent topic existence for
anchors, recall links, and cost rows, and must cascade deletes where the
PostgreSQL schema cascades today. It must enforce one open topic per
`(session_id, tape_id)` with a partial unique index where supported; if the
runtime SQLite build cannot support that index, enforce the same invariant with
an equivalent transactional guard.

Selected local durable topic mutations are fenced to the local durable
owner/session/tape authority. They are not global, unfenced writes. Implement
mutating operations as methods on `SQLiteLocalDurableStore` or as a
`FencedSQLiteTopicStore` that delegates to durable-store transactional methods.
The fenced mutations include:

- `create_topic`
- `finalize_topic`
- `abort_topic`
- `record_topic_anchor`
- `record_recall_link`
- `update_topic_cost`

Each fenced mutation must verify active owner authority and that the target
session, tape, topic, anchor, recall link, or cost belongs to the same local
durable authority inside the protected transaction. The owner of session A must
not be able to mutate topics, anchors, recall links, or costs that belong to
session B. Stale owner authority must reject every fenced mutating operation.
Read and list operations may use direct SQLite reads because they do not mutate
protected durable state.

Thread the selected durable TopicStore into runtime creation, not just onto
`SessionManager`. `create_agent()` already accepts `semantic_topic_store`; the
normal `ensure_session_runtime` path, the local-daemon runtime branch in
`LocalDaemonRuntimePreparationService.build_runtime()`, and checkpoint restore
runtime creation must pass the selected SQLite or PG store through the existing
runtime agent factory. Tests must use fake `create_agent_fn` call capture to
prove the selected store reaches `semantic_topic_store` for all three paths.

Add a product-level semantic maintainer factory/service that derives semantic
maintenance dependencies from product configuration and the selected store, such
as `SessionManager.semantic_memory_maintainer()`. Callers must not rely on ad
hoc unit-level `SemanticMemoryMaintainer(...)` construction as the product path.

If `[memory.semantic]` is disabled, the factory must return a clear unavailable
result or raise a clear unavailable error. It must not enable semantic memory
implicitly. If semantic memory is enabled and a selected durable TopicStore
exists, `status()` reports `topic_store_available=true` and `rebuild()` indexes
finalized topics plus accepted reviewed memories. If no selected durable
TopicStore exists, destructive rebuild still fails before clearing the semantic
backend.

Do not let the current PG fake create false parity. Split topic-store
verification into SQL/schema-shape checks for `PGTopicStore` SQL where useful
and shared behavioral contract tests that run against both:

- `PGTopicStore` through a stricter fake that models real PG constraints;
- `SQLiteTopicStore` through a real temporary SQLite database.

The stricter PG fake/harness must model one open topic per `(session_id,
tape_id)`, FK-like parent existence for anchors, recall links, and costs, and
cascade behavior where the contract depends on it. Adjust the existing cost
aggregate test so it creates the parent topic first.

Add an integration-style SQLite upgrade test through `SQLiteLocalDurableStore`
or `SessionManager`: seed an existing local bundle with session, tape,
checkpoint, runtime, and `session_tapes` data; initialize the upgraded bundle;
assert existing data survives and topic tables/indexes exist.

For Helm and o6n, the default chart with SQLite storage derives the selected
TopicStore from the existing `[storage.paths].local` path. No new topic backend
configuration is required. `[memory.semantic]` remains disabled by default and
is rendered only when explicitly configured.

The implementation PR must also update `docs/topic_layer/USAGE.md` and the
architecture docs from PG-only topic storage to a SQLite/PG split before this
ADR can be accepted. SQLite is the durable TopicStore for single-replica
local/o6n usage; PG remains the durable TopicStore for multi-instance
deployments. These docs are part of the acceptance gate, not a separate
follow-up after the implementation is otherwise done.

No vector backend expansion is part of this decision. The semantic index remains
a derived cache over the selected durable sources.

## Alternatives Rejected

- Keep durable topics PostgreSQL-only - rejected because local SQLite/o6n
  deployments would continue to have durable tape/runtime state without a
  complete durable source set for semantic rebuild.
- Add a separate topic backend config for Helm defaults - rejected because the
  local SQLite durable bundle already has a single source-of-truth path,
  `storage.paths.local`, and topic storage should derive from that bundle.
- Use an unfenced standalone SQLite topic store for mutations - rejected because
  it would bypass the owner/session/tape authority required by ADR-0068.
- Move TopicStore selection into AgentKit - rejected because topics, review
  memories, semantic maintenance, Bee preparation, and console views are Coding
  Agent product policy.
- Fold vector backend work into this ADR - rejected as scope creep. This ADR is
  only about durable topic authority parity for SQLite and PostgreSQL.

## Acceptance Criteria

Implementation is pending. These checks define the gate for accepting this ADR
after the implementation PR lands.

- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_create_is_idempotent_by_topic_id`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_rejects_duplicate_open_topic_for_session_tape`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_rejects_orphan_anchor_recall_and_cost_records`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_cascades_child_rows_with_parent_topic`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_cursor_requires_created_at_and_topic_id_together`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_cursor_paginates_by_created_at_topic_id_with_equal_timestamp_tiebreak`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_finalize_and_abort_only_open_topics`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_records_anchors_recall_links_and_cost_increments`
- [ ] `tests/coding_agent/test_topic_store_contract.py::test_topic_store_contract_validation_parity_for_safe_fields_and_ranges`
- [ ] `tests/coding_agent/test_topic_store.py::test_pg_topic_store_schema_has_required_topic_tables_indexes_and_constraints`
- [ ] `tests/coding_agent/test_topic_store.py::test_pg_topic_store_cost_aggregate_requires_parent_topic`
- [ ] `tests/coding_agent/test_sqlite_topic_store.py::test_sqlite_topic_store_enables_foreign_keys_on_each_connection`
- [ ] `tests/coding_agent/test_sqlite_topic_store.py::test_sqlite_topic_store_stores_metadata_as_json_text_without_json_extension`
- [ ] `tests/coding_agent/test_sqlite_topic_store.py::test_sqlite_topic_store_datetime_text_is_fixed_width_utc_and_round_trips_aware`
- [ ] `tests/coding_agent/test_sqlite_topic_store.py::test_sqlite_topic_store_schema_is_idempotent`
- [ ] `tests/coding_agent/test_sqlite_local_durable_fencing.py::test_fenced_sqlite_topic_mutations_reject_stale_owner_for_all_mutators`
- [ ] `tests/coding_agent/test_sqlite_local_durable_fencing.py::test_fenced_sqlite_topic_mutations_reject_cross_session_targets_for_all_mutators`
- [ ] `tests/coding_agent/test_sqlite_local_durable_fencing.py::test_sqlite_local_durable_upgrade_preserves_existing_bundle_and_adds_topic_schema`
- [ ] `tests/ui/test_session_manager_runtime.py::test_selected_topic_store_returns_fenced_sqlite_store_for_local_durable_bundle`
- [ ] `tests/ui/test_session_manager_runtime.py::test_selected_topic_store_returns_pg_topic_store_for_pg_durable_mode`
- [ ] `tests/ui/test_session_manager_runtime.py::test_selected_topic_store_is_none_for_custom_or_mixed_storage`
- [ ] `tests/ui/test_session_manager_runtime.py::test_ensure_session_runtime_threads_selected_sqlite_topic_store_to_create_agent`
- [ ] `tests/ui/test_session_manager_runtime.py::test_ensure_session_runtime_threads_selected_pg_topic_store_to_create_agent`
- [ ] `tests/coding_agent/test_runtime_preparation.py::test_local_daemon_runtime_preparation_threads_selected_topic_store_to_create_agent`
- [ ] `tests/ui/test_session_manager_runtime.py::test_checkpoint_restore_threads_selected_topic_store_to_create_agent`
- [ ] `tests/ui/test_http_server.py::test_console_topic_helpers_use_selected_topic_store`
- [ ] `tests/ui/test_http_server.py::test_console_topic_helpers_fall_back_to_run_metadata_without_selected_topic_store`
- [ ] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_factory_unavailable_when_semantic_disabled`
- [ ] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_factory_reports_topic_store_available_when_selected`
- [ ] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_product_rebuild_indexes_finalized_topics_and_accepted_memories`
- [ ] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_product_rebuild_document_ids_are_stable_across_rebuild`
- [ ] `tests/coding_agent/test_semantic_maintenance.py::test_semantic_maintenance_product_rebuild_without_selected_topic_store_fails_before_clear`
- [ ] `tests/deploy/test_helm_chart.py::test_helm_default_config_derives_local_sqlite_topic_store_path`
- [ ] `tests/deploy/test_helm_chart.py::test_helm_default_config_does_not_render_or_enable_memory_semantic`
- [ ] `docs/topic_layer/USAGE.md` documents SQLite/PG TopicStore backend split after implementation.
- [ ] `docs/AGENTKIT-ARCHITECTURE.md` and `docs/CODING-AGENT-ARCHITECTURE.md` preserve the AgentKit/Coding Agent boundary after implementation.
- [ ] `uv run pytest tests/coding_agent/test_topic_store_contract.py tests/coding_agent/test_topic_store.py tests/coding_agent/test_sqlite_topic_store.py -v`
- [ ] `uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py -k "topic or upgrade" -v`
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py -k "topic_store or semantic_topic_store or checkpoint_restore" -v`
- [ ] `uv run pytest tests/coding_agent/test_runtime_preparation.py -k "semantic_topic_store or local_daemon" -v`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "console_topic" -v`
- [ ] `uv run pytest tests/coding_agent/test_semantic_maintenance.py -k "factory or topic_store_available or rebuild" -v`
- [ ] `uv run pytest tests/deploy/test_helm_chart.py -k "topic_store or memory_semantic or storage" -v`
- [ ] `git diff --check -- docs/topic_layer/USAGE.md docs/AGENTKIT-ARCHITECTURE.md docs/CODING-AGENT-ARCHITECTURE.md`

## Non-Goals

- No Chroma, Milvus, pgvector, or additional vector backend.
- No default `[memory.semantic]` enablement.
- No o6n rollout that enables semantic memory.
- No AgentKit TopicStore protocol.

## References

- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
- `docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md`
- `docs/topic_layer/USAGE.md`
- `docs/AGENTKIT-ARCHITECTURE.md`
- `docs/CODING-AGENT-ARCHITECTURE.md`
- `src/coding_agent/topics/store.py`
- `src/coding_agent/topics/semantic_maintenance.py`
- `src/coding_agent/topics/semantic_sync.py`
- `src/coding_agent/stores/durable_local.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/http_server.py`
- `src/coding_agent/core/app.py`
- `src/coding_agent/runs/runtime_preparation.py`
- `src/coding_agent/runs/checkpoint_runtime.py`
- `helm/templates/configmap-agent-config.yaml`
- `tests/coding_agent/test_topic_store.py`
- `tests/coding_agent/test_semantic_maintenance.py`
- `tests/deploy/test_helm_chart.py`
