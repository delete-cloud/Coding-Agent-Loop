Goal:
Move local daemon runtime-turn generic error wire notification out of
`SessionManager.run_agent` into a small wire-owned helper without changing wire
messages or turn lifecycle behavior.

Scope:
- Add a wire helper that emits the existing generic error `StreamDelta` and
  `TurnEnd(ERROR)` sequence for a runtime turn.
- Replace the inline generic error notification closure in
  `SessionManager.run_agent` with the helper.
- Add focused tests for the helper's emitted wire messages and logging hook.
- Update ADR-0058 progress notes for this narrow ownership movement.

Out of scope:
- Do not change approval wire consumer setup.
- Do not change runtime event persistence semantics.
- Do not move live SSE/UI rendering to `DisplayEvent`.
- Do not introduce a new wire protocol message.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/wire/protocol.py`
  - `src/coding_agent/wire/__init__.py`
  - `tests/coding_agent/test_runtime_turn_wire.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_turn_wire.py -v`
- `uv run pytest tests/coding_agent/test_run_lifecycle.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "turn_error or fatal_tool or bootstrap_fails or closes_cached_runtime" -v`
- `uv run ruff check src/coding_agent/wire/__init__.py src/coding_agent/wire/runtime.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_wire.py`
- `uv run ruff format --check src/coding_agent/wire/__init__.py src/coding_agent/wire/runtime.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_wire.py`
- `git diff --check`

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
