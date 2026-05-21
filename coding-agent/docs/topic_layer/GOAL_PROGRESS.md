# Topic Layer Goal Progress

This ledger tracks G77-G84 for the Topic Layer / Tape View Foundation phase.

## G77_TOPIC_LAYER_CURRENT_STATE_MAP

### Before

- Goal id: G77_TOPIC_LAYER_CURRENT_STATE_MAP
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `docs/topic_layer/CURRENT_STATE.md`
- Verification commands:
  - `uv run python -m pytest tests/agentkit/tape/ -v`
  - `git diff --check -- .`
- Stop criteria:
  - `docs/topic_layer/CURRENT_STATE.md` exists and maps the existing tape, run, context, memory, evaluation, console, observability, and workspace surfaces for later topic work.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `docs/topic_layer/CURRENT_STATE.md`
- Tests run:
  - `uv run python -m pytest tests/agentkit/tape/ -v`
  - `git diff --check -- .`
- Results:
  - `tests/agentkit/tape/`: 100 passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G77 is a state map only. Durable topic schema, lifecycle anchors, recall, context integration, cost aggregation, console views, and topic observability are intentionally deferred to G78-G84.

## G78_TOPIC_AND_TAPE_VIEW_ADR

### Before

- Goal id: G78_TOPIC_AND_TAPE_VIEW_ADR
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- Verification commands:
  - `test -f docs/adr/0039-topic-layer-tape-view-boundaries.md`
  - `rg -n "TopicRange|topic_initial|topic_finalized|recall_anchor|Session is not Topic|Run is not Topic|Bee workflows" docs/adr/0039-topic-layer-tape-view-boundaries.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR exists and defines Topic Layer / Tape View boundaries, including Topic, TopicRange, topic lifecycle anchors, recall anchors, status, summary, cost, context pack relationship, memory relationship, and out-of-scope schedule/Bee workflow runtime.
  - No production code changes are made.
  - Deterministic verification commands pass.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- Tests run:
  - `test -f docs/adr/0039-topic-layer-tape-view-boundaries.md`
  - `rg -n "TopicRange|topic_initial|topic_finalized|recall_anchor|Session is not Topic|Run is not Topic|Bee workflows" docs/adr/0039-topic-layer-tape-view-boundaries.md`
  - `git diff --check -- .`
- Results:
  - ADR exists and includes the required Topic, TopicRange, anchor, recall, Session/Run distinction, and Bee workflow out-of-scope terms.
  - whitespace diff check: passed.
- Remaining risks:
  - G78 is ADR-only. Durable topic store/schema, lifecycle anchor migration, recall, cost aggregation, console pages, and topic smoke tests are intentionally deferred to G79-G84.

## G79_TOPIC_STORE_AND_SCHEMA

### Before

- Goal id: G79_TOPIC_STORE_AND_SCHEMA
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_store.py`
  - `tests/coding_agent/test_topic_store.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run ruff format --check src/coding_agent/topic_store.py tests/coding_agent/test_topic_store.py`
  - `uv run ruff check src/coding_agent/topic_store.py tests/coding_agent/test_topic_store.py`
  - `git diff --check -- .`
- Stop criteria:
  - Durable topic schema initialization is idempotent.
  - Topic store APIs cover create, finalize, abort, load, list by session/tape/status, find open by session/tape, record anchors, record recall links, and update/read cost aggregates.
  - Existing `agent_tapes` behavior is not destructively migrated.
  - No schedule or Bee workflow code is implemented.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_store.py`
  - `tests/coding_agent/test_topic_store.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run ruff format --check src/coding_agent/topic_store.py tests/coding_agent/test_topic_store.py`
  - `uv run ruff check src/coding_agent/topic_store.py tests/coding_agent/test_topic_store.py`
  - `git diff --check -- .`
- Results:
  - topic store tests: 9 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G79 adds durable store primitives only. Writing lifecycle anchors to tape, migrating existing `TopicPlugin` behavior, recall context integration, topic observability, console pages, and final smoke tests are deferred to G80-G84.

## G80_TOPIC_LIFECYCLE_ANCHORS

### Before

- Goal id: G80_TOPIC_LIFECYCLE_ANCHORS
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_lifecycle.py`
  - `tests/coding_agent/test_topic_lifecycle.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/plugins/test_topic.py -v`
  - `uv run pytest tests/agentkit/tape/ -v`
  - `uv run ruff format --check src/coding_agent/topic_lifecycle.py tests/coding_agent/test_topic_lifecycle.py`
  - `uv run ruff check src/coding_agent/topic_lifecycle.py tests/coding_agent/test_topic_lifecycle.py`
  - `git diff --check -- .`
