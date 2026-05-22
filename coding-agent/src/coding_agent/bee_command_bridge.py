"""Bee command intent bridge.

This module resolves Bee node command references to workspace-declared intents.
It does not execute commands or grant policy permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

from coding_agent.action_safety.approval_routing import (
    ActionApprovalRoute,
    ActionApprovalRoutingResult,
    route_command_action,
)
from coding_agent.action_safety.command_policy import (
    CommandPolicyVerdict,
    EnvironmentKind,
    evaluate_command_policy,
)
from coding_agent.action_safety.validation_runner import (
    ValidationCommandSpec,
    ValidationReport,
    ValidationRunner,
)
from coding_agent.bee_runtime import BeeNodeManifest
from coding_agent.bee_workspace import (
    BeeWorkspaceCommandIntent,
    BeeWorkspaceTemplate,
    load_bee_workspace_command_intents,
)

BeeCommandIntentResolutionStatus = Literal[
    "resolved",
    "missing_command_ref",
    "unknown_command_ref",
    "disabled_intent",
]
BeeCommandIntentPlanStatus = Literal[
    "ready",
    "policy_denied",
    "approval_required",
    "missing_command_ref",
    "unknown_command_ref",
    "disabled_intent",
]
BeeValidationBridgeStatus = Literal[
    "completed",
    "not_validation_node",
    "missing_command_ref",
    "unknown_command_ref",
    "disabled_intent",
    "policy_denied",
    "approval_required",
]


@dataclass(frozen=True)
class BeeCommandIntentResolution:
    status: BeeCommandIntentResolutionStatus
    template_id: str
    node_id: str
    command_ref: str | None
    intent: BeeWorkspaceCommandIntent | None = None
    reason: str | None = None
    will_execute: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "template_id": self.template_id,
            "node_id": self.node_id,
            "command_ref": self.command_ref,
            "reason": self.reason,
            "will_execute": self.will_execute,
        }
        if self.intent is not None:
            payload["intent"] = _intent_safe_dict(self.intent)
        return payload


@dataclass(frozen=True)
class BeeCommandIntentPlan:
    status: BeeCommandIntentPlanStatus
    resolution: BeeCommandIntentResolution
    policy: CommandPolicyVerdict | None = None
    approval_route: ActionApprovalRoutingResult | None = None
    will_execute: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "resolution": self.resolution.to_safe_dict(),
            "will_execute": self.will_execute,
        }
        if self.policy is not None:
            payload["policy"] = self.policy.to_safe_dict()
        if self.approval_route is not None:
            payload["approval_route"] = self.approval_route.to_safe_dict()
        return payload


@dataclass(frozen=True)
class BeeValidationBridgeResult:
    status: BeeValidationBridgeStatus
    plan: BeeCommandIntentPlan
    report: ValidationReport | None = None
    will_execute: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "plan": self.plan.to_safe_dict(),
            "will_execute": self.will_execute,
        }
        if self.report is not None:
            payload["validation_report"] = self.report.to_safe_dict()
        return payload


def resolve_bee_command_intent(
    *,
    template: BeeWorkspaceTemplate,
    node: BeeNodeManifest,
) -> BeeCommandIntentResolution:
    """Resolve a Bee node command_ref to a non-executing workspace intent."""

    command_ref = node.command_ref
    if command_ref is None:
        return BeeCommandIntentResolution(
            status="missing_command_ref",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=None,
            reason="node has no command_ref",
        )

    intents_by_name = {
        intent.name: intent for intent in load_bee_workspace_command_intents(template)
    }
    intent = intents_by_name.get(command_ref)
    if intent is None:
        return BeeCommandIntentResolution(
            status="unknown_command_ref",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=command_ref,
            reason="command_ref is not declared by template commands.yaml",
        )
    if intent.status == "disabled":
        return BeeCommandIntentResolution(
            status="disabled_intent",
            template_id=template.template_id,
            node_id=node.node_id,
            command_ref=command_ref,
            intent=intent,
            reason="declared command intent is disabled",
        )
    return BeeCommandIntentResolution(
        status="resolved",
        template_id=template.template_id,
        node_id=node.node_id,
        command_ref=command_ref,
        intent=intent,
        reason="command_ref resolved to workspace intent",
    )


def plan_bee_command_intent(
    *,
    template: BeeWorkspaceTemplate,
    node: BeeNodeManifest,
    command: str,
    workspace_root: Path | str,
    cwd: Path | str | None = None,
    environment_kind: EnvironmentKind = "local",
    timeout_seconds: int = 120,
) -> BeeCommandIntentPlan:
    """Evaluate a resolved Bee command intent through existing policy gates.

    The command candidate is supplied by the caller and is never read from
    commands.yaml. This function only returns a policy plan; it never executes.
    """

    resolution = resolve_bee_command_intent(template=template, node=node)
    if resolution.status != "resolved":
        return BeeCommandIntentPlan(status=resolution.status, resolution=resolution)

    intent = resolution.intent
    if intent is None:
        raise ValueError("resolved Bee command intent is missing intent metadata")
    policy = evaluate_command_policy(
        command,
        environment_kind=environment_kind,
        workspace_root=workspace_root,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        validation_command=(
            node.kind == "validation"
            or intent.category == "validation"
            or intent.profile == "validation"
        ),
    )
    approval_route = route_command_action(policy)
    if approval_route.route == ActionApprovalRoute.DENY:
        return BeeCommandIntentPlan(
            status="policy_denied",
            resolution=resolution,
            policy=policy,
            approval_route=approval_route,
        )
    if approval_route.route == ActionApprovalRoute.APPROVAL_REQUIRED:
        return BeeCommandIntentPlan(
            status="approval_required",
            resolution=resolution,
            policy=policy,
            approval_route=approval_route,
        )
    return BeeCommandIntentPlan(
        status="ready",
        resolution=resolution,
        policy=policy,
        approval_route=approval_route,
    )


def run_bee_validation_node(
    *,
    template: BeeWorkspaceTemplate,
    node: BeeNodeManifest,
    command: str,
    workspace_root: Path | str,
    cwd: Path | str | None = None,
    environment_kind: EnvironmentKind = "local",
    timeout_seconds: int = 120,
    runner: ValidationRunner | None = None,
) -> BeeValidationBridgeResult:
    """Run a validation Bee node through the existing validation runner."""

    resolution = resolve_bee_command_intent(template=template, node=node)
    if node.kind != "validation":
        return BeeValidationBridgeResult(
            status="not_validation_node",
            plan=BeeCommandIntentPlan(status=resolution.status, resolution=resolution),
        )
    plan = plan_bee_command_intent(
        template=template,
        node=node,
        command=command,
        workspace_root=workspace_root,
        cwd=cwd,
        environment_kind=environment_kind,
        timeout_seconds=timeout_seconds,
    )
    if plan.status != "ready":
        return BeeValidationBridgeResult(status=plan.status, plan=plan)
    intent = plan.resolution.intent
    if intent is None:
        raise ValueError("ready Bee validation plan is missing intent metadata")
    if intent.category != "validation":
        return BeeValidationBridgeResult(status="not_validation_node", plan=plan)

    validation_runner = runner if runner is not None else ValidationRunner()
    report = validation_runner.run(
        [
            ValidationCommandSpec(
                label=intent.validation_label or intent.name,
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
        ],
        workspace_root=workspace_root,
        environment_kind=environment_kind,
    )
    return BeeValidationBridgeResult(
        status="completed",
        plan=plan,
        report=report,
    )


def _intent_safe_dict(intent: BeeWorkspaceCommandIntent) -> dict[str, Any]:
    return {
        "name": intent.name,
        "profile": intent.profile,
        "policy": intent.policy,
        "category": intent.category,
        "validation_label": intent.validation_label,
        "status": intent.status,
    }
