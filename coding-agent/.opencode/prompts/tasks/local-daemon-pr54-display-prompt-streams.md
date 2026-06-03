Goal:
Move remote prompt/resume POST stream clients onto the user-facing
`DisplayEvent` stream boundary while preserving legacy wire streams for
compatibility.

Scope:
- Add an opt-in `event_format=display` response format to
  `POST /sessions/{session_id}/prompt` and `POST /sessions/{session_id}/resume`.
- Project normal prompt/resume wire SSE events into `DisplayEvent` envelopes for
  the direct POST response stream when display format is requested.
- Preserve legacy wire stream behavior as the default and continue broadcasting
  legacy wire events to session event queues.
- Switch remote prompt/resume clients to request display streams and render
  `DisplayEvent` envelopes.
- Update ADR-0058 implementation status for prompt/resume DisplayEvent clients.

Out of scope:
- Removing legacy prompt/resume wire streams.
- Changing `RunRequested` control events for attached/external executors.
- Web UI renderer migration.
- Daemon process supervision or pure-client CLI conversion beyond event streams.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/remote/client.py`
  - `tests/ui/test_http_server.py`
  - `tests/cli/test_remote_client.py`

Target tests:
- `uv run pytest tests/ui/test_http_server.py -k "prompt_display_events or resume_display_events or prompt_streaming_events" -v`
- `uv run pytest tests/cli/test_remote_client.py -k "display_prompt_stream or display_resume_stream or stream_prompt or stream_resume or display_sse_event" -v`
- `uv run pytest tests/ui/test_http_server.py -k "display_events or event_generator or event_queues" -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "get_events" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py -k "event_queue or runtime_close" -v`
- `uv run ruff check src/coding_agent/server/http_server.py src/coding_agent/remote/client.py tests/ui/test_http_server.py tests/cli/test_remote_client.py`

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
