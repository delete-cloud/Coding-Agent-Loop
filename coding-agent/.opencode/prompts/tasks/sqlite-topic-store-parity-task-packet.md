Goal:
Implement ADR-0073 SQLiteTopicStore parity after the ADR draft and this task
packet pass P1/P2 review. Add durable SQLite topic storage, product-level topic
store selection, runtime/semantic-maintenance wiring, focused tests, and docs
updates without changing AgentKit TopicStore boundaries.

Scope:
- Add SQLite-backed TopicStore parity for the local durable SQLite bundle on the
  existing `local.sqlite3` path.
- Add a Coding Agent product-layer selected TopicStore surface, for example
  `SessionManager.selected_topic_store()`.
- Select `SQLiteTopicStore` for all-local SQLite durable storage, existing
  `PGTopicStore` for PG durable mode, and no durable TopicStore for custom,
  non-durable, or mixed storage.
- Route console topics, topic lifecycle/provenance, scheduled/Bee topic
  preparation, semantic recall, and semantic maintenance through the selected
  store instead of hardcoded PG-only construction.
- Pass the selected store into normal runtime build, local-daemon runtime build,
  and checkpoint restore via `create_agent_for_session(...,
  semantic_topic_store=...)` or the equivalent current creation path.
- Add a product-level semantic maintainer factory/service that fails closed
  when semantic memory is disabled or when no selected durable TopicStore exists
  for destructive rebuild.
- Split topic-store parity tests into PG SQL/schema-shape checks and shared
  behavioral contract tests against stricter PG fake plus real temp SQLite.
- Add local durable fencing tests for stale-owner and cross-session rejection
  across create/finalize/abort/anchor/recall/cost mutators.
- Add cursor and datetime tests for `(created_at, topic_id)` pagination,
  equal-timestamp tie-breaks, partial cursor rejection, fixed-width UTC text,
  and aware datetime round-trip.
- Add SQLite upgrade proof for existing local durable bundles and Helm/app proof
  for default local SQLite path derivation without semantic enablement.
- Update `docs/topic_layer/USAGE.md`, `docs/AGENTKIT-ARCHITECTURE.md`, and
  `docs/CODING-AGENT-ARCHITECTURE.md` from PG-only topic storage to SQLite/PG
  split as part of the implementation acceptance gate.

Out of scope:
- Chroma, Milvus, pgvector, or any additional vector backend.
- Default `[memory.semantic]` enablement.
- o6n rollout or chart values that enable semantic memory.
- AgentKit `TopicStore` protocol or product Topic models in AgentKit.
- Broad refactors outside the files needed to satisfy ADR-0073.

Context:
- ADRs:
  - `docs/adr/0039-topic-layer-tape-view-boundaries.md`
  - `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `docs/adr/0068-local-sqlite-transactional-durable-fencing.md`
  - `docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md`
  - `docs/adr/0073-sqlite-topic-store-parity.md`
- Relevant files:
  - `docs/topic_layer/USAGE.md`
  - `docs/AGENTKIT-ARCHITECTURE.md`
  - `docs/CODING-AGENT-ARCHITECTURE.md`
  - `src/coding_agent/topics/store.py`
  - `src/coding_agent/topics/semantic_maintenance.py`
  - `src/coding_agent/topics/semantic_sync.py`
  - `src/coding_agent/stores/durable_local.py`
  - `src/coding_agent/stores/local.py`
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/core/app.py`
  - `src/coding_agent/runs/agent_factory.py`
  - `src/coding_agent/runs/runtime_preparation.py`
  - `src/coding_agent/runs/checkpoint_runtime.py`
  - `src/coding_agent/runs/runtime_checkpoint_restore.py`
  - `src/coding_agent/topics/lifecycle.py`
  - `src/coding_agent/topics/provenance.py`
  - `src/coding_agent/topics/semantic_recall.py`
  - `src/coding_agent/plugins/semantic_memory.py`
  - `src/coding_agent/runs/scheduled.py`
  - `src/coding_agent/bee/launch.py`
  - `helm/templates/configmap-agent-config.yaml`
  - `tests/coding_agent/test_topic_store.py`
  - `tests/coding_agent/test_topic_store_contract.py`
  - `tests/coding_agent/test_sqlite_topic_store.py`
  - `tests/coding_agent/test_sqlite_local_durable_fencing.py`
  - `tests/coding_agent/test_semantic_maintenance.py`
  - `tests/coding_agent/test_runtime_preparation.py`
  - `tests/coding_agent/test_checkpoint_runtime_builder.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`
  - `tests/deploy/test_helm_chart.py`

Implementation steps:
- Split TopicStore tests into shared behavior contracts and backend-specific
  schema/query-shape tests.
- Strengthen PG fakes/fixtures so tests enforce one open topic per
  `(session_id, tape_id)`, parent topic existence for anchors/recall/cost rows,
  and cascade behavior where relevant. Update the existing cost test to create
  a parent topic first.
