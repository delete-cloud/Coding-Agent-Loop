Goal:
- Expose checkpoint capture through the HTTP API so clients can create, list, and restore checkpoints without relying on the TUI.

Scope:
- Add `POST /sessions/{session_id}/checkpoints` with an optional label body.
- Reuse `SessionManager.capture_checkpoint()` and serialize capture with active turns.
- Add focused HTTP and session-manager tests for success, unknown-session, and busy-session behavior.

Out of scope:
- Exposing arbitrary checkpoint `extra` metadata through HTTP.
- Changing checkpoint snapshot format or restore semantics.

Context:
- ADRs:
  - `docs/adr/0001-checkpoint-captures-serialized-tape-and-plugin-state.md`
  - `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
  - `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
  - `docs/adr/0011-expose-http-checkpoint-capture-as-a-label-only-endpoint.md`
- Relevant files:
  - `src/coding_agent/ui/http_server.py`
  - `src/coding_agent/ui/schemas.py`
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/rate_limit.py`
  - `tests/ui/test_http_server.py`
  - `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/ui/test_http_server.py tests/ui/test_session_manager_public_api.py -k "capture_checkpoint" -v`
- `uv run pytest tests/ui/test_http_server.py -k "checkpoints or restore" -v`

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
