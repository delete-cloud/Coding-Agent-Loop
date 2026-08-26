# ADR-0061: ACP stdio adapter for Coding Agent

**Status**: Accepted
**Date**: 2026-06-04

## Context

The Agent Client Protocol (ACP) defines a JSON-RPC 2.0 protocol that lets code
editors and other clients launch an agent as a subprocess, create sessions, send
prompt turns, receive `session/update` notifications, and cancel active work.
Coding Agent already has the product semantics needed for an ACP MVP:
`SessionManager` creates sessions, runs prompt turns, exposes cancellation, and
emits typed `LocalWire` messages for model output, tool calls, tool results, and
turn completion.

The current public control plane is HTTP/SSE. Reusing it would force an ACP
client to run an HTTP daemon and would still require a custom SSE-to-ACP
translation. The narrower integration point is a product-layer stdio adapter
that maps ACP JSON-RPC requests directly to the existing session manager and
wire messages.

## Decision

Add an ACP adapter under `src/coding_agent/acp/`. This is product integration
code, not a generic `agentkit` protocol layer. The adapter owns JSON-RPC stdio
framing, ACP request validation, ACP response/error formatting, and conversion
between Coding Agent `WireMessage` values and ACP `session/update`
notifications.

The first implementation supports the ACP baseline:

- `initialize`
- `session/new`
- `session/prompt`
- `session/cancel`

`session/new` maps ACP `cwd` to `SessionManager.create_session(repo_path=cwd,
...)`. `session/prompt` joins supported ACP content blocks into a user prompt,
starts the existing Coding Agent turn, streams `LocalWire` messages as ACP
`session/update` notifications, and returns an ACP `stopReason` when the root
`TurnEnd` is observed. `session/cancel` maps to `SessionManager.cancel_session_turn`.

The stdio entry point must reserve stdout for JSON-RPC messages. Human-readable
diagnostics and logs go to stderr only.

The MVP does not advertise or implement `session/load`, `session/resume`,
client-hosted filesystem methods, terminal methods, session list/close, or
full approval bridging. Approval can be added in a follow-up by mapping
`ApprovalRequest` to ACP `session/request_permission`; until then the ACP CLI
defaults to non-interactive approval policies.

## Alternatives Rejected

- Implement ACP by wrapping the existing HTTP daemon — rejected because ACP
  clients expect a subprocess speaking JSON-RPC over stdio, and an HTTP bridge
  would add an unnecessary daemon lifecycle and a second streaming protocol.
- Move ACP into `agentkit` — rejected because ACP is a Coding Agent product
  integration over product-specific sessions, workspaces, approvals, and wire
  messages.
- Advertise `session/load` or `session/resume` in the first pass — rejected
  because ACP replay/resume semantics need an explicit durable event contract.
- Implement terminal and client filesystem methods immediately — rejected
  because Coding Agent already executes tools internally; ACP client-hosted
  tools are not required for a useful MVP.
- Treat approval bridging as part of the MVP — rejected to keep the first PR
  bounded. The first version avoids advertising interactive approval support.

## Acceptance Criteria

- [x] `test_initialize_returns_protocol_version_and_minimal_capabilities`
- [x] `test_session_new_creates_local_session_from_absolute_cwd`
- [x] `test_session_prompt_streams_agent_message_chunk_and_returns_end_turn`
- [x] `test_session_prompt_rejects_active_turn`
- [x] `test_session_cancel_calls_session_manager_cancel`
- [x] `test_stdio_server_writes_jsonrpc_to_stdout_only`
- [x] `test_wire_mapper_converts_tool_call_and_tool_result_updates`
- [x] `uv run pytest tests/acp -v`
- [x] `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- [x] `uv run ruff check src/coding_agent/acp src/coding_agent/cli/main.py src/coding_agent/cli/acp_command.py src/coding_agent/cli/local_runtime.py tests/acp tests/cli/test_entrypoint_contract.py`

## References

- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/wire/protocol.py`
- `src/coding_agent/cli/main.py`
- `src/coding_agent/plugins/mcp.py`
- https://agentclientprotocol.com/protocol/v1/overview.md
- https://agentclientprotocol.com/protocol/v1/initialization.md
- https://agentclientprotocol.com/protocol/v1/session-setup.md
- https://agentclientprotocol.com/protocol/v1/prompt-turn.md
