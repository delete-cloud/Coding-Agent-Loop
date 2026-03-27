"""Tests for approval policy module."""

import pytest
from coding_agent.approval.policy import ApprovalPolicy, PolicyConfig, PolicyEngine


class TestApprovalPolicyEnum:
    """Tests for ApprovalPolicy enum."""

    def test_yolo_value(self):
        """YOLO policy has correct value."""
        assert ApprovalPolicy.YOLO.value == "yolo"

    def test_interactive_value(self):
        """INTERACTIVE policy has correct value."""
        assert ApprovalPolicy.INTERACTIVE.value == "interactive"

    def test_auto_value(self):
        """AUTO policy has correct value."""
        assert ApprovalPolicy.AUTO.value == "auto"


class TestPolicyConfig:
    """Tests for PolicyConfig dataclass."""

    def test_default_safe_tools(self):
        """Default safe tools are set correctly."""
        config = PolicyConfig(policy=ApprovalPolicy.AUTO)
        assert config.safe_tools == {"file_read", "repo_list", "git_status"}

    def test_default_timeout(self):
        """Default timeout is 120 seconds."""
        config = PolicyConfig(policy=ApprovalPolicy.AUTO)
        assert config.timeout_seconds == 120

    def test_custom_safe_tools(self):
        """Custom safe tools can be provided."""
        config = PolicyConfig(
            policy=ApprovalPolicy.AUTO,
            safe_tools={"custom_tool"}
        )
        assert config.safe_tools == {"custom_tool"}

    def test_custom_timeout(self):
        """Custom timeout can be provided."""
        config = PolicyConfig(
            policy=ApprovalPolicy.AUTO,
            timeout_seconds=300
        )
        assert config.timeout_seconds == 300


class TestPolicyEngineYOLO:
    """Tests for PolicyEngine with YOLO policy."""

    def test_yolo_never_needs_approval(self):
        """YOLO policy never needs approval for any tool."""
        config = PolicyConfig(policy=ApprovalPolicy.YOLO)
        engine = PolicyEngine(config)
        
        assert engine.needs_approval("file_read") is False
        assert engine.needs_approval("shell_execute") is False
        assert engine.needs_approval("dangerous_tool") is False


class TestPolicyEngineInteractive:
    """Tests for PolicyEngine with INTERACTIVE policy."""

    def test_interactive_always_needs_approval(self):
        """INTERACTIVE policy always needs approval for any tool."""
        config = PolicyConfig(policy=ApprovalPolicy.INTERACTIVE)
        engine = PolicyEngine(config)
        
        assert engine.needs_approval("file_read") is True
        assert engine.needs_approval("shell_execute") is True
        assert engine.needs_approval("any_tool") is True


class TestPolicyEngineAuto:
    """Tests for PolicyEngine with AUTO policy."""

    def test_auto_safe_tools_no_approval(self):
        """AUTO policy does not need approval for safe tools."""
        config = PolicyConfig(policy=ApprovalPolicy.AUTO)
        engine = PolicyEngine(config)
        
        assert engine.needs_approval("file_read") is False
        assert engine.needs_approval("repo_list") is False
        assert engine.needs_approval("git_status") is False

    def test_auto_unsafe_tools_need_approval(self):
        """AUTO policy needs approval for unsafe tools."""
        config = PolicyConfig(policy=ApprovalPolicy.AUTO)
        engine = PolicyEngine(config)
        
        assert engine.needs_approval("shell_execute") is True
        assert engine.needs_approval("file_write") is True
        assert engine.needs_approval("dangerous_tool") is True

    def test_auto_custom_safe_tools(self):
        """AUTO policy respects custom safe tools list."""
        config = PolicyConfig(
            policy=ApprovalPolicy.AUTO,
            safe_tools={"my_safe_tool"}
        )
        engine = PolicyEngine(config)
        
        assert engine.needs_approval("my_safe_tool") is False
        assert engine.needs_approval("file_read") is True  # Not in custom list
