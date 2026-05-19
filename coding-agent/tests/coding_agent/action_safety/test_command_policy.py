from __future__ import annotations

import json
from pathlib import Path

from coding_agent.action_safety import (
    CommandPolicyDecision,
    CommandPolicyReason,
    evaluate_command_policy,
)


def test_command_policy_classifies_allow_deny_and_approval_required(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    allowed = evaluate_command_policy(
        "pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    denied = evaluate_command_policy(
        "echo hello && rm -rf /",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    approval = evaluate_command_policy(
        "rm -rf build",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )

    assert allowed.decision == CommandPolicyDecision.ALLOW
    assert allowed.reasons == (CommandPolicyReason.SAFE_COMMAND,)
    assert denied.decision == CommandPolicyDecision.DENY
    assert CommandPolicyReason.SHELL_SYNTAX in denied.reasons
    assert approval.decision == CommandPolicyDecision.APPROVAL_REQUIRED
    assert approval.reasons == (CommandPolicyReason.DESTRUCTIVE_COMMAND,)


def test_command_policy_applies_to_local_and_cloud_shell_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    local = evaluate_command_policy(
        "uv run pytest tests -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        validation_command=True,
    )
    cloud = evaluate_command_policy(
        "uv run pytest tests -q",
        environment_kind="cloud",
        workspace_root="/workspace",
        cwd="/workspace",
        validation_command=True,
    )

    assert local.decision == CommandPolicyDecision.ALLOW
    assert cloud.decision == CommandPolicyDecision.ALLOW
    assert local.reasons == (CommandPolicyReason.VALIDATION_COMMAND,)
    assert cloud.reasons == (CommandPolicyReason.VALIDATION_COMMAND,)
    assert local.environment_kind == "local"
    assert cloud.environment_kind == "cloud"


def test_command_policy_denies_cwd_path_env_and_timeout_risks(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    cwd_escape = evaluate_command_policy(
        "pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=outside,
    )
    path_escape = evaluate_command_policy(
        "python /etc/passwd",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    bad_env = evaluate_command_policy(
        "pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        env={"BAD-NAME": "1"},
    )
    too_long = evaluate_command_policy(
        "pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        timeout_seconds=601,
    )

    assert cwd_escape.decision == CommandPolicyDecision.DENY
    assert cwd_escape.reasons == (CommandPolicyReason.CWD_ESCAPE,)
    assert path_escape.decision == CommandPolicyDecision.DENY
    assert path_escape.reasons == (CommandPolicyReason.PATH_ESCAPE,)
    assert bad_env.decision == CommandPolicyDecision.DENY
    assert bad_env.reasons == (CommandPolicyReason.UNSAFE_ENV_KEY,)
    assert too_long.decision == CommandPolicyDecision.DENY
    assert too_long.reasons == (CommandPolicyReason.TIMEOUT_EXCEEDS_LIMIT,)


def test_command_policy_safe_dict_omits_raw_arguments(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    verdict = evaluate_command_policy(
        "python -c 'print(\"SECRET_VALUE\")'",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    payload = verdict.to_safe_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["command_name"] == "python"
    assert "SECRET_VALUE" not in serialized
    assert "-c" not in serialized


def test_validation_command_does_not_bypass_approval_risks(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    verdict = evaluate_command_policy(
        "git reset --hard",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        validation_command=True,
    )

    assert verdict.decision == CommandPolicyDecision.APPROVAL_REQUIRED
    assert verdict.reasons == (CommandPolicyReason.DESTRUCTIVE_COMMAND,)

    install_verdict = evaluate_command_policy(
        "uv pip install package",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        validation_command=True,
    )

    assert install_verdict.decision == CommandPolicyDecision.APPROVAL_REQUIRED
    assert install_verdict.reasons == (CommandPolicyReason.NETWORK_COMMAND,)


def test_command_policy_denies_raw_shell_syntax_for_cloud() -> None:
    verdict = evaluate_command_policy(
        "echo hi&&rm -rf build",
        environment_kind="cloud",
        workspace_root="/workspace",
        cwd="/workspace",
    )

    assert verdict.decision == CommandPolicyDecision.DENY
    assert verdict.reasons == (CommandPolicyReason.SHELL_SYNTAX,)


def test_command_policy_safe_dict_sanitizes_command_name(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    verdict = evaluate_command_policy(
        "SECRET=abc pytest -q",
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
    )
    payload = verdict.to_safe_dict()
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["command_name"] is None
    assert "SECRET=abc" not in serialized


def test_command_policy_denies_absolute_executable_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside_executable = tmp_path / "pytest"
    outside_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    verdict = evaluate_command_policy(
        str(outside_executable),
        environment_kind="local",
        workspace_root=workspace,
        cwd=workspace,
        validation_command=True,
    )

    assert verdict.decision == CommandPolicyDecision.DENY
    assert verdict.reasons == (CommandPolicyReason.PATH_ESCAPE,)
