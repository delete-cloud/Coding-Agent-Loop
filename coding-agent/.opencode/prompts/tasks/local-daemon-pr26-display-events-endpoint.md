Goal:
Expose an additive HTTP endpoint for replaying user-facing DisplayEvent
projections from stored RuntimeEvent facts.

Scope:
- Add display-event response schemas.
- Add `GET /runs/{run_id}/display-events` using
  `SessionManager.replay_display_events()`.
- Preserve `GET /runs/{run_id}/events` exactly as the runtime fact endpoint.
- Add focused HTTP tests for projection response shape and runtime endpoint
  compatibility.

Out of scope:
- Change `/runs/{run_id}/events`.
- Add SSE/WebSocket display-event streaming.
- Persist `DisplayEvent` records separately.
- Change CLI rendering.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/schemas.py
  - src/coding_agent/server/http_server.py
  - tests/ui/test_http_server.py

Target tests:
- uv run pytest tests/ui/test_http_server.py -k "display_events or runtime_replay" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "display_events" -v
- uv run pytest tests/coding_agent/test_display_events.py -v
- uv run ruff check src/coding_agent/server/schemas.py src/coding_agent/server/http_server.py tests/ui/test_http_server.py
- git diff --check

Loop policy:
- Engineer implements the smallest additive endpoint and runs target tests.
- Reviewer reviews only the diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Stop if this requires changing runtime event schemas or streaming behavior.
- Ignore non-blocking optimization suggestions.
