Goal:
Add a minimal HTTP maintenance surface for semantic memory so operators can
inspect status and explicitly rebuild the derived semantic index without
enabling semantic memory by default.

Scope:
- Add HTTP schemas for semantic memory maintenance status, rebuild request, and
  rebuild response in `src/coding_agent/server/schemas.py`.
- Add admin-only HTTP endpoints in `src/coding_agent/server/http_server.py`:
  - `GET /sessions/{session_id}/memory/semantic/status`
  - `POST /sessions/{session_id}/memory/semantic/rebuild`
- Add `SessionManager` methods in `src/coding_agent/server/session_manager.py`
  so HTTP does not call destructive rebuild directly:
  - `semantic_memory_status(session_id)`
  - `rebuild_semantic_memory(session_id, batch_size, allow_rebuild)`
- Route `rebuild_semantic_memory` through `_runtime_maintenance_admission` so
  active turns and owner/fencing conflicts are handled before clearing or
  replacing derived semantic documents.
- Add focused HTTP and manager tests proving status/rebuild behavior, admin
  authorization, active-turn fencing, disabled semantic memory, missing
  TopicStore, and owner-conflict error mapping.

Out of scope:
- Do not add Chroma, Milvus, pgvector, or any new semantic backend.
- Do not enable `[memory.semantic]` by default or change Helm/o6n values.
- Do not add a REPL slash command or remote CLI wrapper in this PR.
- Do not redesign semantic ranking, rebuild authority, topic stores, or review
  transition semantics.

Context:
- ADRs:
  - `docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md`
  - `docs/adr/0073-sqlite-topic-store-parity.md`
- Relevant files:
  - `src/coding_agent/topics/semantic_maintenance.py`
  - `src/coding_agent/topics/semantic_sync.py`
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/server/schemas.py`
  - `tests/coding_agent/test_semantic_maintenance.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Design constraints:
- Treat rebuild as global derived-index maintenance. `SemanticMemoryMaintainer`
  lists all finalized topics and reviewed memories and clears the semantic
  backend before re-indexing, so rebuild must be admin-only.
- `GET status` is also admin-only in this PR. It reports maintenance state but
  must not repair, rebuild, clear, or mutate the backend.
- `POST rebuild` is an explicit destructive maintenance action. It must use
  `SessionManager.rebuild_semantic_memory(...)`, which wraps the rebuild in
  `_runtime_maintenance_admission.run_exclusive(...)`.
- Active-turn tests must use a spy or failing fake around the rebuild body so
  they prove the destructive body is not reached, not just that document counts
  happen to remain unchanged.
- `batch_size` must be bounded and positive at the HTTP schema boundary. Keep
  `allow_rebuild` explicit and document it as backend schema rebuild permission,
  not as confirmation for ordinary document clearing.
- Preserve current defaults: `[memory.semantic]` stays disabled unless
  configured by the product/operator.
- Error mapping:
  - unknown or unauthorized session visibility: 404
  - non-admin token: 403
  - owner/fencing conflict: `_owner_conflict_http_exception`
  - active turn / maintenance admission conflict: 409 and rebuild body not
    reached
  - semantic memory disabled: 409
  - missing durable `TopicStore` for rebuild: 409
  - FastAPI request schema validation may return 422; runtime invalid values
    are 400

Target tests:
- `uv run pytest tests/coding_agent/test_semantic_maintenance.py tests/ui/test_session_manager_runtime.py -k "semantic" -v`
- `uv run pytest tests/ui/test_http_server.py -k "SemanticMemoryMaintenance or MemoryReviewTransitions" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
