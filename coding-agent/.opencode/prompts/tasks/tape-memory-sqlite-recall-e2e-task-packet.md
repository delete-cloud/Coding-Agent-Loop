Goal:
Prove tape-native semantic memory works on the real local SQLite durable product
path: a finalized durable topic can be rebuilt into the semantic backend and
then recalled through `build_context` on a later turn without rendering raw
backend hit text.

Scope:
- Add the smallest product-level regression coverage for SQLite durable
  TopicStore + `[memory.semantic]` + semantic maintainer rebuild + semantic
  recall plugin wiring.
- Prefer an end-to-end-ish test through `SessionManager`/`create_agent` surfaces
  over another unit-level `SemanticMemoryMaintainer(...)` construction.
- Add one named regression that covers the full chain in one test: local SQLite
  durable `SessionManager` -> selected durable `TopicStore` creates/finalizes a
  topic -> `manager.semantic_memory_maintainer(session_id).rebuild()` indexes it
  -> a later registered semantic-memory `build_context` call recalls the
  authoritative topic summary.
- The full-chain regression must assert that rendered context comes from the
  authoritative topic record, not raw semantic backend hit text. If a backend
  sentinel can be injected without weakening the product path, assert the
  sentinel never renders; otherwise assert the rendered summary exactly matches
  the durable topic summary and not a stale/mutated backend value.
- If the new test exposes a missing product wiring gap, fix only that gap.
- Keep semantic memory disabled by default; tests may enable the fake semantic
  backend explicitly.

Out of scope:
- Adding Chroma, Milvus, pgvector, or any new vector backend.
- Changing `agentkit` tape primitives or treating semantic index as tape
  storage.
- Enabling semantic memory in Helm/o6n/default config.
- Rewriting `TopicRangeIndex`, `SemanticRecallPlanner`, or backend scoring.
- Expanding the HTTP API surface unless the existing product path cannot be
  exercised otherwise.

Context:
- ADRs:
  - docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md
  - docs/adr/0073-sqlite-topic-store-parity.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/core/app.py
  - src/coding_agent/topics/semantic_maintenance.py
  - src/coding_agent/topics/semantic_sync.py
  - src/coding_agent/topics/semantic_recall.py
  - src/coding_agent/plugins/semantic_memory.py
  - src/coding_agent/topics/store.py
  - src/coding_agent/stores/durable_local.py
  - tests/coding_agent/test_semantic_maintenance.py
  - tests/coding_agent/plugins/test_semantic_memory.py
  - tests/ui/test_session_manager_runtime.py
  - tests/coding_agent/test_sqlite_local_durable_fencing.py

Target tests:
- uv run pytest tests/ui/test_session_manager_runtime.py::test_sqlite_durable_semantic_rebuild_recalled_by_later_build_context_without_backend_hit_text -q
- uv run pytest tests/coding_agent/test_semantic_maintenance.py tests/coding_agent/plugins/test_semantic_memory.py -q
- uv run pytest tests/ui/test_session_manager_runtime.py -k "selected_topic_store or semantic_topic_store or semantic_memory" -q
- uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py -k "selected_topic_store or topic_store or semantic" -q
- uv run pytest tests/coding_agent/test_semantic_recall.py tests/coding_agent/test_semantic_sync.py tests/coding_agent/test_semantic_tape_index.py -q

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings for this design-sensitive slice.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the implementation needs a new backend abstraction, new
  persistent schema, or default semantic-memory enablement.
- Ignore non-blocking optimization suggestions.
