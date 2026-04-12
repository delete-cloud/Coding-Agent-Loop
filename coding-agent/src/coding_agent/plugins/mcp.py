"""MCPPlugin — integrates MCP (Model Context Protocol) servers as agent tools.

Each MCP server is launched as a subprocess via stdio transport.  The plugin
discovers the tools exposed by every configured server at mount-time and
re-exposes them through the standard AgentKit hook surface:

  - mount         : starts server processes and discovers tools
  - get_tools     : returns the aggregated ToolSchema list
  - execute_tool  : routes calls to the owning server
  - on_checkpoint : health-check / reconnect logic

Configuration example (agent.toml):

    [mcp.servers.filesystem]
    command = "npx"
    args    = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

    [mcp.servers.github]
    command = "npx"
    args    = ["-y", "@modelcontextprotocol/server-github"]
    env     = {GITHUB_TOKEN = "ghp_..."}

The ``mcp`` Python library is a soft dependency.  If it is not installed the
plugin registers but exposes zero tools and logs a warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

from agentkit.tools.schema import ToolSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server config
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server subprocess."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Thin stdio transport (no external mcp library required)
# ---------------------------------------------------------------------------


class _MCPConnection:
    """Manages a JSON-RPC 2.0 / stdio conversation with one MCP server."""

    def __init__(self, cfg: MCPServerConfig) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._tools: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        env = {**os.environ, **self.cfg.env}
        cmd = [self.cfg.command, *self.cfg.args]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.warning(
                "MCPPlugin: command not found for server '%s': %s",
                self.cfg.name,
                self.cfg.command,
            )
            self._proc = None
            return
        except Exception as exc:
            logger.warning(
                "MCPPlugin: failed to start server '%s': %s",
                self.cfg.name,
                exc,
            )
            self._proc = None
            return

        # MCP initialization handshake
        try:
            self._initialize()
            self._discover_tools()
        except Exception as exc:
            logger.warning(
                "MCPPlugin: handshake failed for server '%s': %s",
                self.cfg.name,
                exc,
            )
            self._tools = []

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    def _send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the ``result`` field."""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP server is not running")

        req_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()

        if self._proc.stdout is None:
            raise RuntimeError("MCP server has no stdout")

        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise RuntimeError("MCP server closed stdout unexpectedly")
            try:
                resp = json.loads(raw.decode())
            except json.JSONDecodeError:
                continue  # skip non-JSON lines (e.g. banner text)

            if resp.get("id") != req_id:
                continue  # skip notifications / other responses

            if "error" in resp:
                raise RuntimeError(f"MCP error: {resp['error']}")
            return resp.get("result", {})

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if self._proc is None or self._proc.stdin is None:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()

    # ------------------------------------------------------------------
    # MCP protocol
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        result = self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "coding-agent", "version": "0.1.0"},
            },
        )
        logger.debug(
            "MCPPlugin: server '%s' initialized: %s",
            self.cfg.name,
            result.get("serverInfo", {}),
        )
        self._notify("notifications/initialized")

    def _discover_tools(self) -> None:
        result = self._send("tools/list")
        self._tools = result.get("tools", [])
        logger.info(
            "MCPPlugin: server '%s' exposes %d tools",
            self.cfg.name,
            len(self._tools),
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = self._send(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        # MCP returns {content: [{type, text}], isError: bool}
        content_list = result.get("content", [])
        is_error = result.get("isError", False)
        texts = [
            c.get("text", "") or json.dumps(c)
            for c in content_list
            if isinstance(c, dict)
        ]
        output = "\n".join(texts) if texts else json.dumps(result)
        if is_error:
            return f"[MCP Error] {output}"
        return output

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools


# ---------------------------------------------------------------------------
# MCPPlugin
# ---------------------------------------------------------------------------


class MCPPlugin:
    """Plugin that exposes MCP server tools inside the agent pipeline."""

    state_key = "mcp"

    def __init__(
        self,
        servers: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Args:
            servers: Dict mapping server name → raw config dict from agent.toml.
                     Example::

                         {
                             "filesystem": {
                                 "command": "npx",
                                 "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                             }
                         }
        """
        self._server_configs: list[MCPServerConfig] = []
        for name, raw in (servers or {}).items():
            self._server_configs.append(
                MCPServerConfig(
                    name=name,
                    command=raw.get("command", ""),
                    args=raw.get("args", []),
                    env=raw.get("env", {}),
                )
            )

        # name → connection
        self._connections: dict[str, _MCPConnection] = {}
        # tool_name → (server_name, raw_name) for routing
        self._tool_index: dict[str, tuple[str, str]] = {}
        self._tool_schemas: list[ToolSchema] = []

    # ------------------------------------------------------------------ #
    # Plugin protocol
    # ------------------------------------------------------------------ #

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
            "on_checkpoint": self.on_checkpoint,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Start all configured MCP servers and discover their tools."""
        self._start_servers()
        return {
            "servers": {
                name: conn.is_alive() for name, conn in self._connections.items()
            },
            "tool_count": len(self._tool_schemas),
        }

    # ------------------------------------------------------------------ #
    # get_tools
    # ------------------------------------------------------------------ #

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        """Return all tools discovered from MCP servers."""
        return list(self._tool_schemas)

    # ------------------------------------------------------------------ #
    # execute_tool
    # ------------------------------------------------------------------ #

    def execute_tool(
        self,
        name: str = "",
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Route a tool call to the owning MCP server."""
        entry = self._tool_index.get(name)
        if entry is None:
            return None  # not our tool

        server_name, raw_name = entry
        conn = self._connections.get(server_name)
        if conn is None or not conn.is_alive():
            return f"[MCPPlugin] Server '{server_name}' is not available."

        try:
            return conn.call_tool(raw_name, arguments or {})
        except Exception as exc:
            logger.error(
                "MCPPlugin: tool call '%s' on server '%s' failed: %s",
                name,
                server_name,
                exc,
            )
            return f"[MCPPlugin] Tool '{name}' failed: {exc}"

    # ------------------------------------------------------------------ #
    # on_checkpoint — reconnect dead servers
    # ------------------------------------------------------------------ #

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        """Attempt to reconnect any MCP servers that have died."""
        for name, conn in list(self._connections.items()):
            if not conn.is_alive():
                logger.info("MCPPlugin: reconnecting server '%s'", name)
                conn.stop()
                conn.start()
                if conn.is_alive():
                    self._rebuild_tool_index()

    # ------------------------------------------------------------------ #
    # Public API for CLI layer (/mcp command)
    # ------------------------------------------------------------------ #

    def list_servers(self) -> list[dict[str, Any]]:
        """Return server status info for the /mcp CLI command."""
        result = []
        for name, conn in self._connections.items():
            tool_names = [t["name"] for t in conn.tools]
            result.append(
                {
                    "name": name,
                    "alive": conn.is_alive(),
                    "tools": tool_names,
                }
            )
        return result

    def reload_servers(self) -> str:
        """Stop and restart all MCP servers, rediscovering tools."""
        for conn in self._connections.values():
            conn.stop()
        self._connections.clear()
        self._tool_index.clear()
        self._tool_schemas.clear()
        self._start_servers()
        return (
            f"Reloaded {len(self._connections)} server(s), "
            f"{len(self._tool_schemas)} tool(s) available."
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _start_servers(self) -> None:
        for cfg in self._server_configs:
            if not cfg.command:
                logger.warning(
                    "MCPPlugin: server '%s' has no command configured, skipping",
                    cfg.name,
                )
                continue
            conn = _MCPConnection(cfg)
            conn.start()
            self._connections[cfg.name] = conn

        self._rebuild_tool_index()

    def _rebuild_tool_index(self) -> None:
        self._tool_index.clear()
        self._tool_schemas.clear()

        for server_name, conn in self._connections.items():
            if not conn.is_alive():
                continue
            for raw_tool in conn.tools:
                original_name = raw_tool.get("name", "")
                if not original_name:
                    continue

                # Namespace the tool if there's a conflict across servers
                tool_name = original_name
                if tool_name in self._tool_index:
                    namespaced = f"{server_name}__{tool_name}"
                    logger.debug(
                        "MCPPlugin: tool name conflict '%s', using '%s'",
                        tool_name,
                        namespaced,
                    )
                    tool_name = namespaced

                self._tool_index[tool_name] = (server_name, original_name)
                self._tool_schemas.append(_raw_tool_to_schema(tool_name, raw_tool))

    def __del__(self) -> None:
        for conn in self._connections.values():
            conn.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_tool_to_schema(tool_name: str, raw: dict[str, Any]) -> ToolSchema:
    """Convert an MCP tool descriptor to AgentKit ToolSchema."""
    return ToolSchema(
        name=tool_name,
        description=raw.get("description", ""),
        parameters=raw.get("inputSchema", {"type": "object", "properties": {}}),
    )
