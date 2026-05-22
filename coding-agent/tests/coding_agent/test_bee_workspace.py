from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.bee_workspace import (
    discover_bee_workspace_templates,
    load_bee_workspace_template,
)


def test_bee_workspace_discovers_template_metadata(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
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
                "  - node_id: node-plan",
                "    kind: analysis",
                "    profile: default",
                "    title: Plan local task",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
    (template_dir / "commands.yaml").write_text(
        "commands:\n  smoke:\n    profile: validation\n", encoding="utf-8"
    )

    templates = discover_bee_workspace_templates(tmp_path)

    assert [template.template_id for template in templates] == ["template-alpha"]
    template = templates[0]
    assert template.metadata_path == template_dir / "metadata.yaml"
    assert template.skill_path == template_dir / "SKILL.md"
    assert template.feature_paths == (feature_dir / "acceptance.feature",)
    assert template.commands_path == template_dir / "commands.yaml"
    assert template.metadata["kind"] == "maintenance"
    assert template.metadata["topic"] == {"session_id": "session-alpha"}


def test_bee_workspace_loads_json_metadata(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "json-template"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "template_id": "json-template",
                "kind": "maintenance",
                "profile": "local",
                "title": "JSON template",
                "topic": {"session_id": "session-alpha"},
                "nodes": [
                    {
                        "node_id": "node-plan",
                        "kind": "analysis",
                        "profile": "default",
                        "title": "Plan JSON task",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# JSON Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: JSON template\n", encoding="utf-8"
    )

    template = load_bee_workspace_template(tmp_path, "json-template")

    assert template.template_id == "json-template"
    assert template.metadata_path == template_dir / "metadata.json"
    assert template.commands_path is None


@pytest.mark.parametrize(
    ("metadata_key", "expected"),
    [
        ("prompt", "forbidden sensitive field"),
        ("command", "forbidden executable field"),
    ],
)
def test_bee_workspace_rejects_sensitive_template_fields(
    tmp_path: Path,
    metadata_key: str,
    expected: str,
) -> None:
    template_dir = tmp_path / ".bee" / "templates" / f"unsafe-{metadata_key}"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                f"template_id: unsafe-{metadata_key}",
                "kind: maintenance",
                "profile: local",
                "title: Unsafe template",
                "topic:",
                "  session_id: session-alpha",
                "nodes: []",
                "metadata:",
                f"  {metadata_key}: unsafe value",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Unsafe Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: unsafe template\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=expected):
        load_bee_workspace_template(tmp_path, f"unsafe-{metadata_key}")


def test_bee_workspace_rejects_invalid_template_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="template_id"):
        load_bee_workspace_template(tmp_path, "../escape")


def test_bee_workspace_rejects_symlinked_template_dir(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside-template"
    _write_safe_template(outside_dir, template_id="template-link")
    templates_dir = tmp_path / ".bee" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "template-link").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        discover_bee_workspace_templates(tmp_path)


def test_bee_workspace_rejects_symlinked_metadata_file(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
    outside_metadata = tmp_path / "outside-metadata.yaml"
    outside_metadata.write_text(_safe_template_yaml("template-alpha"), encoding="utf-8")
    (template_dir / "metadata.yaml").symlink_to(outside_metadata)

    with pytest.raises(ValueError, match="symlink"):
        load_bee_workspace_template(tmp_path, "template-alpha")


def _write_safe_template(template_dir: Path, *, template_id: str) -> None:
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        _safe_template_yaml(template_id),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )


def _safe_template_yaml(template_id: str) -> str:
    return "\n".join(
        [
            "version: 1",
            f"template_id: {template_id}",
            "kind: maintenance",
            "profile: local",
            "title: Local template",
            "topic:",
            "  session_id: session-alpha",
            "nodes:",
            "  - node_id: node-plan",
            "    kind: analysis",
            "    profile: default",
            "    title: Plan local task",
        ]
    )
