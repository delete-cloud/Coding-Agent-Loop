Goal:
Expose durable runtime replay APIs for runs, message snapshots, and runtime
events.

Scope:
- Add HTTP endpoints:
  - `GET /runs/{run_id}`
  - `GET /runs/{run_id}/message-snapshot`
  - `GET /runs/{run_id}/events`
- Support `last_event_id` cursor filtering for runtime event replay.
- Authorize replay access through the run's owning session.
- Return 404 for missing, unauthorized, or unconfigured durable runtime data.
- Keep runtime replay APIs opt-in through the existing runtime store.

Out of scope:
- Do not add SSE resume or live replay streaming.
- Do not persist approval interaction records.
- Do not change AgentKit pipeline behavior.
- Do not change runtime store configuration semantics.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/coding_agent/runtime_store.py`
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/ui/schemas.py`
  - `tests/coding_agent/test_pg_runtime_store.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/ui/test_http_server.py -k "runtime_replay or get_runtime_run" -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -k "runtime_event" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "message_snapshot or persists_wire_events or approval_request_wire_events or agent_run or run_id" -v`
- `uv run ruff check src/coding_agent/runtime_store.py src/coding_agent/ui/session_manager.py src/coding_agent/ui/http_server.py src/coding_agent/ui/schemas.py tests/coding_agent/test_pg_runtime_store.py tests/ui/test_http_server.py`

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate SSE resume, search, or approval interaction persistence.
