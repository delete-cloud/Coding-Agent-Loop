Goal:
Persist compacted run message snapshots when a runtime store is configured.

Scope:
- Save a latest message snapshot for root HTTP turns after adapter execution.
- Use `ctx.messages` as the compacted context source produced by the runtime
  pipeline.
- Serialize snapshot messages into JSON-safe objects for PG JSONB storage.
- Use a deterministic latest snapshot id: `{run_id}:latest`.
- Keep runtime message snapshots opt-in through the existing runtime store.
- Preserve JSONL/file defaults.

Out of scope:
- Do not add replay endpoints or event search APIs.
- Do not persist approval interaction records.
- Do not change AgentKit pipeline behavior.
- Do not change runtime store configuration semantics.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/runtime_store.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/coding_agent/test_pg_runtime_store.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "message_snapshot or persists_wire_events or approval_request_wire_events or agent_run or run_id" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`
- `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate replay endpoints or approval interaction persistence.
