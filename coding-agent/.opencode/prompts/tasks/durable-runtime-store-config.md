Goal:
Wire the PostgreSQL durable runtime store into HTTP session configuration.

Scope:
- Add an opt-in `storage.runtime_backend = "pg"` setting for
  `SessionManager`.
- Reuse the existing shared PG pool used by tape/checkpoint storage.
- Keep missing, empty, `none`, and `disabled` runtime backends disabled.
- Preserve JSONL/file defaults.
- Document the production PG example with the new runtime backend.

Out of scope:
- Do not add non-PG runtime store implementations.
- Do not persist runtime events, message snapshots, or approval interactions.
- Do not add replay/search endpoints.
- Do not change AgentKit storage plugin behavior.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/agent.toml`
  - `docs/remote-sandbox-production.md`
  - `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "runtime_store or pg_backends or pg_pool or runtime_backend" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or run_id" -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate broader storage plugin or non-PG runtime-store support.
