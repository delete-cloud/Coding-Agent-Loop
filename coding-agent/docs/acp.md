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
- `session/resume`
- `session/set_mode`
- `session/list`
- `session/close`
- agent-originated `session/request_permission`

The initialization response advertises:

- `loadSession: true`
- `sessionCapabilities.close`
- `sessionCapabilities.list`
- `sessionCapabilities.resume`
- `sessionCapabilities.additionalDirectories`
- `mcpCapabilities.stdio: true`
- `mcpCapabilities.http: false`
- `mcpCapabilities.sse: false`

HTTP and SSE MCP transports are rejected because the runtime MCP plugin only
supports stdio subprocess servers.

`initialize` requires `protocolVersion: 1` in the request params. Requests with a
missing or unsupported protocol version are rejected with JSON-RPC invalid params.

## Session creation

`session/new` requires an absolute `cwd` and an `mcpServers` array. The cwd
becomes the Coding Agent repo path and the default local workspace root.

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

## Additional directories

ACP `additionalDirectories` are supported on `session/new`, `session/load`, and
`session/resume`. Every directory must be an absolute path. The list is stored as
per-session metadata and treated as the complete additional-root set for the
session.

Additional directories are activated for local file tools, `file_patch`,
`bash_run`, and native/Docker/Podman sandbox runners. Relative paths still
resolve against the primary `cwd`; absolute paths are allowed when they are under
either the primary workspace root or one of the additional directories.

`session/list` includes `additionalDirectories` for each returned session.

## MCP servers

ACP `mcpServers` are persisted as per-session runtime configuration and passed
to `MCPPlugin` when the agent pipeline is built. `session/load` updates the
stored server set before replaying session events; if the set changes, the
current runtime adapter is closed so the next turn rebuilds with the updated
tools.

The `mcpServers` field is required by ACP on both `session/new` and
`session/load`; use an empty array when no MCP servers are requested.
`session/resume` accepts the same MCP server shape but treats omitted
`mcpServers` as an empty server set.

Only stdio servers are supported:

```json
{
  "name": "filesystem",
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

Prompt content supports ACP baseline text blocks and resource links. Resource
links must include both `name` and `uri`; the adapter passes the URI into the
agent prompt text.

If the turn needs tool approval, Coding Agent sends an agent-originated
`session/request_permission` JSON-RPC request to the ACP client. The client must
respond on the same stdio connection. Supported outcomes map to Coding Agent
approval responses:

- `allow-once`
- `allow-session`
- `reject-once`
- `cancelled`

## Load, resume, list, and close

`session/load` is restore-and-replay only. It loads an existing session, applies
the supplied MCP server set, replays durable display events as `session/update`
notifications, and returns `{}`. It does not start a new model turn.

`session/resume` re-attaches to an existing session without replaying previous
messages. It applies the supplied MCP server set and additional directories, then
returns `{}`. It does not start a new model turn.

Session responses include a single ACP mode, `default`, representing standard
Coding Agent behavior. `session/set_mode` accepts `default` and returns `{}`.

`session/list` returns known sessions with `sessionId`, `cwd`, `title`,
`updatedAt`, and `additionalDirectories`. Cursor pagination is not implemented;
`nextCursor` is always `null`. Exact absolute `cwd` filtering is supported.

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
