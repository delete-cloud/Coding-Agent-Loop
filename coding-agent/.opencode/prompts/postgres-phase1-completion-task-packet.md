Goal:
Complete Phase 1 PostgreSQL persistence for the HTTP/UI session stack by adding PG checkpoint storage and config-driven default persistence wiring for session metadata, tapes, and checkpoints.

Scope:
- Add `PGCheckpointStore` in `agentkit.storage.pg` with matching protocol tests.
- Add a synchronous UI PostgreSQL session metadata store and one app-layer persistence factory for default SessionManager/HTTP wiring.
- Update config defaults/tests so HTTP startup and SessionManager defaults can use file or PostgreSQL storage through one shared path.

Out of scope:
- Phase 2 ownership, fencing, approval routing, or cross-instance event routing.
- New migration tooling or schema-management framework beyond the repo's existing inline schema creation pattern.

Context:
- ADRs:
  - `docs/adr/0012-complete-phase1-postgresql-http-session-persistence.md`
  - `docs/adr/0001-checkpoint-captures-serialized-tape-and-plugin-state.md`
  - `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
  - `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
- Relevant files:
  - `src/agentkit/storage/pg.py`
  - `src/coding_agent/ui/session_store.py`
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/agent.toml`
  - `tests/agentkit/storage/test_pg.py`
  - `tests/ui/test_session_persistence.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/agentkit/storage/test_pg.py -k "checkpoint or session or tape" -v`
- `uv run pytest tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server.py -k "pg or checkpoint or persistence" -v`
- `uv run pytest tests/ -v`

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
