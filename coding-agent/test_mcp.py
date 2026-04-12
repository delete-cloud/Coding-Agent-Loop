from __future__ import annotations

from unittest.mock import MagicMock, patch

from coding_agent.plugins.mcp import MCPPlugin, MCPServerConfig, _MCPConnection


def _make_connection(server_name: str, tool_names: list[str]) -> _MCPConnection:
    conn = MagicMock(spec=_MCPConnection)
    conn.cfg = MCPServerConfig(name=server_name, command="npx")
    conn.is_alive.return_value = True
    conn.tools = [
        {
            "name": tool_name,
            "description": f"{tool_name} from {server_name}",
            "inputSchema": {"type": "object", "properties": {}},
        }
        for tool_name in tool_names
    ]
    conn.stop = MagicMock()
    return conn


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

    with patch.object(plugin, "_start_servers") as mock_start:
        plugin._connections = {"filesystem": conn}
        plugin._tool_schemas = [MagicMock(), MagicMock()]

        result = plugin.do_mount()

    mock_start.assert_called_once_with()
    assert result == {
        "servers": {"filesystem": True},
        "tool_count": 2,
    }


def test_reload_servers_restarts_connections_and_rebuilds_index() -> None:
    plugin = MCPPlugin()
    conn = _make_connection("filesystem", ["read_file"])
    plugin._connections = {"filesystem": conn}
    plugin._tool_index = {"read_file": ("filesystem", "read_file")}
    plugin._tool_schemas = [MagicMock()]

    with patch.object(plugin, "_start_servers") as mock_start:
        message = plugin.reload_servers()

    conn.stop.assert_called_once_with()
    mock_start.assert_called_once_with()
    assert plugin._connections == {}
    assert plugin._tool_index == {}
    assert plugin._tool_schemas == []
    assert message == "Reloaded 0 server(s), 0 tool(s) available."


def test_list_servers_exposes_alive_status_and_tool_names() -> None:
    plugin = MCPPlugin()
    alive_conn = _make_connection("filesystem", ["read_file", "write_file"])
    dead_conn = _make_connection("github", [])
    dead_conn.is_alive.return_value = False
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
