Goal:
Persist HTTP session run lifecycle records when a runtime store is configured.

Scope:
- Add an optional `runtime_store` dependency to `SessionManager`.
- Create an `agent_runs` record for each root HTTP turn after runtime context
  identity and tape identity are known.
- Update the run record on succeeded, failed, and cancelled outcomes.
- Keep runtime persistence opt-in; JSONL/file defaults must remain unchanged.
- Store only low-cardinality run metadata and compact outcome details.

Out of scope:
- Do not create runtime stores from config yet.
- Do not persist runtime events, message snapshots, or approval interactions.
- Do not add replay endpoints.
- Do not modify AgentKit pipeline behavior.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/runtime_store.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or run_id or reuses_live_runtime or hardcode_api_key or emits_error_turn_end" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate config-based runtime-store construction to a later checkpoint.
