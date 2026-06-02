Goal:
Introduce a behavior-preserving DisplayEvent projection boundary over existing RuntimeEventRecord facts.

Scope:
- Add a `coding_agent.events` module with DisplayEvent models and projection helpers.
- Project existing `wire.*` runtime events into UI/display-oriented event kinds.
- Keep runtime event storage, replay endpoints, HTTP schemas, wire protocol, and runtime behavior unchanged.
- Add focused tests for projection semantics and redaction-preserving payload handling.

Out of scope:
- Add a new HTTP display-event endpoint.
- Change `/runs/{run_id}/events` response shape.
- Change persisted RuntimeEventRecord payloads or event_kind names.
- Refactor `stream_wire_messages` or SSE delivery.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/events/__init__.py
  - src/coding_agent/events/display.py
  - tests/coding_agent/test_display_events.py

Target tests:
- uv run pytest tests/coding_agent/test_display_events.py -v
- uv run ruff check src/coding_agent/events tests/coding_agent/test_display_events.py
- git diff --check

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
