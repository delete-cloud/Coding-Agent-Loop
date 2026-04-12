import pytest
from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy
from agentkit.directive.types import Approve, Reject, AskUser


class TestApprovalPlugin:
    def test_state_key(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.YOLO)
        assert plugin.state_key == "approval"

    def test_hooks_include_approve_tool_call(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.YOLO)
        hooks = plugin.hooks()
        assert "approve_tool_call" in hooks

    def test_yolo_policy_approves_all(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.YOLO)
        result = plugin.approve_tool_call(tool_name="bash_run", arguments={"cmd": "ls"})
        assert isinstance(result, Approve)

    def test_interactive_policy_asks_user(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.INTERACTIVE)
        result = plugin.approve_tool_call(
            tool_name="bash_run", arguments={"cmd": "rm -rf /"}
        )
        assert isinstance(result, AskUser)

    def test_auto_policy_approves_safe_tools_only(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.AUTO,
            safe_tools={"file_read", "grep_search"},
        )
        assert isinstance(
            plugin.approve_tool_call(tool_name="file_read", arguments={}),
            Approve,
        )
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            AskUser,
        )

    def test_blocklist_rejects(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.YOLO,
            blocked_tools={"dangerous_tool"},
        )
        result = plugin.approve_tool_call(tool_name="dangerous_tool", arguments={})
        assert isinstance(result, Reject)

    def test_yolo_policy_allows_external_request_tool(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.YOLO)
        result = plugin.approve_tool_call(
            tool_name="web_search",
            arguments={"query": "agentkit"},
        )
        assert isinstance(result, Approve)

    def test_interactive_policy_requires_approval_for_external_request_tool(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.INTERACTIVE)
        result = plugin.approve_tool_call(
            tool_name="web_search",
            arguments={"query": "agentkit"},
        )
        assert isinstance(result, AskUser)

    def test_auto_policy_still_requires_approval_for_external_request_tool(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.AUTO,
            safe_tools={"file_read", "web_search"},
        )
        result = plugin.approve_tool_call(
            tool_name="web_search",
            arguments={"query": "agentkit"},
        )
        assert isinstance(result, AskUser)

    def test_ask_user_carries_tool_metadata(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.INTERACTIVE)
        result = plugin.approve_tool_call(
            tool_name="bash_run", arguments={"command": "ls"}
        )
        assert isinstance(result, AskUser)
        assert result.metadata is not None
        assert result.metadata["tool_name"] == "bash_run"
        assert result.metadata["arguments"] == {"command": "ls"}

    def test_external_request_ask_user_carries_metadata(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        result = plugin.approve_tool_call(
            tool_name="web_search", arguments={"query": "test"}
        )
        assert isinstance(result, AskUser)
        assert result.metadata is not None
        assert result.metadata["tool_name"] == "web_search"
        assert result.metadata["arguments"] == {"query": "test"}