- Stop criteria:
  - Creating a topic writes a safe `topic_initial` product anchor encoded through existing tape anchor types.
  - Finalizing a topic writes a safe `topic_finalized` product anchor and records the finalized sequence.
  - Aborting a topic writes a safe `topic_aborted` product anchor or explicit aborted status.
  - Topic range listing and product-anchor discovery work on fixture tapes.
  - Old tapes without topic anchors still load and return no topic anchors.
  - Existing topic plugin and tape tests still pass.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/topic_lifecycle.py`
  - `tests/coding_agent/test_topic_lifecycle.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/plugins/test_topic.py -v`
  - `uv run pytest tests/agentkit/tape/ -v`
  - `uv run ruff format --check src/coding_agent/topic_lifecycle.py tests/coding_agent/test_topic_lifecycle.py`
  - `uv run ruff check src/coding_agent/topic_lifecycle.py tests/coding_agent/test_topic_lifecycle.py`
  - `git diff --check -- .`
- Results:
  - topic lifecycle tests: 8 passed.
  - topic store tests: 9 passed.
  - existing TopicPlugin tests: 11 passed.
  - AgentKit tape tests: 100 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G80 adds a lifecycle helper but does not wire automatic durable topics into runtime execution. Topic recall, context integration, cost provenance, console views, and final smoke tests are deferred to G81-G84.

## G81_TOPIC_RECALL_AND_CONTEXT_VIEW

### Before

- Goal id: G81_TOPIC_RECALL_AND_CONTEXT_VIEW
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/context_pack.py`
  - `src/coding_agent/topic_recall.py`
  - `tests/coding_agent/test_topic_recall.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_recall.py -v`
  - `uv run pytest tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_pack.py -v`
  - `uv run ruff format --check src/coding_agent/context_pack.py src/coding_agent/topic_recall.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_context_pack.py`
  - `uv run ruff check src/coding_agent/context_pack.py src/coding_agent/topic_recall.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_context_pack.py`
  - `git diff --check -- .`
