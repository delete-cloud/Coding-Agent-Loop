Goal:
Move the local wire consumer adapter class out of `SessionManager` into the
wire module while keeping approval business logic in `SessionManager`.

Scope:
- Add `LocalWireConsumer` under `coding_agent.wire`.
- Preserve current `emit()` behavior: use custom emit handler when provided,
  otherwise send through `LocalWire`.
- Preserve current `request_approval()` delegation to the supplied approval
  handler.
- Update `SessionManager` to construct `LocalWireConsumer` instead of the
  private server-local `_WireConsumer`.
- Add focused wire tests for emit fallback, custom emit handler, and approval
  delegation.
- Update ADR-0058 progress notes for this narrow wire consumer adapter move.

Out of scope:
- Do not move approval/session mutation logic out of `SessionManager`.
- Do not change runtime event persistence or approval interaction records.
- Do not change checkpoint restore approval behavior.
- Do not change live SSE/UI rendering.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/wire/__init__.py`
  - `src/coding_agent/wire/consumer.py`
  - `tests/wire/test_consumer.py`

Target tests:
- `uv run pytest tests/wire/test_consumer.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "approval or restore_consumer or bootstrap_fails" -v`
- `uv run ruff check src/coding_agent/wire/__init__.py src/coding_agent/wire/consumer.py src/coding_agent/server/session_manager.py tests/wire/test_consumer.py`
- `uv run ruff format --check src/coding_agent/wire/__init__.py src/coding_agent/wire/consumer.py src/coding_agent/server/session_manager.py tests/wire/test_consumer.py`
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
