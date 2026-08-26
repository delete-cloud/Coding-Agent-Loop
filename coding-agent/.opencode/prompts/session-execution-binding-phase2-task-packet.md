Goal:
Add a first-class execution binding abstraction that separates WHERE a session runs from WHO owns it, then build the thin Phase 2 ownership substrate (session owners, leases, fencing) on top of Phase 1 PostgreSQL persistence.

Scope:
- Introduce `ExecutionBinding` dataclass hierarchy in `src/coding_agent/ui/execution_binding.py` with `LocalExecutionBinding` and `CloudWorkspaceBinding` variants, plus typed serialization helpers.
- Add `BindingResolver` protocol and `DefaultBindingResolver` in `src/coding_agent/ui/binding_resolver.py` that converts bindings into `workspace_root` and tool config for `create_agent_for_session`.
- Integrate binding into `Session` metadata: store `execution_binding` on the session, round-trip it through `to_store_data` / `from_store_data`, default missing bindings to `LocalExecutionBinding(repo_path)` for backward compatibility, and resolve the binding in `run_agent`, `ensure_session_runtime`, and `_restore_checkpoint`.
- Add thin Phase 2 ownership substrate:
  - `PGSessionOwnerStore` in `src/agentkit/storage/pg.py` with `session_owners` table schema and atomic acquire/renew/release SQL.
  - App-layer `SessionOwnerStore` in `src/coding_agent/ui/session_owner_store.py` wrapping the PG store.
  - Owner checks in `SessionManager` on `run_agent`, `_restore_checkpoint`, and `close_session` using `_assert_owner`.

Out of scope:
- HTTP server local-execution binding integration (endpoint changes and HTTP-level binding tests).
- Cloud workspace tool implementations or resolver behavior beyond `NotImplementedError`.
- Sticky routing hardening, brokered event routing, or failover boundary tests.
- In-flight turn resume after owner loss.

Context:
- ADRs:
  - `docs/adr/0014-separate-session-execution-binding-from-runtime-ownership.md`
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0012-complete-phase1-postgresql-http-session-persistence.md`
- Relevant files:
  - `src/coding_agent/ui/execution_binding.py` (new)
  - `src/coding_agent/ui/binding_resolver.py` (new)
  - `src/coding_agent/ui/session_owner_store.py` (new)
  - `src/agentkit/storage/pg.py` (append `PGSessionOwnerStore`)
  - `src/coding_agent/ui/session_manager.py` (integrate binding + owner checks)
  - `tests/ui/test_execution_binding.py` (new)
  - `tests/ui/test_session_owner_store.py` (new)
  - `tests/ui/test_session_manager_owner_checks.py` (new)
  - `tests/ui/test_session_manager_public_api.py` (binding round-trip tests)
  - `tests/agentkit/storage/test_pg.py` (PG owner store tests)
- Implementation plan:
  - `docs/superpowers/plans/2026-04-19-session-execution-binding-and-thin-phase2.md`

Target tests:
- `uv run pytest tests/ui/test_execution_binding.py -v`
- `uv run pytest tests/ui/test_session_owner_store.py -v`
- `uv run pytest tests/ui/test_session_manager_owner_checks.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "binding" -v`
- `uv run pytest tests/agentkit/storage/test_pg.py -k "owner" -v`
- `uv run pytest tests/ui/test_execution_binding.py tests/ui/test_session_owner_store.py tests/ui/test_session_manager_owner_checks.py tests/ui/test_session_manager_public_api.py tests/agentkit/storage/test_pg.py -v`

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
