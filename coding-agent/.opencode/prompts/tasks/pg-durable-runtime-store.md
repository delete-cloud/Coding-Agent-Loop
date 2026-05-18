Goal:
Implement the PostgreSQL durable runtime store layer for Coding-Agent-Loop.

Scope:
- Add app-owned runtime store records and a `PGRuntimeStore` backed by
  `agentkit.storage.pg.PGPool`.
- Persist and load `agent_runs`, `runtime_events`,
  `run_message_snapshots`, and `agent_interactions`.
- Make schema initialization lazy and idempotent.
- Prove create/update/load/list/replay/idempotent resolve behavior with unit
  tests.

Out of scope:
- Do not integrate the store with `SessionManager`.
- Do not change JSONL/file storage defaults.
- Do not modify `agentkit` runtime pipeline behavior.
- Do not add a live PostgreSQL integration test or require external services.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/agentkit/storage/pg.py`
  - `src/coding_agent/runtime_store.py`
  - `tests/coding_agent/test_pg_runtime_store.py`

Target tests:
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
- `uv run pytest tests/agentkit/storage/test_pg.py tests/coding_agent/plugins/test_storage_factory.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Stop with a blocker report if an equivalent durable runtime store already
  exists.
- Ignore non-blocking optimization suggestions.
