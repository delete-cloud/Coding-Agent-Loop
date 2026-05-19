from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .command_policy import CommandPolicyDecision, CommandPolicyVerdict
from .patch_plan import PatchPlan, PatchRiskLevel
from .safe_edit import SafeEditDecision


class ActionApprovalRoute(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class ActionApprovalReason(StrEnum):
    COMMAND_ALLOWED = "command_allowed"
    COMMAND_DENIED = "command_denied"
    COMMAND_POLICY_REQUIRES_APPROVAL = "command_policy_requires_approval"
    FILE_EDIT_ALLOWED = "file_edit_allowed"
    FILE_EDIT_DENIED = "file_edit_denied"
    HIGH_RISK_PATCH = "high_risk_patch"


@dataclass(frozen=True)
class ActionApprovalRoutingResult:
    route: ActionApprovalRoute
    reasons: tuple[ActionApprovalReason, ...]
    action_kind: str
    risk_level: str | None = None
    policy_decision: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "reasons": [reason.value for reason in self.reasons],
            "action_kind": self.action_kind,
            "risk_level": self.risk_level,
            "policy_decision": self.policy_decision,
        }


def route_command_action(
    verdict: CommandPolicyVerdict,
) -> ActionApprovalRoutingResult:
    if verdict.decision == CommandPolicyDecision.DENY:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.DENY,
            reasons=(ActionApprovalReason.COMMAND_DENIED,),
            action_kind="command",
            policy_decision=verdict.decision.value,
        )
    if verdict.decision == CommandPolicyDecision.APPROVAL_REQUIRED:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.APPROVAL_REQUIRED,
            reasons=(ActionApprovalReason.COMMAND_POLICY_REQUIRES_APPROVAL,),
            action_kind="command",
            policy_decision=verdict.decision.value,
        )
    return ActionApprovalRoutingResult(
        route=ActionApprovalRoute.ALLOW,
        reasons=(ActionApprovalReason.COMMAND_ALLOWED,),
        action_kind="command",
        policy_decision=verdict.decision.value,
    )


def route_file_patch_action(
    plan: PatchPlan,
    safe_edit_decision: SafeEditDecision,
) -> ActionApprovalRoutingResult:
    if not safe_edit_decision.allowed:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.DENY,
            reasons=(ActionApprovalReason.FILE_EDIT_DENIED,),
            action_kind="file_patch",
            risk_level=plan.risk_level.value,
        )
    if plan.risk_level == PatchRiskLevel.HIGH:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.APPROVAL_REQUIRED,
            reasons=(ActionApprovalReason.HIGH_RISK_PATCH,),
            action_kind="file_patch",
            risk_level=plan.risk_level.value,
        )
    return ActionApprovalRoutingResult(
        route=ActionApprovalRoute.ALLOW,
        reasons=(ActionApprovalReason.FILE_EDIT_ALLOWED,),
        action_kind="file_patch",
        risk_level=plan.risk_level.value,
    )


def route_file_edit_action(
    decision: SafeEditDecision,
    *,
    risk_level: PatchRiskLevel,
) -> ActionApprovalRoutingResult:
    if not decision.allowed:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.DENY,
            reasons=(ActionApprovalReason.FILE_EDIT_DENIED,),
            action_kind="file_edit",
            risk_level=risk_level.value,
        )
    if risk_level == PatchRiskLevel.HIGH:
        return ActionApprovalRoutingResult(
            route=ActionApprovalRoute.APPROVAL_REQUIRED,
            reasons=(ActionApprovalReason.HIGH_RISK_PATCH,),
            action_kind="file_edit",
            risk_level=risk_level.value,
        )
    return ActionApprovalRoutingResult(
        route=ActionApprovalRoute.ALLOW,
        reasons=(ActionApprovalReason.FILE_EDIT_ALLOWED,),
        action_kind="file_edit",
        risk_level=risk_level.value,
    )
