"""Workspace-local Bee template discovery.

This module treats `.bee` files as sanitized product artifacts. It does not
execute commands or create durable runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Final, cast

import yaml

from coding_agent.bee_runtime import parse_bee_task_manifest
from coding_agent.topic_store import JSONObject

_BEE_DIR: Final[str] = ".bee"
_TEMPLATES_DIR: Final[str] = "templates"
_METADATA_JSON: Final[str] = "metadata.json"
_METADATA_YAML: Final[str] = "metadata.yaml"
_SKILL_FILE: Final[str] = "SKILL.md"
_FEATURES_DIR: Final[str] = "features"
_COMMANDS_FILE: Final[str] = "commands.yaml"
_SAFE_TEMPLATE_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
)


@dataclass(frozen=True)
class BeeWorkspaceTemplate:
    template_id: str
    template_dir: Path
    metadata_path: Path
    metadata: JSONObject
    skill_path: Path
    feature_paths: tuple[Path, ...]
    commands_path: Path | None = None


def discover_bee_workspace_templates(
    workspace_root: Path | str,
) -> list[BeeWorkspaceTemplate]:
    """Discover valid Bee templates under a workspace root."""

    templates_dir = _templates_root(Path(workspace_root))
    if not templates_dir.exists():
        return []
    templates_root = _require_directory_in_templates_root(
        templates_dir,
        templates_dir,
        label="Bee templates root",
    )
    if not templates_root.is_dir():
        raise ValueError(f"Bee templates path is not a directory: {templates_dir}")

    templates: list[BeeWorkspaceTemplate] = []
    for template_dir in sorted(
        path
        for path in templates_root.iterdir()
        if not path.is_symlink() and path.is_dir()
    ):
        templates.append(
            _load_template_dir(template_dir, templates_root=templates_root)
        )
    for symlink in sorted(
        path for path in templates_root.iterdir() if path.is_symlink()
    ):
        raise ValueError(f"Bee template path must not be a symlink: {symlink.name}")
    return templates


def load_bee_workspace_template(
    workspace_root: Path | str,
    template_id: str,
) -> BeeWorkspaceTemplate:
    """Load one Bee template by safe local template ID."""

    _require_safe_template_id(template_id)
    templates_dir = _templates_root(Path(workspace_root))
    templates_root = _require_directory_in_templates_root(
        templates_dir,
        templates_dir,
        label="Bee templates root",
    )
    template_dir = templates_root / template_id
    if not template_dir.is_dir():
        raise FileNotFoundError(f"Bee template not found: {template_id}")
    return _load_template_dir(template_dir, templates_root=templates_root)


def _templates_root(workspace_root: Path) -> Path:
    return workspace_root / _BEE_DIR / _TEMPLATES_DIR


def _load_template_dir(
    template_dir: Path,
    *,
    templates_root: Path,
) -> BeeWorkspaceTemplate:
    template_dir = _require_directory_in_templates_root(
        template_dir,
        templates_root,
        label="Bee template path",
    )
    template_id = template_dir.name
    _require_safe_template_id(template_id)
    metadata_path = _metadata_path(template_dir, templates_root=templates_root)
    metadata = _load_metadata(metadata_path)
    declared_template_id = metadata.get("template_id")
    if declared_template_id is not None and declared_template_id != template_id:
        raise ValueError(
            f"Bee template_id mismatch: {declared_template_id} != {template_id}"
        )
    _validate_template_metadata(metadata)

    skill_path = template_dir / _SKILL_FILE
    skill_path = _require_file_in_templates_root(
        skill_path,
        templates_root,
        label="Bee template skill",
    )
    if not skill_path.is_file():
        raise FileNotFoundError(f"Bee template missing {_SKILL_FILE}: {template_id}")

    features_dir = template_dir / _FEATURES_DIR
    features_dir = _require_directory_in_templates_root(
        features_dir,
        templates_root,
        label="Bee template features",
    )
    for feature_path in features_dir.glob("*.feature"):
        if feature_path.is_symlink():
            raise ValueError(
                f"Bee template feature must not be a symlink: {feature_path}"
            )
    feature_paths = tuple(
        sorted(
            _require_file_in_templates_root(
                path,
                templates_root,
                label="Bee template feature",
            )
            for path in features_dir.glob("*.feature")
            if not path.is_symlink() and path.is_file()
        )
    )
    if not feature_paths:
        raise FileNotFoundError(
            f"Bee template must include at least one features/*.feature file: {template_id}"
        )

    commands_path = template_dir / _COMMANDS_FILE
    if commands_path.exists():
        commands_path = _require_file_in_templates_root(
            commands_path,
            templates_root,
            label="Bee template commands",
        )
    if commands_path.is_symlink():
        raise ValueError(
            f"Bee template commands must not be a symlink: {commands_path}"
        )
    if not commands_path.is_file():
        commands_path = None

    return BeeWorkspaceTemplate(
        template_id=template_id,
        template_dir=template_dir,
        metadata_path=metadata_path,
        metadata=metadata,
        skill_path=skill_path,
        feature_paths=feature_paths,
        commands_path=commands_path,
    )


def _metadata_path(template_dir: Path, *, templates_root: Path) -> Path:
    candidates = [
        _require_file_in_templates_root(path, templates_root, label="Bee metadata")
        for path in (
            template_dir / _METADATA_YAML,
            template_dir / _METADATA_JSON,
        )
        if (path.exists() or path.is_symlink()) and path.is_file()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"Bee template missing metadata file: {template_dir.name}"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"Bee template must include only one metadata.yaml or metadata.json: {template_dir.name}"
        )
    return candidates[0]


def _load_metadata(metadata_path: Path) -> JSONObject:
    if metadata_path.name == _METADATA_JSON:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
    elif metadata_path.name == _METADATA_YAML:
        loaded = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported Bee metadata file: {metadata_path.name}")
    if not isinstance(loaded, dict):
        raise TypeError(f"Bee metadata must be an object: {metadata_path}")
    return cast(JSONObject, dict(loaded))


def _validate_template_metadata(metadata: JSONObject) -> None:
    # Reuse the existing Bee manifest parser so workspace templates cannot drift
    # into a second, weaker metadata safety model.
    parse_bee_task_manifest(dict(metadata))


def _require_safe_template_id(template_id: str) -> None:
    if not _SAFE_TEMPLATE_ID_RE.fullmatch(template_id):
        raise ValueError(f"invalid Bee template_id: {template_id}")


def _require_directory_in_templates_root(
    path: Path,
    templates_root: Path,
    *,
    label: str,
) -> Path:
    return _require_path_in_templates_root(
        path,
        templates_root,
        label=label,
        expected_kind="directory",
    )


def _require_file_in_templates_root(
    path: Path,
    templates_root: Path,
    *,
    label: str,
) -> Path:
    return _require_path_in_templates_root(
        path,
        templates_root,
        label=label,
        expected_kind="file",
    )


def _require_path_in_templates_root(
    path: Path,
    templates_root: Path,
    *,
    label: str,
    expected_kind: str,
) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not path.exists():
        return path
    resolved_root = templates_root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"{label} must stay under Bee templates root: {path}")
    if expected_kind == "directory" and not resolved_path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    if expected_kind == "file" and not resolved_path.is_file():
        raise ValueError(f"{label} must be a file: {path}")
    return resolved_path
