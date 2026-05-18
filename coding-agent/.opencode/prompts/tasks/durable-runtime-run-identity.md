Goal:
Bind HTTP session turns to durable runtime run identity.

Scope:
- Generate one root `run_id` for each `SessionManager.run_agent()` turn.
- Preserve `Session.current_turn_id` as the compatibility alias for that root
  `run_id`.
- Pass the root `run_id` into `create_agent(..., run_id_override=...)` when a
  runtime is created.
- Rebind a reused hot runtime context to the new root `run_id` before each
  turn.
- Use the same alias in error `TurnEnd` messages.

Out of scope:
- Do not persist run lifecycle rows yet.
- Do not add event replay, durable approval, or runtime-store integration.
- Do not change JSONL/file defaults.
- Do not modify AgentKit pipeline behavior.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_id or reuses_live_runtime or hardcode_api_key or emits_error_turn_end" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architecture redirection or scope expansion to the human.
