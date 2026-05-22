from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.action_safety import ValidationStatus
from coding_agent.bee_command_bridge import (
    BeeNodeCompletionEvidence,
    complete_bee_node_from_bridge_result,
    plan_bee_command_intent,
    resolve_bee_command_intent,
    run_bee_validation_node,
)
from coding_agent.bee_workspace import (
    build_bee_manifest_from_workspace_template,
    load_bee_workspace_template,
)


def test_bee_command_bridge_resolves_declared_intent_without_executing_yaml(
    tmp_path: Path,
) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    resolution = resolve_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
    )

    assert resolution.status == "resolved"
    assert resolution.template_id == "template-alpha"
    assert resolution.node_id == "node-validate"
    assert resolution.command_ref == "pytest_smoke"
    assert resolution.intent is not None
    assert resolution.intent.name == "pytest_smoke"
    assert resolution.intent.profile == "validation"
    assert resolution.intent.policy == "existing_command_policy"
    assert resolution.intent.category == "validation"
    assert resolution.intent.validation_label == "pytest_smoke"
    assert resolution.intent.metadata == {"owner": "local"}
    assert resolution.will_execute is False


def test_bee_command_bridge_fails_closed_without_command_ref(
    tmp_path: Path,
) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref=None,
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    resolution = resolve_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
    )

    assert resolution.status == "missing_command_ref"
    assert resolution.command_ref is None
    assert resolution.intent is None
    assert resolution.reason == "node has no command_ref"
    assert resolution.will_execute is False


def test_bee_command_bridge_fails_closed_for_unknown_command_ref(
    tmp_path: Path,
) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="missing_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    resolution = resolve_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
    )

    assert resolution.status == "unknown_command_ref"
    assert resolution.command_ref == "missing_smoke"
    assert resolution.intent is None
    assert resolution.reason == "command_ref is not declared by template commands.yaml"
    assert resolution.will_execute is False


def test_bee_command_bridge_fails_closed_for_disabled_intent(
    tmp_path: Path,
) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="pytest_smoke",
        intent_status="disabled",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    resolution = resolve_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
    )

    assert resolution.status == "disabled_intent"
    assert resolution.command_ref == "pytest_smoke"
    assert resolution.intent is not None
    assert resolution.intent.status == "disabled"
    assert resolution.reason == "declared command intent is disabled"
    assert resolution.will_execute is False


def test_bee_command_bridge_denies_policy_blocked_intent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="echo hello && rm -rf /",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert plan.status == "policy_denied"
    assert plan.policy is not None
    assert plan.policy.decision == "deny"
    assert plan.approval_route is not None
    assert plan.approval_route.route == "deny"
    assert plan.will_execute is False
    safe_payload = plan.to_safe_dict()
    serialized = json.dumps(safe_payload, sort_keys=True)
    assert safe_payload["policy"]["decision"] == "deny"
    assert "echo hello" not in serialized
    assert "rm -rf" not in serialized


def test_bee_command_bridge_returns_approval_required_without_execution(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="rm -rf build",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert plan.status == "approval_required"
    assert plan.policy is not None
    assert plan.policy.decision == "approval_required"
    assert plan.approval_route is not None
    assert plan.approval_route.route == "approval_required"
    assert plan.will_execute is False
    safe_payload = plan.to_safe_dict()
    serialized = json.dumps(safe_payload, sort_keys=True)
    assert safe_payload["approval_route"]["route"] == "approval_required"
    assert "rm -rf" not in serialized
    assert "build" not in serialized


def test_bee_command_bridge_ready_plan_still_does_not_execute(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert plan.status == "ready"
    assert plan.policy is not None
    assert plan.policy.decision == "allow"
    assert plan.approval_route is not None
    assert plan.approval_route.route == "allow"
    assert plan.will_execute is False


def test_bee_command_bridge_safe_summary_omits_intent_metadata(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
        metadata_owner="rm -rf build",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert plan.resolution.intent is not None
    assert plan.resolution.intent.metadata == {"owner": "rm -rf build"}
    serialized = json.dumps(plan.to_safe_dict(), sort_keys=True)
    assert "metadata" not in serialized
    assert "rm -rf" not in serialized
    assert "build" not in serialized


def test_bee_validation_node_uses_validation_runner(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command='python -c "raise SystemExit(0)"',
        workspace_root=workspace,
        cwd=workspace,
    )

    assert result.status == "completed"
    assert result.report is not None
    assert result.report.status == ValidationStatus.PASSED
    assert result.report.outcomes[0].label == "pytest_smoke"
    assert result.report.outcomes[0].policy.decision == "allow"
    assert result.will_execute is False
    serialized = json.dumps(result.to_safe_dict(), sort_keys=True)
    assert "python -c" not in serialized
    assert "raise SystemExit" not in serialized


def test_bee_validation_node_does_not_execute_denied_command(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    marker = workspace / "marker"
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command=f'python -c "print(1)" > {marker}',
        workspace_root=workspace,
        cwd=workspace,
    )

    assert result.status == "policy_denied"
    assert result.report is None
    assert result.will_execute is False
    assert not marker.exists()


def test_bee_validation_node_does_not_execute_approval_required_command(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command="rm -rf build",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert result.status == "approval_required"
    assert result.report is None
    assert result.will_execute is False


def test_bee_validation_runner_rejects_non_validation_node(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
        node_kind="analysis",
        intent_category="analysis",
        validation_label=None,
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert result.status == "not_validation_node"
    assert result.report is None
    assert result.will_execute is False


def test_bee_validation_runner_rejects_non_validation_node_with_validation_intent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    marker = workspace / "marker"
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
        node_kind="analysis",
        intent_category="validation",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command=f'python -c "from pathlib import Path; Path({str(marker)!r}).touch()"',
        workspace_root=workspace,
        cwd=workspace,
    )

    assert result.status == "not_validation_node"
    assert result.report is None
    assert result.will_execute is False
    assert not marker.exists()


def test_bee_validation_runner_preserves_local_only_validation_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    with pytest.raises(ValueError, match="local execution"):
        run_bee_validation_node(
            template=template,
            node=manifest.nodes[0],
            command="pytest -q",
            workspace_root="/workspace",
            cwd="/workspace",
            environment_kind="cloud",
        )


def test_bee_node_completion_requires_evidence(tmp_path: Path) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    decision = complete_bee_node_from_bridge_result(node=manifest.nodes[0])

    assert decision.status == "evidence_required"
    assert decision.will_complete is False
    assert decision.evidence == ()
    assert decision.reason == "Bee node completion requires evidence"


def test_bee_node_completion_accepts_passed_validation_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)
    bridge_result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command='python -c "raise SystemExit(0)"',
        workspace_root=workspace,
        cwd=workspace,
    )

    decision = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        bridge_result=bridge_result,
    )

    assert decision.status == "completed"
    assert decision.will_complete is True
    assert decision.evidence == (
        BeeNodeCompletionEvidence(
            evidence_kind="validation_report",
            evidence_ref="node-validate:passed:pytest_smoke",
            status="passed",
        ),
    )
    serialized = json.dumps(decision.to_safe_dict(), sort_keys=True)
    assert "evidence_ref_hash" in serialized
    assert "node-validate:passed:pytest_smoke" not in serialized
    assert "python -c" not in serialized
    assert "raise SystemExit" not in serialized


def test_bee_node_completion_rejects_failed_validation_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)
    bridge_result = run_bee_validation_node(
        template=template,
        node=manifest.nodes[0],
        command='python -c "raise SystemExit(3)"',
        workspace_root=workspace,
        cwd=workspace,
    )

    decision = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        bridge_result=bridge_result,
    )

    assert decision.status == "evidence_failed"
    assert decision.will_complete is False
    assert decision.evidence[0].status == "failed"


