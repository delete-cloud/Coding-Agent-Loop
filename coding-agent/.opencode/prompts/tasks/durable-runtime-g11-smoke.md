Goal:
Implement active G11: end-to-end smoke tests and docs covering normal run,
failed run, approval run, replay, Langfuse/OTLP correlation, and tape debug.

Scope:
- Add durable runtime smoke coverage using deterministic fakes only.
- Cover SessionManager run lifecycle, runtime events, message snapshots, and
  durable approval interactions in one smoke layer.
- Cover HTTP runtime replay endpoints with a configured runtime store.
- Cover OTLP/Langfuse trace correlation and trace-attribute privacy.
- Cover PostgreSQL tape debug `info()` and `search()` query behavior.
- Add `docs/durable_runtime/SMOKE.md`.
- Update `docs/durable_runtime/GOAL_PROGRESS.md`.

Out of scope:
- Do not add new runtime features or public HTTP endpoints.
- Do not use real LLMs, real PostgreSQL, real Langfuse, or network credentials.
- Do not change JSONL defaults, storage schema, scheduling, or replay semantics.

Context:
- ADRs:
  - `docs/adr/0028-observability-and-langfuse-integration.md`
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
  - `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
  - `docs/adr/0033-postgresql-tape-debug-queries.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/observability.py`
  - `src/agentkit/storage/pg.py`
  - `tests/integration/test_durable_runtime_smoke.py`
  - `docs/durable_runtime/SMOKE.md`
  - `docs/durable_runtime/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or persists_wire_events or approval_interaction or message_snapshot" -v`
- `uv run pytest tests/ui/test_http_server.py -k "runtime_replay" -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/agentkit/storage/test_pg.py -k "tape" -v`
- `uv run ruff check tests/integration/test_durable_runtime_smoke.py`
- `uv run ruff format --check tests/integration/test_durable_runtime_smoke.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- Stop with a blocker if G11 requires a real LLM, real PostgreSQL, real
  Langfuse, or real credentials.
- Stop with a blocker if smoke coverage requires new runtime behavior rather
  than documenting/verifying implemented behavior.
- Stop with a blocker if adding tape debug smoke coverage would require a schema
  migration or JSONL debug search.
