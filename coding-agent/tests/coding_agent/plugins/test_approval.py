import pytest
from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy
from agentkit.directive.types import Approve, Reject, AskUser


class TestApprovalPlugin:
    def test_state_key(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        assert plugin.state_key == "approval"

    def test_hooks_include_approve_tool_call(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        hooks = plugin.hooks()
        assert "approve_tool_call" in hooks

    def test_auto_policy_approves_all(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO)
        result = plugin.approve_tool_call(tool_name="bash_run", arguments={"cmd": "ls"})
        assert isinstance(result, Approve)

    def test_manual_policy_asks_user(self):
        plugin = ApprovalPlugin(policy=ApprovalPolicy.MANUAL)
        result = plugin.approve_tool_call(
            tool_name="bash_run", arguments={"cmd": "rm -rf /"}
        )
        assert isinstance(result, AskUser)

    def test_safe_only_approves_safe_tools(self):
        plugin = ApprovalPlugin(
            policy=ApprovalPolicy.SAFE_ONLY,
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
            policy=ApprovalPolicy.AUTO,
            blocked_tools={"dangerous_tool"},
        )
        result = plugin.approve_tool_call(tool_name="dangerous_tool", arguments={})
        assert isinstance(result, Reject)
