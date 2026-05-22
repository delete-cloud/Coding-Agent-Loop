from __future__ import annotations

from pathlib import Path

from coding_agent.bee_command_bridge import resolve_bee_command_intent
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


def _write_template_with_commands(
    tmp_path: Path,
    *,
    command_ref: str | None,
    intent_status: str,
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
        "    kind: validation",
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
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: pytest_smoke",
                "    profile: validation",
                "    policy: existing_command_policy",
                "    category: validation",
                "    validation_label: pytest_smoke",
                f"    status: {intent_status}",
                "    metadata:",
                "      owner: local",
            ]
        ),
        encoding="utf-8",
    )
    return load_bee_workspace_template(tmp_path, "template-alpha")
