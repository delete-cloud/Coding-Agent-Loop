Goal:
Implement active G09: `tape.info` and `tape.search` for PostgreSQL tape
storage, supporting filters by kind, run_id, tool_call_id, and anchor_type.

Scope:
- Add optional typed tape debug records/protocol in AgentKit storage protocols.
- Implement `PGTapeStore.info(tape_id)`.
- Implement `PGTapeStore.search(...)` with filters for tape_id, kind, run_id,
  tool_call_id, anchor_type, ordered by `tape_id, seq`.
- Keep JSONL tape storage and the base `TapeStore` protocol unchanged.
- Update `docs/durable_runtime/GOAL_PROGRESS.md`.

Out of scope:
- Do not add public HTTP endpoints in this slice.
- Do not perform full-text search over prompt/message/result content.
- Do not add destructive migrations or rewrite existing `agent_tapes` rows.
- Do not require JSONL tape stores to implement PG debug queries.
- Do not work on active G11 smoke docs/tests.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
  - `docs/adr/0033-postgresql-tape-debug-queries.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0006-add-usage-event-fields-and-fix-tool-name-kwarg-in-pipeline.md`
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
  - `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`
- Relevant files:
  - `src/agentkit/storage/pg.py`
  - `src/agentkit/storage/protocols.py`
  - `src/agentkit/storage/__init__.py`
  - `tests/agentkit/storage/test_pg.py`
  - `docs/durable_runtime/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/agentkit/storage/test_pg.py -k "tape" -v`
- `uv run pytest tests/agentkit/storage/test_protocols.py -v`
- `uv run ruff check src/agentkit/storage/pg.py src/agentkit/storage/protocols.py src/agentkit/storage/__init__.py tests/agentkit/storage/test_pg.py`
- `uv run ruff format --check src/agentkit/storage/pg.py src/agentkit/storage/protocols.py src/agentkit/storage/__init__.py tests/agentkit/storage/test_pg.py`

Loop policy:
- Engineer writes failing tests first, implements the smallest correct change,
  and runs target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- Stop with a blocker if G09 requires an `agent_tapes` schema migration.
- Stop with a blocker if callers need content full-text search.
- Stop with a blocker if the implementation would force JSONL tape stores to
  implement PG-only debug queries.
