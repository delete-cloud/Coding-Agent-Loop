Goal:
Move product-facing remote client event consumption onto the user-facing
`DisplayEvent` boundary for replay listing and live attach streams.

Scope:
- Add a remote client helper for `/runs/{run_id}/display-events`.
- Switch `coding_agent remote events` to list display events instead of internal
  runtime events.
- Switch `coding_agent remote attach` to consume
  `/sessions/{session_id}/display-events` and render display event envelopes.
- Keep legacy runtime event helpers and prompt POST streaming unchanged.
- Update ADR-0058 implementation status for client DisplayEvent adoption.

Out of scope:
- Removing legacy `/runs/{run_id}/events` or `/sessions/{session_id}/events`.
- Changing prompt/resume POST stream protocol responses.
- Daemon process supervision or pure-client CLI conversion beyond event streams.
- Web UI renderer migration.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/remote/client.py`
  - `src/coding_agent/cli/remote_commands.py`
  - `tests/cli/test_remote_client.py`
  - `tests/ui/test_http_server.py`
  - `tests/ui/test_http_server_failover.py`

Target tests:
- `uv run pytest tests/cli/test_remote_client.py -k "display_event or remote_events or remote_attach or handle_sse_event" -v`
- `uv run pytest tests/ui/test_http_server.py -k "display_events or event_generator or event_queues" -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "get_events" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py -k "event_queue or runtime_close" -v`
- `uv run ruff check src/coding_agent/remote/client.py src/coding_agent/cli/remote_commands.py tests/cli/test_remote_client.py`

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
