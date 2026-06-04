# Coding Agent ACP stdio integration

Coding Agent exposes an Agent Client Protocol (ACP) stdio agent with:

```bash
uv run python -m coding_agent \
  --provider codex \
  --model gpt-5.5 \
  acp --approval auto --max-steps 30
```

The process reads JSON-RPC 2.0 messages from stdin and writes JSON-RPC messages
to stdout. Diagnostics go to stderr.

## Supported ACP surface

The adapter currently supports:

- `initialize`
- `session/new`
- `session/prompt`
- `session/cancel`
- `session/load`
- `session/list`
- `session/close`
- agent-originated `session/request_permission`

The initialization response advertises:

- `loadSession: true`
- `sessionCapabilities.close`
- `sessionCapabilities.list`
- `mcpCapabilities.stdio: true`
- `mcpCapabilities.http: false`
- `mcpCapabilities.sse: false`

HTTP and SSE MCP transports are rejected because the runtime MCP plugin only
supports stdio subprocess servers.

## Session creation

`session/new` requires an absolute `cwd`. The cwd becomes the Coding Agent repo
path and the default local workspace root.

Example:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session/new",
  "params": {
    "cwd": "/Users/kina/Code/project",
    "mcpServers": []
  }
}
```

## MCP servers

ACP `mcpServers` are persisted as per-session runtime configuration and passed
to `MCPPlugin` when the agent pipeline is built. `session/load` updates the
stored server set before replaying session events; if the set changes, the
current runtime adapter is closed so the next turn rebuilds with the updated
tools.

Only stdio servers are supported:

```json
{
  "name": "filesystem",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
  "env": [{"name": "EXPLICIT_OK", "value": "yes"}]
}
```

ACP-originated MCP subprocesses do not inherit the host environment. They receive
only explicit `env` values plus `PATH`. This prevents client-supplied MCP server
commands from seeing unrelated host secrets. MCP servers configured in
`agent.toml` keep the existing default of inheriting the host environment unless
`inherit_env = false` is set.

## Prompt and updates

`session/prompt` starts a Coding Agent turn. The adapter streams internal wire
messages as ACP `session/update` notifications and returns a `stopReason` when
the root turn ends.

If the turn needs tool approval, Coding Agent sends an agent-originated
`session/request_permission` JSON-RPC request to the ACP client. The client must
respond on the same stdio connection. Supported outcomes map to Coding Agent
approval responses:

- `allow-once`
- `allow-session`
- `reject-once`
- `cancelled`

## Load, list, and close

`session/load` is restore-and-replay only. It loads an existing session, applies
the supplied MCP server set, replays durable display events as `session/update`
notifications, and returns `null`. It does not start a new model turn.

`session/list` returns known sessions with `sessionId`, `cwd`, `title`, and
`updatedAt`. Cursor pagination is not implemented; `nextCursor` is always
`null`. Exact absolute `cwd` filtering is supported.

`session/close` delegates to the session manager and returns `{}` after cleanup.

## Compatibility harness

The external-client compatibility harness lives in:

```bash
tests/acp/test_compat_harness.py
```

It drives `run_stdio` through a JSON-RPC client loop instead of calling
`AcpServer.handle_message` directly. The harness covers:

- initialize capability negotiation
- session creation with stdio MCP server params
- prompt streaming and response ordering
- session list/load/close lifecycle
- agent-originated permission request and client response routing

Run it with:

```bash
uv run pytest tests/acp/test_compat_harness.py -v
```

For the broader ACP gate:

```bash
uv run pytest tests/acp -v
uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v
```
