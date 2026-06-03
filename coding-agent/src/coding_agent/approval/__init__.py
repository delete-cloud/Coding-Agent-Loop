"""Approval system for tool execution.

This package provides:
- PolicyEngine: Check if tool execution needs approval
- ApprovalStore: In-memory storage for pending approval requests
- ApprovalDecisionConsumer: Product-side runtime message consumer
"""

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.approval.interactions import (
    ApprovalInteractionService,
    ApprovalInteractionSession,
    approval_interaction_id,
    approval_interaction_status,
    approval_request_payload,
    approval_response_payload,
)
from coding_agent.approval.policy import ApprovalPolicy, PolicyConfig, PolicyEngine
from coding_agent.approval.runtime_messages import (
    ApprovalDecisionConsumer,
    ApprovalDecisionConsumptionResult,
    approval_response_from_runtime_payload,
)
from coding_agent.approval.store import ApprovalStore, PendingRequest

__all__ = [
    "ApprovalPolicy",
    "ApprovalCoordinator",
    "ApprovalInteractionService",
    "ApprovalInteractionSession",
    "ApprovalDecisionConsumer",
    "ApprovalDecisionConsumptionResult",
    "approval_interaction_id",
    "approval_interaction_status",
    "approval_request_payload",
    "approval_response_payload",
    "approval_response_from_runtime_payload",
    "PolicyConfig",
    "PolicyEngine",
    "ApprovalStore",
    "PendingRequest",
]