def test_bee_node_completion_rejects_policy_only_ready_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(
        workspace,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)
    plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )

    decision = complete_bee_node_from_bridge_result(node=manifest.nodes[0])

    assert plan.status == "ready"
    assert decision.status == "evidence_required"
    assert decision.will_complete is False


def test_bee_node_completion_rejects_model_text_evidence() -> None:
    with pytest.raises(ValueError, match="evidence kind"):
        BeeNodeCompletionEvidence(
            evidence_kind="model_text",
            evidence_ref="looks done",
            status="accepted",
        )


def test_bee_node_completion_rejects_failed_caller_evidence(tmp_path: Path) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    decision = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        evidence=(
            BeeNodeCompletionEvidence(
                evidence_kind="validation_report",
                evidence_ref="node-validate:failed:pytest_smoke",
                status="failed",
            ),
        ),
    )

    assert decision.status == "evidence_failed"
    assert decision.will_complete is False


def test_bee_node_completion_safe_summary_hashes_evidence_ref(
    tmp_path: Path,
) -> None:
    template = _write_template_with_commands(
        tmp_path,
        command_ref="pytest_smoke",
        intent_status="declared",
    )
    manifest = build_bee_manifest_from_workspace_template(template)

    decision = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        evidence=(
            BeeNodeCompletionEvidence(
                evidence_kind="sanitized_artifact",
                evidence_ref="python -c print-secret",
                status="accepted",
            ),
        ),
    )

    assert decision.status == "completed"
    serialized = json.dumps(decision.to_safe_dict(), sort_keys=True)
    assert "evidence_ref_hash" in serialized
    assert "python -c" not in serialized
    assert "print-secret" not in serialized


def _write_template_with_commands(
    tmp_path: Path,
    *,
    command_ref: str | None,
    intent_status: str,
    metadata_owner: str = "local",
    node_kind: str = "validation",
    intent_category: str = "validation",
    validation_label: str | None = "pytest_smoke",
):
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    command_ref_line = (
        f"    command_ref: {command_ref}" if command_ref is not None else ""
    )
    metadata_lines = [
        "version: 1",
        "template_id: template-alpha",
        "kind: maintenance",
        "profile: local",
        "title: Local template alpha",
        "summary: Safe local template",
        "topic:",
        "  session_id: session-alpha",
        "context_profile: default",
        "validation_profile: smoke",
        "workspace_policy: local",
        "nodes:",
        "  - node_id: node-validate",
        f"    kind: {node_kind}",
        "    profile: smoke",
        "    title: Run smoke validation",
    ]
    if command_ref_line:
        metadata_lines.append(command_ref_line)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(metadata_lines),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n",
        encoding="utf-8",
    )
    command_lines = [
        "commands:",
        "  - name: pytest_smoke",
        "    profile: validation",
        "    policy: existing_command_policy",
        f"    category: {intent_category}",
    ]
    if validation_label is not None:
        command_lines.append(f"    validation_label: {validation_label}")
    command_lines.extend(
        [
            f"    status: {intent_status}",
            "    metadata:",
            f"      owner: {metadata_owner}",
        ]
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join(command_lines),
        encoding="utf-8",
    )
    return load_bee_workspace_template(tmp_path, "template-alpha")
