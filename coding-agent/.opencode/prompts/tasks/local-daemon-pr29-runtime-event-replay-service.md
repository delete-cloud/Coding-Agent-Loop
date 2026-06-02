Goal:
Extract runtime event replay and DisplayEvent projection replay out of
SessionManager into a narrow service that depends on RuntimeEventStore.

Scope:
- Add a runtime event replay service under coding_agent.events.
- Move cursor validation, runtime replay, display projection replay, and
  internal-only event scanning into that service.
- Keep SessionManager public methods as compatibility delegates.
- Add focused service tests for cursor validation and display-event paging.
- Refresh ADR-0058 follow-up status for the partial event-service boundary.

Out of scope:
- Change persisted RuntimeEventRecord payloads or event_kind names.
- Change HTTP `/runs/{run_id}/events` or `/runs/{run_id}/display-events`.
- Move live SSE/UI rendering to DisplayEvent.
- Extract run, approval, checkpoint, or full event-store implementations.
- Change concrete JSONL/SQLite/Postgres runtime stores.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/events/
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/stores/runtime.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_runtime_event_replay_service.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "replay_display_events or replay_runtime_events" -v
- uv run ruff check src/coding_agent/events src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_event_replay_service.py
- uv run ruff format --check src/coding_agent/events src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_event_replay_service.py
- git diff --check

Loop policy:
- Engineer implements the smallest correct change and runs target tests.
- Reviewer reports only P1/P2 correctness, replay cursor, import-boundary,
  behavior-change, or test-gap findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.

Stop conditions:
- Stop if this requires changing HTTP or live streaming behavior.
- Stop if this requires store schema migration.
- Stop if this requires broader RunService or ApprovalService extraction.
