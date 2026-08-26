# ADR-0011: Expose HTTP checkpoint capture as a label-only endpoint

**Status**: Accepted
**Date**: 2026-04-17

## Context

The HTTP API already supports listing checkpoints and restoring a checkpoint into an existing session, but it does not let HTTP clients create a checkpoint. That makes the HTTP surface incomplete for end-to-end checkpoint workflows and pushes clients back to TUI or internal test hooks.

`SessionManager.capture_checkpoint()` already exists in the product layer, including restart-safe session metadata stamping. The open protocol question is what part of that product-layer capability should be exposed to remote HTTP clients.

## Decision

Add `POST /sessions/{session_id}/checkpoints` to the HTTP API.

The request body exposes an optional `label` only. Caller-provided checkpoint `extra` metadata remains an internal product-layer capability and is not part of the HTTP contract.

Checkpoint capture must reject busy sessions with `409 turn already in progress`. Capture uses the same per-session turn lock that already serializes turns and restore, so HTTP checkpoint creation cannot race a live turn.

The endpoint returns the same checkpoint metadata shape used by checkpoint listing: `checkpoint_id`, `tape_id`, `session_id`, `entry_count`, `window_start`, `created_at`, and `label`.

## Alternatives Rejected

- Expose arbitrary caller-provided `extra` metadata over HTTP — wider protocol surface, harder validation, and easier accidental coupling to reserved internal keys.
- Keep checkpoint capture internal-only — leaves the HTTP workflow incomplete and prevents pure HTTP clients from creating restore points.
- Allow capture while a turn is active — risks snapshotting mutable runtime state during an in-flight turn.

## Acceptance Criteria

- [ ] `test_capture_checkpoint_returns_session_scoped_metadata`
- [ ] `test_capture_checkpoint_returns_404_for_unknown_session`
- [ ] `test_capture_checkpoint_returns_409_for_active_turn`
- [ ] `test_capture_checkpoint_rejects_active_turn`
- [ ] `test_capture_checkpoint_rejects_when_turn_lock_is_held`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/ui/test_session_manager_public_api.py -k "capture_checkpoint" -v`

## References

- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/rate_limit.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_session_manager_public_api.py`
- `docs/adr/0001-checkpoint-captures-serialized-tape-and-plugin-state.md`
- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0010-synchronize-checkpoint-restore-with-active-turns.md`
