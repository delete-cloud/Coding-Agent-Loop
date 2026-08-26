# ADR-0063: ACP session load replay

**Status**: Accepted
**Date**: 2026-06-05

## Context

ADR-0061 added the ACP stdio adapter MVP and intentionally deferred
`session/load` until there was an explicit durable replay contract. Coding Agent
already persists runtime events and projects them into user-facing
`DisplayEvent` records through `SessionManager.replay_display_events`.

ACP `session/load` requires the agent to restore an existing session and stream
the conversation history back as `session/update` notifications before returning
to the original request. It must not start a new run; ACP `session/resume`
covers reconnecting without replay and remains out of scope for this slice.

## Decision

Advertise `agentCapabilities.loadSession = true` and implement `session/load`
in the ACP adapter.

The adapter validates the requested session ID and absolute working directory,
loads the existing session through `SessionManager.get_session_async`, lists the
session's durable runtime runs through `SessionManager.list_runtime_runs`, and
replays each run through `SessionManager.replay_display_events` in stable
creation order.

Replay uses the existing display projection instead of reading raw runtime
events or tape entries directly. This keeps ACP replay aligned with the HTTP
`/runs/{run_id}/display-events` endpoint and avoids exposing raw tool result
payloads that the display layer already redacts.

Each replayed display event is mapped back into an ACP `session/update`
notification. Events that do not have an ACP representation, such as final
result markers or malformed tool events without IDs, are skipped. After all
updates for all known runs are emitted, `session/load` returns `null`, matching
the ACP loading flow.

## Alternatives Rejected

- Rebuild history from tape entries directly — rejected because tape is the
  authoritative log but ACP needs a user-facing projection; display replay
  already provides that sanitized projection.
- Call `resume_session` during `session/load` — rejected because that starts a
  new run, while ACP load is a pure restore-and-replay operation.
- Use `session_resume_metadata.last_run_id` — rejected because that would only
  replay the latest run and would advertise `loadSession` without meeting ACP's
  whole-conversation replay expectation.

## Acceptance Criteria

- [x] `test_initialize_advertises_load_session`
- [x] `test_session_load_replays_display_events_before_response`
- [x] `test_session_load_replays_all_runs_in_started_order`
- [x] `test_session_load_without_runs_returns_null_without_replay`
- [x] `test_stdio_session_load_writes_replay_updates_before_response`
- [x] `uv run pytest tests/acp -k "load or initialize" -v`
- [x] `uv run pytest tests/acp -v`
- [x] `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- [x] `uv run ruff check src/coding_agent/acp src/coding_agent/cli/local_runtime.py tests/acp tests/cli/test_entrypoint_contract.py`

## References

- `docs/adr/0061-acp-stdio-adapter.md`
- `docs/adr/0062-acp-approval-permission-bridge.md`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/events/display.py`
- https://agentclientprotocol.com/protocol/v1/session-setup.md#loading-sessions
- https://agentclientprotocol.com/protocol/v1/schema.md#session-load
