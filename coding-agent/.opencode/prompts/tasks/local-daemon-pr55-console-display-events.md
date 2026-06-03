Goal:
Move the tracked server-rendered console run detail event renderer onto the
user-facing `DisplayEvent` replay boundary.

Scope:
- Render run detail event summaries as display events instead of internal
  runtime events.
- Build console run detail event summaries from
  `SessionManager.replay_display_events()`.
- Point console replay guidance at `/runs/{run_id}/display-events`.
- Update ADR-0058 implementation status for the tracked console renderer.

Out of scope:
- Removing legacy `/runs/{run_id}/events`.
- Changing runtime event storage or projection semantics.
- Touching the untracked `webui/` directory in the main checkout.
- Redesigning the developer console layout.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/developer_console.py`
  - `src/coding_agent/server/http_server.py`
  - `tests/ui/test_developer_console.py`

Target tests:
- `uv run pytest tests/ui/test_developer_console.py -k "run_detail" -v`
- `uv run pytest tests/ui/test_http_server.py -k "display_events or event_generator or event_queues" -v`
- `uv run ruff check src/coding_agent/server/developer_console.py src/coding_agent/server/http_server.py tests/ui/test_developer_console.py`

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
