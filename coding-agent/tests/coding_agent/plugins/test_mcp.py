# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportAttributeAccessIssue=false, reportReturnType=false, reportUnknownMemberType=false, reportUnnecessaryCast=false

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coding_agent.plugins.mcp import MCPPlugin, MCPServerConfig, _MCPConnection


@dataclass
class _FakeConnection:
    cfg: MCPServerConfig
    tools: list[dict[str, object]] = field(default_factory=list)
    stopped: int = 0
    alive: bool = True
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped += 1

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        self.calls.append((tool_name, arguments))
        return f"{self.cfg.name}:{tool_name}:{arguments.get('value', '')}"


def _make_connection(server_name: str, tool_names: list[str]) -> _MCPConnection:
    return _FakeConnection(
        cfg=MCPServerConfig(name=server_name, command="npx"),
        tools=[
            {
                "name": tool_name,
                "description": f"{tool_name} from {server_name}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for tool_name in tool_names
        ],
    )


def test_connection_uses_explicit_env_when_inherit_env_is_false(monkeypatch) -> None:
    monkeypatch.setenv("SHOULD_NOT_LEAK_SECRET", "secret")
    popen = MagicMock()
    popen.return_value.stdin = MagicMock()
    popen.return_value.stdout = MagicMock()
    popen.return_value.stderr = MagicMock()
    monkeypatch.setattr("coding_agent.plugins.mcp.subprocess.Popen", popen)
    conn = _MCPConnection(
        MCPServerConfig(
            name="acp",
            command="server",
            env={"EXPLICIT_OK": "yes"},
            inherit_env=False,
        )
    )
    conn._initialize = MagicMock()  # type: ignore[method-assign]
    conn._discover_tools = MagicMock()  # type: ignore[method-assign]

    conn.start()

    env = popen.call_args.kwargs["env"]
    assert env["EXPLICIT_OK"] == "yes"
    assert "PATH" in env
    assert "SHOULD_NOT_LEAK_SECRET" not in env


def test_plugin_rejects_non_boolean_inherit_env() -> None:
    with pytest.raises(ValueError, match="inherit_env must be a boolean"):
        MCPPlugin(servers={"bad": {"command": "server", "inherit_env": "false"}})


def test_mount_reports_server_status_and_tool_count() -> None:
    plugin = MCPPlugin(
        servers={
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        }
    )
    conn = _make_connection("filesystem", ["read_file", "write_file"])

    started = 0

    def fake_start_servers() -> None:
        nonlocal started
        started += 1

    plugin._start_servers = fake_start_servers  # type: ignore[method-assign]
    plugin._connections = {"filesystem": conn}
    plugin._tool_schemas = [SimpleNamespace(), SimpleNamespace()]

    result = plugin.do_mount()

    assert started == 1
    assert result == {
        "servers": {"filesystem": True},
        "tool_count": 2,
    }


def test_hooks_expose_mcp_tools_only_through_proxy_hooks() -> None:
    plugin = MCPPlugin()

    hooks = plugin.hooks()

    assert "get_proxy_tools" in hooks
    assert "execute_proxy_tool" in hooks
    assert "get_tools" not in hooks
    assert "execute_tool" not in hooks


def test_reload_servers_restarts_connections_and_rebuilds_index() -> None:
    plugin = MCPPlugin()
    conn = _make_connection("filesystem", ["read_file"])
    plugin._connections = {"filesystem": conn}
    plugin._tool_index = {"read_file": ("filesystem", "read_file")}
    started = 0

    def fake_start_servers() -> None:
        nonlocal started
        started += 1

    plugin._start_servers = fake_start_servers  # type: ignore[method-assign]
    plugin._tool_schemas = [SimpleNamespace()]

    message = plugin.reload_servers()

    assert conn.stopped == 1
    assert started == 1
    assert plugin._connections == {}
    assert plugin._tool_index == {}
    assert plugin._tool_schemas == []
    assert message == "Reloaded 0 server(s), 0 tool(s) available."


def test_list_servers_exposes_alive_status_and_tool_names() -> None:
    plugin = MCPPlugin()
    alive_conn = _make_connection("filesystem", ["read_file", "write_file"])
    dead_conn = _make_connection("github", [])
    dead_conn.alive = False
    plugin._connections = {
        "filesystem": alive_conn,
        "github": dead_conn,
    }

    result = plugin.list_servers()

    assert result == [
        {
            "name": "filesystem",
            "alive": True,
            "tools": ["read_file", "write_file"],
        },
        {
            "name": "github",
            "alive": False,
            "tools": [],
        },
    ]


def test_get_proxy_tools_returns_discovered_mcp_schemas() -> None:
    plugin = MCPPlugin()
    conn = _make_connection("filesystem", ["read_file"])
    plugin._connections = {"filesystem": conn}
    plugin._rebuild_tool_index()

    schemas = plugin.get_proxy_tools()

    assert [schema.name for schema in schemas] == ["read_file"]
    assert schemas[0].description == "read_file from filesystem"


def test_execute_proxy_tool_routes_to_owning_mcp_server() -> None:
    plugin = MCPPlugin()
    conn = _make_connection("filesystem", ["read_file"])
    plugin._connections = {"filesystem": conn}
    plugin._rebuild_tool_index()

    result = plugin.execute_proxy_tool(
        name="read_file",
        arguments={"value": "a.txt"},
    )

    assert result == "filesystem:read_file:a.txt"
    assert conn.calls == [("read_file", {"value": "a.txt"})]
