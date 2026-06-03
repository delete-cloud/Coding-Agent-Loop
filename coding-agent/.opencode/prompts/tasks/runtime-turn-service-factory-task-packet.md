Goal:
Move runtime turn service construction out of `SessionManager` and into a
runtime-layer factory so the control plane no longer owns the full turn-service
dependency bundle.

Scope:
- Add a `RuntimeTurnServiceFactory` under `coding_agent.runs`.
- Wire `SessionManager` to build runtime turn services through that factory.
- Preserve `configure_runtime_store()` and `configure_run_coordinator()` rebuild
  behavior.
- Update ADR-0058 follow-up status for this boundary movement.

Out of scope:
- Changing run protocol, runtime store formats, or run target persistence.
- Reworking `RuntimeTurnService.run()` behavior.
- Introducing daemon process/client behavior.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/runs/turn_execution.py`
  - `src/coding_agent/runs/turn_service_factory.py`
  - `tests/coding_agent/test_runtime_turn_service.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_turn_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "configure_run_coordinator or submits_run_request or executes_local_runtime" -v`
- `uv run ruff check src/coding_agent/runs/turn_service_factory.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_service.py`
- `uv run ruff format --check src/coding_agent/runs/turn_service_factory.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_turn_service.py`

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
