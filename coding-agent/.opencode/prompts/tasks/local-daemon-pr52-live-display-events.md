Goal:
Add a live user-facing `DisplayEvent` stream for sessions so UI clients can
consume projected display events without binding to internal wire/runtime event
names.

Scope:
- Add live projection from queued wire SSE events into `DisplayEvent` records.
- Add an additive `/sessions/{session_id}/display-events` SSE endpoint.
- Reuse the existing owned event queue authorization, post-attach owner
  revalidation, keepalive, and disconnect cleanup shape.
- Update ADR-0058 implementation status for live DisplayEvent projection.

Out of scope:
- Removing or changing the legacy `/sessions/{session_id}/events` wire stream.
- Changing runtime event replay APIs.
- Daemon-backed CLI/REPL clients.
- Sandbox defaulting.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/events/display.py`
  - `src/coding_agent/server/http_server.py`
  - `tests/coding_agent/test_display_events.py`
  - `tests/ui/test_http_server.py`
  - `tests/ui/test_http_server_failover.py`

Target tests:
- `uv run pytest tests/coding_agent/test_display_events.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "display_events or event_generator or event_queues" -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "get_events" -v`
- `uv run ruff check src/coding_agent/events/display.py src/coding_agent/server/http_server.py tests/coding_agent/test_display_events.py tests/ui/test_http_server.py`

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
