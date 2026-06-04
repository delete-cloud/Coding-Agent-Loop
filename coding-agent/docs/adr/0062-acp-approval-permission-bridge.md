# ADR-0062: ACP approval permission bridge

**Status**: Accepted
**Date**: 2026-06-05

## Context

ADR-0061 added the ACP stdio adapter MVP for session creation, prompt turns,
wire updates, and cancellation. The MVP intentionally did not bridge Coding
Agent's internal approval flow to ACP. That leaves an important product gap:
when a tool requires approval, `SessionManager` emits a `WireMessage`
`ApprovalRequest` and waits for a response through its approval coordinator.
HTTP clients answer that request through `submit_approval_response`; ACP clients
expect the agent to call their `session/request_permission` method.

ACP is bidirectional JSON-RPC. The same stdio connection must therefore support
client-to-agent requests and agent-to-client requests concurrently. Permission
requests also need ordered handling inside an active `session/prompt` stream so
the blocked tool can continue before the turn returns.

## Decision

Extend the ACP adapter with a client-call facility. The stdio transport assigns
agent-originated request IDs, writes JSON-RPC requests to stdout, records a
future for each pending request, and resolves it when the client sends a
matching JSON-RPC response on stdin. Client-originated requests continue to be
dispatched to `AcpServer.handle_message`.

When `session/prompt` observes an internal `ApprovalRequest`, the ACP adapter
calls the client method `session/request_permission` with:

- `sessionId`
- a `toolCall` update containing the tool call ID, title, kind, pending status,
  and raw input
- permission options for allow once, allow for session, and reject once

The selected ACP permission outcome is translated back into
`SessionManager.submit_approval_response`. `allow-once` maps to an approved
one-shot response, `allow-session` maps to an approved session-scoped response,
`reject-once` maps to a denied response, and `cancelled` maps to a denied
response with cancellation feedback.

The adapter does not advertise new capabilities because `session/request_permission`
is a baseline client method, not an agent capability. Full policy UX and
remembered cross-session permissions remain out of scope.

## Alternatives Rejected

- Keep approval unsupported in ACP — rejected because it blocks realistic
  editor usage as soon as a command or write operation requires approval.
- Return a synthetic denial without asking the ACP client — rejected because it
  would preserve protocol liveness but make approved tool execution impossible.
- Implement permission requests as `session/update` notifications — rejected
  because ACP defines `session/request_permission` as a client method with a
  response.
- Add only allow/reject options — rejected because Coding Agent already has a
  session-scoped approval response; exposing it gives ACP clients parity with
  existing UI behavior without extra state.

## Acceptance Criteria

- [x] `test_permission_request_calls_client_and_submits_allow_once`
- [x] `test_permission_request_submits_session_scope_for_allow_session`
- [x] `test_permission_request_submits_denial_for_cancelled_outcome`
- [x] `test_stdio_resolves_agent_originated_permission_response`
- [x] `test_stdio_routes_unknown_agent_response_to_stderr`
- [x] `uv run pytest tests/acp -k "permission or stdio" -v`
- [x] `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- [x] `uv run ruff check src/coding_agent/acp src/coding_agent/cli/local_runtime.py tests/acp tests/cli/test_entrypoint_contract.py`

## References

- `docs/adr/0061-acp-stdio-adapter.md`
- `src/coding_agent/acp/server.py`
- `src/coding_agent/acp/mapper.py`
- `src/coding_agent/server/session_manager.py`
- https://agentclientprotocol.com/protocol/v1/tool-calls.md
- https://agentclientprotocol.com/protocol/v1/schema.md#session-request_permission
