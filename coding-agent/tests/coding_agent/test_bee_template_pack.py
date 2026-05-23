from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.bee_template_pack import (
    BeePackRegistry,
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


def test_bee_pack_registry_discovers_one_local_pack(tmp_path: Path) -> None:
    _write_manifest_pack(tmp_path, pack_id="pack-alpha", template_id="template-alpha")

    registry = BeePackRegistry.discover(
        (tmp_path,), source=BeeTemplatePackSource.LOCAL_WORKSPACE
    )

    assert [summary.pack_id for summary in registry.list_packs()] == ["pack-alpha"]
    summary = registry.list_packs()[0]
    assert summary.source == BeeTemplatePackSource.LOCAL_WORKSPACE
    assert summary.template_count == 1
    assert summary.root == tmp_path


def test_bee_pack_registry_discovers_multiple_fixture_packs(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest_pack(first, pack_id="pack-alpha", template_id="template-alpha")
    _write_manifest_pack(second, pack_id="pack-beta", template_id="template-beta")

    registry = BeePackRegistry.discover(
        (second, first), source=BeeTemplatePackSource.FIXTURE
    )

    assert [summary.pack_id for summary in registry.list_packs()] == [
        "pack-alpha",
        "pack-beta",
    ]
    assert {summary.source for summary in registry.list_packs()} == {
        BeeTemplatePackSource.FIXTURE
    }


def test_bee_pack_registry_lists_templates_by_pack(tmp_path: Path) -> None:
    _write_manifest_pack(tmp_path, pack_id="pack-alpha", template_id="template-alpha")

    registry = BeePackRegistry.discover((tmp_path,))

    assert [
        summary.template_id for summary in registry.list_templates("pack-alpha")
    ] == ["template-alpha"]
    template_summary = registry.list_templates("pack-alpha")[0]
    assert template_summary.pack_id == "pack-alpha"
    assert template_summary.source == BeeTemplatePackSource.LOCAL_WORKSPACE
    assert template_summary.template_kind == "maintenance"
    assert template_summary.template_profile == "local"


def test_bee_pack_registry_loads_template_from_pack(tmp_path: Path) -> None:
    _write_manifest_pack(tmp_path, pack_id="pack-alpha", template_id="template-alpha")

    registry = BeePackRegistry.discover((tmp_path,))
    template = registry.load_template("pack-alpha", "template-alpha")

    assert template.template_id == "template-alpha"
    assert template.template_dir == tmp_path / ".bee" / "templates" / "template-alpha"


def test_bee_pack_registry_rejects_unknown_pack(tmp_path: Path) -> None:
    _write_manifest_pack(tmp_path, pack_id="pack-alpha", template_id="template-alpha")

    registry = BeePackRegistry.discover((tmp_path,))

    with pytest.raises(KeyError, match="pack-missing"):
        registry.list_templates("pack-missing")
    with pytest.raises(KeyError, match="pack-missing"):
        registry.load_template("pack-missing", "template-alpha")
    with pytest.raises(KeyError, match="template-missing"):
        registry.load_template("pack-alpha", "template-missing")


def test_bee_pack_registry_preserves_pack_template_provenance(
    tmp_path: Path,
) -> None:
    _write_manifest_pack(tmp_path, pack_id="pack-alpha", template_id="template-alpha")

    registry = BeePackRegistry.discover(
        (tmp_path,), source=BeeTemplatePackSource.IMPORTED
    )
    provenance = registry.template_provenance("pack-alpha", "template-alpha")

    assert provenance.pack_id == "pack-alpha"
    assert provenance.template_id == "template-alpha"
    assert provenance.source == BeeTemplatePackSource.IMPORTED
    assert provenance.root == tmp_path
    assert provenance.manifest_path == tmp_path / "bee-pack.yaml"
    assert provenance.template_dir == tmp_path / ".bee" / "templates" / "template-alpha"


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


def _write_manifest_pack(
    workspace_root: Path, *, pack_id: str, template_id: str
) -> None:
    _write_safe_template(workspace_root, template_id)
    (workspace_root / "bee-pack.yaml").write_text(
        f"""pack_id: {pack_id}
name: {pack_id}
version: 1.0.0
domain_profile: maintenance
templates:
  - {template_id}
tags:
  - local
""",
        encoding="utf-8",
    )