- Stop criteria:
  - Historical topic summaries can be recalled with deterministic matching.
  - Recording recall writes a safe `recall_anchor` encoded through an existing generic tape anchor and persists a topic recall link.
  - ContextPack helpers include recalled topic metadata such as source topic ids and entry ranges when enabled.
  - Disabled mode returns no topic recall context and preserves old context behavior.
  - Memory remains reference-only and is not converted into instructions.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/context_pack.py`
  - `src/coding_agent/topic_recall.py`
  - `tests/coding_agent/test_topic_recall.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_recall.py -v`
  - `uv run pytest tests/coding_agent/test_topic_lifecycle.py -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_context_pack.py -v`
  - `uv run ruff format --check src/coding_agent/context_pack.py src/coding_agent/topic_recall.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_context_pack.py`
  - `uv run ruff check src/coding_agent/context_pack.py src/coding_agent/topic_recall.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_context_pack.py`
  - `git diff --check -- .`
- Results:
  - topic recall tests: 7 passed.
  - topic lifecycle tests: 8 passed.
  - topic store tests: 9 passed.
  - context system smoke: 1 passed.
  - context pack tests: 5 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G81 adds deterministic recall and context-pack helpers only. Topic summaries are guarded as reference-only context, and default recall ignores low-information kind-only matches. Runtime build_context wiring, topic cost/eval/memory provenance, console pages, and final smoke tests are deferred to G82-G84.

## G82_TOPIC_COST_EVAL_MEMORY_PROVENANCE

### Before

- Goal id: G82_TOPIC_COST_EVAL_MEMORY_PROVENANCE
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/observability.py`
  - `src/coding_agent/topic_provenance.py`
  - `tests/coding_agent/test_topic_provenance.py`
  - `tests/coding_agent/test_observability.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_provenance.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -k "topic or forbidden_high_cardinality_labels" -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run ruff format --check src/coding_agent/topic_provenance.py src/coding_agent/observability.py tests/coding_agent/test_topic_provenance.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/topic_provenance.py src/coding_agent/observability.py tests/coding_agent/test_topic_provenance.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Stop criteria:
  - Topic usage/action/validation/run/tool counts can be converted into `TopicCostRecord` deltas and persisted through the existing topic store aggregate API.
  - Eval result provenance can reference `topic_id` and source entry ranges in deterministic metadata.
  - Memory evidence provenance can reference `topic_id` and source entry ranges without turning memory into instructions.
  - Prometheus accepts only low-cardinality topic labels such as `topic_kind`, `topic_status`, and `topic_profile`.
  - Prometheus output never exposes `topic_id` as a label.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/observability.py`
  - `src/coding_agent/topic_provenance.py`
  - `tests/coding_agent/test_observability.py`
  - `tests/coding_agent/test_topic_provenance.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_provenance.py -v`
  - `uv run pytest tests/coding_agent/test_observability.py -k "topic or forbidden_high_cardinality_labels" -v`
  - `uv run pytest tests/coding_agent/test_topic_store.py -v`
  - `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
  - `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run ruff format --check src/coding_agent/topic_provenance.py src/coding_agent/observability.py tests/coding_agent/test_topic_provenance.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/topic_provenance.py src/coding_agent/observability.py tests/coding_agent/test_topic_provenance.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Results:
  - topic provenance tests: 7 passed.
  - scoped observability tests: 3 passed, 23 deselected.
  - topic store tests: 9 passed.
  - context system smoke: 1 passed.
  - observability platform smoke: 2 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G82 adds deterministic provenance helpers and low-cardinality metric label support only. `topic_kind` is allowlisted and unknown values are normalized to `unknown`; `topic_id` remains excluded from Prometheus labels. Console topic views and end-to-end topic smoke docs remain deferred to G83-G84.

## G83_TOPIC_CONSOLE_AND_OBSERVABILITY

### Before

- Goal id: G83_TOPIC_CONSOLE_AND_OBSERVABILITY
- Intended files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_developer_console.py`
  - `tests/coding_agent/test_observability.py`
- Verification commands:
  - `uv run pytest tests/ui/test_developer_console.py -k "topic or observability or e2e" -v`
  - `uv run pytest tests/coding_agent/test_observability.py -k "topic or forbidden_high_cardinality_labels" -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Stop criteria:
  - Developer Console navigation includes Topics.
  - `/console/topics` renders a topic list using existing run/topic metadata and empty state.
  - `/console/topics/{topic_id}` renders topic range summary, anchors, recall links, cost, and related runs/actions/validations where available.
  - Observability correlation can display safe `topic_id` as trace correlation metadata.
  - No raw sensitive content is rendered, and Prometheus still rejects `topic_id` labels.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/topic_layer/GOAL_PROGRESS.md`
  - `src/coding_agent/observability.py`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/coding_agent/test_observability.py`
  - `tests/ui/test_developer_console.py`
- Tests run:
  - `uv run pytest tests/ui/test_developer_console.py -k "topic or observability or e2e" -v`
  - `uv run pytest tests/coding_agent/test_observability.py -k "topic or forbidden_high_cardinality_labels" -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/observability.py tests/ui/test_developer_console.py tests/coding_agent/test_observability.py`
  - `git diff --check -- .`
- Results:
  - targeted console topic/observability tests: 6 passed, 26 deselected.
  - scoped observability no-label tests: 3 passed, 23 deselected.
  - full developer console tests: 32 passed.
  - observability platform smoke: 2 passed.
  - scoped ruff format/check: passed.
  - whitespace diff check: passed.
- Remaining risks:
  - G83 prefers durable `PGTopicStore` topic/anchor/recall/cost data when the HTTP server uses PG-backed storage, and falls back to existing sanitized run/topic metadata otherwise. Final E2E topic smoke and user docs remain deferred to G84.
