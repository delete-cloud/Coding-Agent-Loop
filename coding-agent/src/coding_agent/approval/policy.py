"""Approval policy framework for tool execution.

Provides policy definitions and PolicyEngine to determine if tools need approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ApprovalPolicy(Enum):
    """Policy types for tool execution approval.
    
    - YOLO: Auto-approve all tools
    - INTERACTIVE: Always ask for approval
    - AUTO: Auto-approve safe tools only
    """
    YOLO = "yolo"
    INTERACTIVE = "interactive"
    AUTO = "auto"


@dataclass
class PolicyConfig:
    """Configuration for approval policy.
    
    Attributes:
        policy: The approval policy to use
        safe_tools: Set of tool names considered safe (for AUTO mode)
        timeout_seconds: Timeout for approval requests
    """
    policy: ApprovalPolicy
    safe_tools: set[str] = field(default_factory=lambda: {
        "file_read", "repo_list", "git_status"
    })
    timeout_seconds: int = 120


class PolicyEngine:
    """Check if tool execution needs approval based on policy."""
    
    def __init__(self, config: PolicyConfig):
        """Initialize with policy configuration.
        
        Args:
            config: Policy configuration
        """
        self.config = config
    
    def needs_approval(self, tool_name: str) -> bool:
        """Check if tool needs user approval.
        
        Args:
            tool_name: Name of the tool to check
            
        Returns:
            True if approval is required, False otherwise
        """
        if self.config.policy == ApprovalPolicy.YOLO:
            return False
        elif self.config.policy == ApprovalPolicy.INTERACTIVE:
            return True
        elif self.config.policy == ApprovalPolicy.AUTO:
            return tool_name not in self.config.safe_tools
        return True
