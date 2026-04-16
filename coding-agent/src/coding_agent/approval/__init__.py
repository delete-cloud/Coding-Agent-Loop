"""Approval system for tool execution.

This package provides:
- PolicyEngine: Check if tool execution needs approval
- ApprovalStore: In-memory storage for pending approval requests
"""

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.approval.policy import ApprovalPolicy, PolicyConfig, PolicyEngine
from coding_agent.approval.store import ApprovalStore, PendingRequest

__all__ = [
    "ApprovalPolicy",
    "ApprovalCoordinator",
    "PolicyConfig",
    "PolicyEngine",
    "ApprovalStore",
    "PendingRequest",
]
