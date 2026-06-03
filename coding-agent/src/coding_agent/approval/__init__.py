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
from coding_agent.approval.requests import (
    ApprovalRequestService,
    ApprovalRequestSession,
    approval_wait_response_projection,
)
from coding_agent.approval.runtime_messages import (
    ApprovalDecisionConsumer,
    ApprovalDecisionConsumptionResult,
    ApprovalDecisionService,
    ApprovalDecisionSession,
    PublishedApprovalDecision,
    approval_decision_message_id,
    approval_response_projection,
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
    "ApprovalDecisionService",
    "ApprovalDecisionSession",
    "ApprovalRequestService",
    "ApprovalRequestSession",
    "PublishedApprovalDecision",
    "approval_interaction_id",
    "approval_interaction_status",
    "approval_request_payload",
    "approval_response_payload",
    "approval_decision_message_id",
    "approval_response_projection",
    "approval_response_from_runtime_payload",
    "approval_wait_response_projection",
    "PolicyConfig",
    "PolicyEngine",
    "ApprovalStore",
    "PendingRequest",
]