- Add `SQLiteTopicStore` using a real SQLite connection and schema with
  `topics`, `topic_anchors`, `topic_recall_links`, and `topic_costs`.
- Enable `PRAGMA foreign_keys=ON`, use a partial unique index for one open topic
  per `(session_id, tape_id)`, store JSON as deterministic text, and serialize
  datetimes as fixed-width UTC text that sorts by `(created_at, topic_id)`.
- Add SQLite bundle upgrade initialization through `SQLiteLocalDurableStore` or
  `SessionManager`; prove existing session/tape/checkpoint/runtime/session_tapes
  rows survive and topic tables/indexes exist after upgrade.
- Fence local durable topic mutations through `SQLiteLocalDurableStore` methods
  or a `FencedSQLiteTopicStore` delegating to those methods. Mutations include
  create, finalize, abort, record anchor, record recall link, and update cost.
  Direct read/list operations may remain direct reads.
- Add selected TopicStore creation in Coding Agent product code. Local SQLite
  durable bundle selects SQLite on the same `local.sqlite3`; PG durable mode
  selects existing PG pool; custom/non-durable/mixed storage selects none.
- Replace PG-only console/topic/semantic wiring with selected-store wiring while
  preserving console metadata fallback.
- Pass selected store through normal runtime build, local-daemon runtime build,
  and checkpoint restore so fake `create_agent_fn` tests can assert
  `semantic_topic_store`.
- Add semantic maintainer factory/service. Disabled semantic returns
  unavailable/fails closed; enabled plus selected store reports
  `topic_store_available=true`; rebuild indexes finalized topic summaries and
  accepted reviewed memories; no selected store refuses destructive rebuild
  before clearing backend.
- Add Helm/config tests proving default SQLite chart derives local TopicStore
  from `storage.paths.local` and still omits `[memory.semantic]` unless
  explicitly configured.
- Update topic-layer and architecture docs in the same implementation acceptance
  path; do not accept ADR-0073 while those docs still claim PG-only durable
  TopicStore authority.

Target tests:
- `uv run pytest tests/coding_agent/test_topic_store_contract.py tests/coding_agent/test_topic_store.py tests/coding_agent/test_sqlite_topic_store.py -v`
- `uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py -k "topic or upgrade" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "topic_store or semantic_topic_store or checkpoint_restore" -v`
- `uv run pytest tests/coding_agent/test_runtime_preparation.py -k "semantic_topic_store or local_daemon" -v`
- `uv run pytest tests/ui/test_http_server.py -k "console_topic" -v`
- `uv run pytest tests/coding_agent/test_semantic_maintenance.py -k "factory or topic_store_available or rebuild" -v`
- `uv run pytest tests/deploy/test_helm_chart.py -k "topic_store or memory_semantic or storage" -v`
- `uv run ruff check src/coding_agent/topics src/coding_agent/stores/durable_local.py src/coding_agent/server/session_manager.py src/coding_agent/server/http_server.py src/coding_agent/core/app.py src/coding_agent/runs/agent_factory.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/runs/checkpoint_runtime.py tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_store_contract.py tests/coding_agent/test_sqlite_topic_store.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_semantic_maintenance.py tests/coding_agent/test_runtime_preparation.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/deploy/test_helm_chart.py`
- `uv run ruff format --check src/coding_agent/topics src/coding_agent/stores/durable_local.py src/coding_agent/server/session_manager.py src/coding_agent/server/http_server.py src/coding_agent/core/app.py src/coding_agent/runs/agent_factory.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/runs/checkpoint_runtime.py tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_store_contract.py tests/coding_agent/test_sqlite_topic_store.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_semantic_maintenance.py tests/coding_agent/test_runtime_preparation.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/deploy/test_helm_chart.py`
- `git diff --check -- docs/adr/0073-sqlite-topic-store-parity.md docs/topic_layer/USAGE.md docs/AGENTKIT-ARCHITECTURE.md docs/CODING-AGENT-ARCHITECTURE.md src/coding_agent/topics/store.py src/coding_agent/topics/semantic_maintenance.py src/coding_agent/stores/durable_local.py src/coding_agent/server/session_manager.py src/coding_agent/server/http_server.py src/coding_agent/core/app.py src/coding_agent/runs/agent_factory.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/runs/checkpoint_runtime.py helm/templates/configmap-agent-config.yaml tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_store_contract.py tests/coding_agent/test_sqlite_topic_store.py tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_semantic_maintenance.py tests/coding_agent/test_runtime_preparation.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/deploy/test_helm_chart.py`

Review gate:
- Stop before implementation unless ADR-0073 and this task packet have no P1/P2
  review findings.
- The first implementation PR should implement SQLiteTopicStore parity and
  product selection only, not vector backend expansion.

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Stop if the implementation would require changing ADR-0072 or superseding it.
- Stop if the implementation would move TopicStore selection into AgentKit.
- Stop if the selected-store rules cannot distinguish local SQLite durable, PG
  durable, and custom/mixed/non-durable storage.
- Stop if destructive rebuild would clear semantic backend documents without a
  selected durable TopicStore.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
