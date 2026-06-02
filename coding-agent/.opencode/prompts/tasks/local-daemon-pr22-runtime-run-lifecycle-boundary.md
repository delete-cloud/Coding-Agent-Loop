Goal:
Introduce a small durable runtime run lifecycle boundary so SessionManager no longer owns raw create/update/finish record construction inline.

Scope:
- Add a RuntimeRunLifecycle service for create/start/update/finish operations over runtime stores.
- Keep runtime store payloads, HTTP/CLI behavior, run statuses, and metadata unchanged.
- Make existing SessionManager runtime run helpers delegate through the lifecycle boundary.
- Add focused unit tests for the lifecycle boundary and run the affected SessionManager runtime tests.

Out of scope:
- Change RuntimeStore schemas or persisted payload formats.
- Split RuntimeEvent/DisplayEvent.
- Move observation, wire consumer, runtime close, or executor callbacks.
- Implement daemon-backed client surfaces.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runs/lifecycle.py
  - src/coding_agent/runs/__init__.py
  - src/coding_agent/server/session_manager.py
  - tests/coding_agent/test_run_lifecycle.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_run_lifecycle.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "persists_agent_run_lifecycle or marks_agent_run_failed or routes_unsupported_runtime_through_run_coordinator or agent_run_marks_interrupted" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -v
- uv run pytest tests/ui/test_http_server_failover.py -k "events or event_queue or owner_change or stale_owner or stream or queue_registration" -v
- uv run pytest tests/ui/test_session_manager_public_api.py -k "event_queue or cleanup or teardown" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "event_queues or clear_sessions or close_session or shutdown_session_runtime or cleanup" -v
- uv run ruff check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_run_lifecycle.py
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
