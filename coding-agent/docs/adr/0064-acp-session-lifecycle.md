# ADR-0064: ACP session lifecycle list and close

**Status**: Accepted
**Date**: 2026-06-05

## Context

ACP defines optional session lifecycle methods for listing known sessions and
closing active sessions. Coding Agent already has durable session metadata,
`SessionManager.list_sessions_async`, and `SessionManager.close_session`.

After adding `session/load`, ACP clients can reconnect to known session IDs, but
they still need a protocol-native way to discover sessions and close resources.

## Decision

Advertise `sessionCapabilities.list` and `sessionCapabilities.close`, then map:

- `session/list` to `list_sessions_async` plus per-session metadata loading.
- `session/close` to `close_session`.

`session/list` returns ACP `SessionInfo` entries with `sessionId`, `cwd`,
`title`, and `updatedAt`. The cwd is derived from the session repo path or local
workspace run target. Cursor pagination is not implemented in this slice, so
`nextCursor` is always `null`; cwd filtering is supported for exact absolute
path matches.

`session/close` returns an empty object after the manager closes the session.

## Alternatives Rejected

- Return only session IDs — rejected because ACP `SessionInfo.cwd` is required.
- Delete sessions directly in the ACP adapter — rejected because
  `SessionManager.close_session` already owns cancellation, runtime cleanup,
  durable deletion, and workspace finalization.
- Advertise list without cwd filtering — rejected because the schema exposes
  cwd as a first-class filter and exact matching is cheap.

## Acceptance Criteria

- [x] `test_initialize_advertises_session_lifecycle_capabilities`
- [x] `test_session_list_returns_session_info_and_filters_by_cwd`
- [x] `test_session_close_calls_session_manager_close`
- [x] `test_stdio_session_list_and_close`
- [x] `uv run pytest tests/acp -k "list or close or initialize" -v`
- [x] `uv run pytest tests/acp -v`
- [x] `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- [x] `uv run ruff check src/coding_agent/acp src/coding_agent/cli/local_runtime.py tests/acp tests/cli/test_entrypoint_contract.py`

## References

- `docs/adr/0061-acp-stdio-adapter.md`
- `docs/adr/0063-acp-session-load-replay.md`
- `src/coding_agent/server/session_manager.py`
- https://agentclientprotocol.com/protocol/v1/schema.md#session-list
- https://agentclientprotocol.com/protocol/v1/schema.md#session-close
