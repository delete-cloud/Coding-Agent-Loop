from __future__ import annotations

import json
from pathlib import Path

from coding_agent.action_safety import (
    ActionApprovalReason,
    ActionApprovalRoute,
    PatchRiskLevel,
    build_patch_plan,
    evaluate_command_policy,
    route_command_action,
    route_file_edit_action,
    route_file_patch_action,
    validate_safe_edit_path,
)


def test_high_risk_file_or_command_action_routes_to_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "src").mkdir()
    _ = (workspace / "src" / "large.py").write_text("before\n", encoding="utf-8")

    command_verdict = evaluate_command_policy(
        "rm -rf build",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    command_route = route_command_action(command_verdict)

    assert command_route.route == ActionApprovalRoute.APPROVAL_REQUIRED
    assert command_route.reasons == (
        ActionApprovalReason.COMMAND_POLICY_REQUIRES_APPROVAL,
    )

    patch = "\n".join(
        [
            "@@ -1,201 +0,0 @@",
            *[f"-line {index}" for index in range(201)],
        ]
    )
    safe_edit_decision = validate_safe_edit_path(
        workspace, "src/large.py", allow_create=True
    )
    patch_plan = build_patch_plan("src/large.py", patch, file_exists=True)
    patch_route = route_file_patch_action(patch_plan, safe_edit_decision)

    assert patch_plan.risk_level == PatchRiskLevel.HIGH
    assert patch_route.route == ActionApprovalRoute.APPROVAL_REQUIRED
    assert patch_route.reasons == (ActionApprovalReason.HIGH_RISK_PATCH,)


def test_denied_command_or_file_action_does_not_route_to_approval(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    command_verdict = evaluate_command_policy(
        "echo hello && rm -rf /",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    file_decision = validate_safe_edit_path(workspace, outside / "secret.txt")
    patch_plan = build_patch_plan(
        "../secret.txt",
        "@@ -1,1 +1,1 @@\n-before\n+after",
        file_exists=True,
    )

    assert route_command_action(command_verdict).route == ActionApprovalRoute.DENY
    assert (
        route_file_edit_action(file_decision, risk_level=PatchRiskLevel.LOW).route
        == ActionApprovalRoute.DENY
    )
    assert (
        route_file_patch_action(patch_plan, file_decision).route
        == ActionApprovalRoute.DENY
    )


def test_allowed_low_risk_actions_do_not_require_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "app.py"
    _ = target.write_text("print('before')\n", encoding="utf-8")

    command_verdict = evaluate_command_policy(
        "pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    file_decision = validate_safe_edit_path(workspace, "app.py")
    patch_plan = build_patch_plan(
        "app.py",
        "@@ -1,1 +1,1 @@\n-print('before')\n+print('after')",
        file_exists=True,
    )

    assert route_command_action(command_verdict).route == ActionApprovalRoute.ALLOW
    assert (
        route_file_edit_action(file_decision, risk_level=PatchRiskLevel.LOW).route
        == ActionApprovalRoute.ALLOW
    )
    assert (
        route_file_patch_action(patch_plan, file_decision).route
        == ActionApprovalRoute.ALLOW
    )


def test_action_approval_route_safe_dict_omits_raw_paths_and_commands(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    route = route_command_action(
        evaluate_command_policy(
            "rm -rf SECRET_PATH",
            environment_kind="local",
            workspace_root=workspace,
            cwd=workspace,
        )
    )
    payload = route.to_safe_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["route"] == "approval_required"
    assert payload["action_kind"] == "command"
    assert "SECRET_PATH" not in serialized
    assert "rm -rf" not in serialized
