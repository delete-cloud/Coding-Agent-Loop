Goal:
Introduce explicit runtime store contract boundaries as a behavior-preserving
step toward ADR-0058 store separation.

Scope:
- Move the current SessionManager-local RuntimeStoreProtocol into a dedicated
  coding_agent store contract module.
- Split the protocol surface into composable RuntimeRunLifecycleStore,
  RuntimeRunStore, RuntimeEventStore, RuntimeInteractionStore, and
  RuntimeCheckpointStore contracts.
- Update SessionManager and runtime run lifecycle typing to depend on the
  shared contracts.
- Add focused tests that concrete local runtime stores satisfy the new
  structural contracts.
- Refresh ADR-0058 follow-up status for the partial store-contract boundary.

Out of scope:
- Change JSONL, SQLite, or Postgres runtime store schemas.
- Rename persisted record types or payload fields.
- Move runtime store implementations.
- Extract RunService/EventStore implementations from SessionManager.
- Change HTTP, CLI, executor, or runtime behavior.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runtime_store.py
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/runs/lifecycle.py
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md

Target tests:
- uv run pytest tests/coding_agent/test_runtime_store_contracts.py -v
- uv run pytest tests/coding_agent/test_run_lifecycle.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run_lifecycle or replay_display_events" -v
- uv run ruff check src/coding_agent/stores src/coding_agent/server/session_manager.py src/coding_agent/runs/lifecycle.py tests/coding_agent/test_runtime_store_contracts.py
- git diff --check

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 contract, behavior, import-boundary, or test-gap
  findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.

Stop conditions:
- Stop if this requires store schema migration.
- Stop if this requires moving concrete store implementations.
- Stop if this changes public HTTP/CLI behavior.
