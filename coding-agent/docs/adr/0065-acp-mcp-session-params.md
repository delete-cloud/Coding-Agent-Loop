# ADR-0065: ACP MCP session parameters

**Status**: Accepted
**Date**: 2026-06-05

## Context

ACP `session/new` and `session/load` carry `mcpServers` so the client can attach
per-session MCP servers. Coding Agent already has an `MCPPlugin`, but before
this ADR it only read `[mcp.servers]` from `agent.toml`. ACP requests could
include `mcpServers` without affecting the runtime tools.

## Decision

Support ACP stdio MCP servers as durable per-session runtime metadata.

The ACP adapter validates `mcpServers`, rejects non-stdio transports, and
normalizes the protocol payload to the existing `MCPPlugin` config shape:

- server name -> `{command, args, env}`
- `env` accepts ACP `{name, value}` entries and is stored as a string map
- ACP-originated servers are stored with `inherit_env: false`, so the MCP
  subprocess receives only its explicit env plus `PATH`; this avoids leaking
  host secrets to client-supplied server commands

`Session.mcp_servers` is persisted with the session record. Runtime preparation
passes it to `create_agent` as `mcp_servers_override`, and `create_child_pipeline`
uses that override instead of `[mcp.servers]` from config. Subagent child
pipelines inherit the same override.

`session/load` updates the stored MCP server set from the load request before
replaying events. If the value changes, the current runtime adapter is closed so
the next turn rebuilds with the updated tools.

The ACP initialization response advertises stdio MCP support and explicitly
does not advertise HTTP or SSE MCP support.

## Alternatives Rejected

- Ignore `mcpServers` while accepting the field — rejected because external ACP
  clients would believe tools are available when they are not.
- Store MCP servers in `origin` — rejected because `origin` is string metadata,
  not runtime configuration.
- Reuse the existing toml MCP environment inheritance for ACP — rejected because
  ACP clients can supply commands dynamically, so inheriting all host env would
  expose unrelated secrets to client-controlled subprocesses.
- Advertise HTTP/SSE MCP support — rejected because the existing plugin only
  implements stdio subprocess transport.

## Acceptance Criteria

- [x] `test_initialize_advertises_stdio_mcp_capability`
- [x] `test_session_new_passes_stdio_mcp_servers_to_session_manager`
- [x] `test_session_new_rejects_unsupported_mcp_transport`
- [x] `test_session_load_updates_mcp_servers_from_params`
- [x] `test_session_record_round_trips_existing_store_payload`
- [x] `test_session_record_defaults_missing_mcp_servers_to_empty`
- [x] `test_session_as_dict_excludes_mcp_servers`
- [x] `test_runtime_preparation_service_passes_session_mcp_servers`
- [x] `test_create_agent_uses_mcp_servers_override`
- [x] `test_connection_uses_explicit_env_when_inherit_env_is_false`
- [x] `test_plugin_rejects_non_boolean_inherit_env`
- [x] `uv run pytest tests/acp -k "mcp or initialize or session_new_creates" -v`
- [x] `uv run pytest tests/acp -v`
- [x] `uv run pytest tests/ui/test_session_persistence.py -k "mcp_servers or round_trips_existing" -v`
- [x] `uv run pytest tests/coding_agent/test_runtime_preparation.py -k "mcp_servers or builds_local_daemon_runtime" -v`
- [x] `uv run pytest tests/coding_agent/test_bootstrap.py -k "mcp_servers_override" -v`
- [x] `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- [x] `uv run pytest tests/coding_agent/plugins/test_mcp.py -v`
- [x] `uv run pytest tests/coding_agent/test_local_daemon_executor.py -v`
- [x] `uv run ruff check src/coding_agent/acp src/coding_agent/app.py src/coding_agent/cli/local_runtime.py src/coding_agent/executors/local_daemon.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/server/session_manager.py tests/acp tests/coding_agent/test_bootstrap.py tests/coding_agent/test_runtime_preparation.py tests/ui/test_session_persistence.py`

## References

- `docs/adr/0061-acp-stdio-adapter.md`
- `docs/adr/0063-acp-session-load-replay.md`
- `src/coding_agent/plugins/mcp.py`
- https://agentclientprotocol.com/protocol/schema
