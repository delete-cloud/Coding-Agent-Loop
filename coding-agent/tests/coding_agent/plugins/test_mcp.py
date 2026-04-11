"""Tests for MCPPlugin — tool routing with duplicate/conflicting tool names."""

import pytest
from unittest.mock import MagicMock, patch

from coding_agent.plugins.mcp import MCPPlugin, MCPServerConfig, _MCPConnection


def _make_mock_connection(server_name: str, tool_names: list[str]) -> _MCPConnection:
    """Build a mock _MCPConnection with the given tool descriptors."""
    conn = MagicMock(spec=_MCPConnection)
    conn.cfg = MCPServerConfig(name=server_name, command="echo")
    conn.is_alive.return_value = True
    conn.tools = [
        {
            "name": t,
            "description": f"{t} from {server_name}",
            "inputSchema": {"type": "object", "properties": {}},
        }
        for t in tool_names
    ]
    conn.call_tool = MagicMock(return_value="ok")
    return conn


class TestMCPToolRouting:
    def test_namespaced_tool_routes_to_raw_name(self):
        """When two servers expose the same tool name, the namespaced variant
        must pass the *raw* name to conn.call_tool(), not the namespaced key."""
        plugin = MCPPlugin()
        conn_a = _make_mock_connection("server_a", ["read_file"])
        conn_b = _make_mock_connection("server_b", ["read_file"])
        plugin._connections = {"server_a": conn_a, "server_b": conn_b}

        plugin._rebuild_tool_index()

        namespaced_key = "server_b__read_file"
        assert namespaced_key in plugin._tool_index

        result = plugin.execute_tool(name=namespaced_key, arguments={"path": "/tmp"})

        conn_b.call_tool.assert_called_once_with("read_file", {"path": "/tmp"})

    def test_non_conflicting_tool_routes_correctly(self):
        """A unique tool name should pass through unchanged."""
        plugin = MCPPlugin()
        conn_a = _make_mock_connection("server_a", ["unique_tool"])
        plugin._connections = {"server_a": conn_a}

        plugin._rebuild_tool_index()

        result = plugin.execute_tool(name="unique_tool", arguments={})

        conn_a.call_tool.assert_called_once_with("unique_tool", {})
