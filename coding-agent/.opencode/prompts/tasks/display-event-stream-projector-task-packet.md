Goal:
Move live display-event SSE projection out of `server/http_server.py` and into
the `coding_agent.events` boundary so HTTP routing does not directly assemble
display event source IDs or SSE envelopes.

Scope:
- Add an event-layer projector for live wire SSE events to display-event SSE
  responses.
- Delegate prompt and session display-event stream transforms in
  `server/http_server.py` to the projector.
- Add focused event-layer tests and keep HTTP display-event regressions.
- Update ADR-0058 follow-up status text for tracked live stream projection.

Out of scope:
- Changing SSE wire formats or HTTP endpoint paths.
- Changing runtime event replay storage.
- Editing or importing the untracked standalone `webui/` workspace.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Postmortems:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
- Relevant files:
  - `src/coding_agent/events/display.py`
  - `src/coding_agent/events/__init__.py`
  - `src/coding_agent/server/http_server.py`
  - `tests/coding_agent/test_display_events.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_display_events.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "display_events or event_format" -v`
- `uv run ruff check src/coding_agent/events/display.py src/coding_agent/events/__init__.py src/coding_agent/server/http_server.py tests/coding_agent/test_display_events.py`
- `uv run ruff format --check src/coding_agent/events/display.py src/coding_agent/events/__init__.py src/coding_agent/server/http_server.py tests/coding_agent/test_display_events.py`
- `git diff --check`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the standalone `webui/` workspace must be touched.
- Ignore non-blocking optimization suggestions.
