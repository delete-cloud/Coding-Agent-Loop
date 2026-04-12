# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportAttributeAccessIssue=false, reportReturnType=false, reportUnknownMemberType=false, reportUnnecessaryCast=false

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from coding_agent.plugins.mcp import MCPPlugin, MCPServerConfig, _MCPConnection


@dataclass
class _FakeConnection:
    cfg: MCPServerConfig
    tools: list[dict[str, object]] = field(default_factory=list)
    stopped: int = 0
    alive: bool = True

    def is_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        self.stopped += 1


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
