Goal:
Add a behavior-preserving service boundary for replaying user-facing
DisplayEvent projections from stored RuntimeEvent facts.

Scope:
- Add `SessionManager.replay_display_events()` that loads runtime events from
  the existing runtime store and projects them through `coding_agent.events`.
- Preserve existing runtime event persistence and `/runs/{run_id}/events`
  behavior.
- Add focused tests for projection replay, cursor passthrough, and storeless
  behavior.

Out of scope:
- Add a public HTTP display-events endpoint.
- Change `RuntimeEventsResponse` schemas.
- Change SSE/wire output.
- Persist `DisplayEvent` records separately.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/events/display.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/ui/test_session_manager_runtime.py -k "display_events" -v
- uv run pytest tests/coding_agent/test_display_events.py -v
- uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py
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
