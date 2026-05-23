from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.bee_template_pack import (
    BeeTemplatePackSource,
    load_bee_template_pack,
)


def test_bee_pack_manifest_loads_valid_manifest(tmp_path: Path) -> None:
    _write_safe_template(tmp_path, "template-alpha")
    (tmp_path / "bee-pack.yaml").write_text(
        """pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
description: Safe fixture pack
domain_profile: maintenance
templates:
  - template_id: template-alpha
tags:
  - local
metadata:
  owner: platform
""",
        encoding="utf-8",
    )

    pack = load_bee_template_pack(tmp_path)

    assert pack.manifest_path == tmp_path / "bee-pack.yaml"
    assert pack.manifest.pack_id == "pack-alpha"
    assert pack.manifest.name == "Alpha Pack"
    assert pack.manifest.version == "1.0.0"
    assert pack.manifest.description == "Safe fixture pack"
    assert pack.manifest.domain_profile == "maintenance"
    assert pack.manifest.template_ids == ("template-alpha",)
    assert pack.manifest.tags == ("local",)
    assert pack.manifest.metadata == {"owner": "platform"}
    assert pack.source == BeeTemplatePackSource.LOCAL_WORKSPACE
    assert [template.template_id for template in pack.templates] == ["template-alpha"]


@pytest.mark.parametrize(
    ("manifest_name", "payload"),
    [
        (
            "bee-pack.json",
            {
                "pack_id": "pack-json-root",
                "name": "Root JSON Pack",
                "version": "1.0.0",
                "templates": ["template-alpha"],
            },
        ),
        (
            ".bee/pack.yaml",
            """pack_id: pack-yaml-bee
name: Bee YAML Pack
version: 1.0.0
templates:
  - template-alpha
""",
        ),
        (
            ".bee/pack.json",
            {
                "pack_id": "pack-json-bee",
                "name": "Bee JSON Pack",
                "version": "1.0.0",
                "templates": ["template-alpha"],
            },
        ),
    ],
)
def test_bee_pack_manifest_loads_supported_manifest_names(
    tmp_path: Path,
    manifest_name: str,
    payload: dict[str, object] | str,
) -> None:
    _write_safe_template(tmp_path, "template-alpha")
    manifest_path = tmp_path / manifest_name
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_text = json.dumps(payload) if isinstance(payload, dict) else payload
    manifest_path.write_text(manifest_text, encoding="utf-8")

    pack = load_bee_template_pack(tmp_path)

    assert pack.manifest_path == manifest_path
    assert pack.manifest.template_ids == ("template-alpha",)


def test_bee_pack_manifest_missing_manifest_creates_implicit_local_pack(
    tmp_path: Path,
) -> None:
    _write_safe_template(tmp_path, "template-alpha")

    pack = load_bee_template_pack(tmp_path)

    assert pack.manifest_path is None
    assert pack.manifest.pack_id == "local"
    assert pack.manifest.name == "Local Bee Template Pack"
    assert pack.manifest.version == "0.0.0"
    assert pack.manifest.template_ids == ("template-alpha",)
    assert [template.template_id for template in pack.templates] == ["template-alpha"]


@pytest.mark.parametrize(
    "manifest_text,match",
    [
        ("name: Missing Pack Id\nversion: 1.0.0\ntemplates: []\n", "pack_id"),
        ("pack_id: bad pack\nname: Bad\nversion: 1.0.0\ntemplates: []\n", "pack_id"),
        ("pack_id: pack-alpha\nname: ''\nversion: 1.0.0\ntemplates: []\n", "name"),
        ("pack_id: pack-alpha\nname: Alpha\nversion: ''\ntemplates: []\n", "version"),
        (
            "pack_id: pack-alpha\nname: Alpha\nversion: 1.0.0\ntemplates: {}\n",
            "templates",
        ),
    ],
)
def test_bee_pack_manifest_rejects_invalid_manifest(
    tmp_path: Path,
    manifest_text: str,
    match: str,
) -> None:
    (tmp_path / "bee-pack.yaml").write_text(manifest_text, encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=match):
        load_bee_template_pack(tmp_path)


def test_bee_pack_manifest_rejects_missing_template(tmp_path: Path) -> None:
    (tmp_path / "bee-pack.yaml").write_text(
        """pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
templates:
  - template_id: template-missing
""",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="template-missing"):
        load_bee_template_pack(tmp_path)


def test_bee_pack_manifest_rejects_duplicate_template_id(tmp_path: Path) -> None:
    _write_safe_template(tmp_path, "template-alpha")
    (tmp_path / "bee-pack.yaml").write_text(
        """pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
templates:
  - template-alpha
  - template_id: template-alpha
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_bee_template_pack(tmp_path)


def test_bee_pack_manifest_loader_does_not_execute_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_dir = _write_safe_template(tmp_path, "template-alpha")
    marker = tmp_path / "executed"
    (template_dir / "commands.yaml").write_text(
        "\n".join([
            "commands:",
            "  - name: smoke",
            "    profile: validation",
            "    policy: existing_command_policy",
            "    category: validation",
            "    metadata:",
            f"      marker_path: {marker.name}",
        ]),
        encoding="utf-8",
    )
    (tmp_path / "bee-pack.yaml").write_text(
        """pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
templates:
  - template-alpha
""",
        encoding="utf-8",
    )

    def fail_if_subprocess_runs(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("manifest loading must not execute commands")

    monkeypatch.setattr("subprocess.run", fail_if_subprocess_runs)
    monkeypatch.setattr("subprocess.Popen", fail_if_subprocess_runs)

    pack = load_bee_template_pack(tmp_path)

    assert pack.templates[0].commands_path == template_dir / "commands.yaml"
    assert not marker.exists()


def _write_safe_template(workspace_root: Path, template_id: str) -> Path:
    template_dir = workspace_root / ".bee" / "templates" / template_id
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join([
            "version: 1",
            f"template_id: {template_id}",
            "kind: maintenance",
            "profile: local",
            "title: Local template",
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
        ]),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
    return template_dir
