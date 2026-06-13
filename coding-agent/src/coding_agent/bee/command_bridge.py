"""Bee command intent bridge.

This module resolves Bee node command references to workspace-declared intents.
It does not execute commands or grant policy permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
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
    ValidationStatus,
    ValidationRunner,
)
from coding_agent.bee.runtime import BeeNodeManifest
from coding_agent.bee.workspace import (
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
BeeNodeCompletionStatus = Literal[
    "completed",
    "evidence_required",
    "evidence_failed",
]
_ALLOWED_COMPLETION_EVIDENCE_KINDS = frozenset(
    {
        "action_record",
        "sanitized_artifact",
        "validation_report",
    }
)
_BEE_COMMAND_PLAN_AUTHORIZATION_TOKEN: object = object()


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
    authorization_token: object | None = field(default=None, repr=False, compare=False)
    authorization_signature: str | None = field(
        default=None, init=False, repr=False, compare=False
    )

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


@dataclass(frozen=True)
class BeeNodeCompletionEvidence:
    evidence_kind: str
    evidence_ref: str
    status: str

    def __post_init__(self) -> None:
        if not self.evidence_kind.strip():
            raise ValueError("Bee completion evidence kind must not be empty")
        if not self.evidence_ref.strip():
            raise ValueError("Bee completion evidence ref must not be empty")
        if not self.status.strip():
            raise ValueError("Bee completion evidence status must not be empty")
        if self.evidence_kind not in _ALLOWED_COMPLETION_EVIDENCE_KINDS:
            raise ValueError(
                f"Bee completion evidence kind is not supported: {self.evidence_kind}"
            )

    def to_safe_dict(self) -> dict[str, str]:
        return {
            "evidence_kind": self.evidence_kind,
            "evidence_ref_hash": _safe_ref_hash(self.evidence_ref),
            "status": self.status,
        }


@dataclass(frozen=True)
class BeeNodeCompletionDecision:
    status: BeeNodeCompletionStatus
    node_id: str
    evidence: tuple[BeeNodeCompletionEvidence, ...] = ()
    reason: str | None = None
    will_complete: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "node_id": self.node_id,
            "evidence": [item.to_safe_dict() for item in self.evidence],
            "reason": self.reason,
            "will_complete": self.will_complete,
        }


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
    return _authorize_bee_command_plan(
        BeeCommandIntentPlan(
            status="ready",
            resolution=resolution,
            policy=policy,
            approval_route=approval_route,
            authorization_token=_BEE_COMMAND_PLAN_AUTHORIZATION_TOKEN,
        )
    )


def _authorize_bee_command_plan(plan: BeeCommandIntentPlan) -> BeeCommandIntentPlan:
    object.__setattr__(
        plan,
        "authorization_signature",
        _bee_command_plan_signature(plan),
    )
    return plan


def is_authorized_bee_command_plan(plan: BeeCommandIntentPlan) -> bool:
    return (
        plan.authorization_token is _BEE_COMMAND_PLAN_AUTHORIZATION_TOKEN
        and plan.authorization_signature == _bee_command_plan_signature(plan)
    )


def _bee_command_plan_signature(plan: BeeCommandIntentPlan) -> str:
    intent = plan.resolution.intent
    policy = plan.policy
    approval_route = plan.approval_route
    parts = (
        plan.status,
        plan.resolution.status,
        plan.resolution.template_id,
        plan.resolution.node_id,
        plan.resolution.command_ref or "",
        intent.name if intent is not None else "",
        intent.profile if intent is not None else "",
        intent.policy if intent is not None else "",
        intent.category if intent is not None else "",
        intent.validation_label if intent is not None else "",
        intent.status if intent is not None else "",
        policy.decision.value if policy is not None else "",
        policy.command_name or "" if policy is not None else "",
        policy.environment_kind if policy is not None else "",
        str(policy.timeout_seconds) if policy is not None else "",
        approval_route.route.value if approval_route is not None else "",
        approval_route.policy_decision or "" if approval_route is not None else "",
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


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


def complete_bee_node_from_bridge_result(
    *,
    node: BeeNodeManifest,
    bridge_result: BeeValidationBridgeResult | None = None,
    evidence: tuple[BeeNodeCompletionEvidence, ...] = (),
) -> BeeNodeCompletionDecision:
    """Decide whether a Bee node may complete from evidence-backed results."""

    collected_evidence = list(evidence)
    failed_evidence = tuple(
        item for item in collected_evidence if item.status not in {"accepted", "passed"}
    )
    if failed_evidence:
        return BeeNodeCompletionDecision(
            status="evidence_failed",
            node_id=node.node_id,
            evidence=tuple(collected_evidence),
            reason="completion evidence did not pass",
        )
    if bridge_result is not None:
        report = bridge_result.report
        if bridge_result.status != "completed" or report is None:
            return BeeNodeCompletionDecision(
                status="evidence_required",
                node_id=node.node_id,
                evidence=tuple(collected_evidence),
                reason="bridge result did not produce completion evidence",
            )
        collected_evidence.append(
            BeeNodeCompletionEvidence(
                evidence_kind="validation_report",
                evidence_ref=_validation_evidence_ref(node, report),
                status=report.status.value,
            )
        )
        if report.status != ValidationStatus.PASSED:
            return BeeNodeCompletionDecision(
                status="evidence_failed",
                node_id=node.node_id,
                evidence=tuple(collected_evidence),
                reason="validation evidence did not pass",
            )

    if not collected_evidence:
        return BeeNodeCompletionDecision(
            status="evidence_required",
            node_id=node.node_id,
            reason="Bee node completion requires evidence",
        )
    return BeeNodeCompletionDecision(
        status="completed",
        node_id=node.node_id,
        evidence=tuple(collected_evidence),
        reason="Bee node completion evidence accepted",
        will_complete=True,
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


def _validation_evidence_ref(
    node: BeeNodeManifest,
    report: ValidationReport,
) -> str:
    labels = "-".join(outcome.label for outcome in report.outcomes)
    return f"{node.node_id}:{report.status.value}:{labels}"


def _safe_ref_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
