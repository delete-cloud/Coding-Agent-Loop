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

from coding_agent.bee_runtime import BeeTaskManifest, parse_bee_task_manifest
from coding_agent.topic_store import JSONObject

_BEE_DIR: Final[str] = ".bee"
_TEMPLATES_DIR: Final[str] = "templates"
_METADATA_JSON: Final[str] = "metadata.json"
_METADATA_YAML: Final[str] = "metadata.yaml"
_SKILL_FILE: Final[str] = "SKILL.md"
_FEATURES_DIR: Final[str] = "features"
_COMMANDS_FILE: Final[str] = "commands.yaml"
_RUNS_DIR: Final[str] = "runs"
_TASK_JSON_FILE: Final[str] = "task.json"
_REPORT_FILE: Final[str] = "report.md"
_EVIDENCE_DIR: Final[str] = "evidence"
_MEMORY_CANDIDATES_FILE: Final[str] = "memory_candidates.yaml"
_SAFE_TEMPLATE_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
)
_FORBIDDEN_ARTIFACT_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "args",
        "argv",
        "cmd",
        "command",
        "command_output",
        "credential",
        "credentials",
        "content",
        "env",
        "environment",
        "exec",
        "executor",
        "bearer",
        "key",
        "message",
        "password",
        "prompt",
        "result",
        "script",
        "secret",
        "shell",
        "stderr",
        "stdout",
        "text",
        "token",
    }
)
_COMPACT_FORBIDDEN_ARTIFACT_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "args",
        "argv",
        "cmd",
        "command",
        "command_output",
        "credential",
        "credentials",
        "env",
        "environment",
        "exec",
        "executor",
        "bearer",
        "key",
        "password",
        "prompt",
        "script",
        "secret",
        "shell",
        "stderr",
        "stdout",
        "token",
    }
)
_FORBIDDEN_REPORT_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "command_output=",
    "content=",
    "env=",
    "message=",
    "prompt=",
    "result=",
    "secret=",
    "stderr=",
    "stdout=",
    "text=",
    "token=",
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "bearer ",
    "gho_",
    "ghp_",
    "github_pat_",
    "password=",
    "secret=",
    "sk-",
    "token=",
)
_MAX_ARTIFACT_TEXT_CHARS: Final[int] = 256
_COMMAND_INTENT_STATUSES: Final[frozenset[str]] = frozenset({"declared", "disabled"})
_ALLOWED_COMMAND_INTENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "profile",
        "policy",
        "category",
        "validation_label",
        "status",
        "metadata",
    }
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


@dataclass(frozen=True)
class BeeWorkspaceRunNode:
    node_id: str
    status: str
    run_id: str | None = None
    action_ids: tuple[str, ...] = ()
    validation_ids: tuple[str, ...] = ()
    attempts: int = 0


@dataclass(frozen=True)
class BeeWorkspaceRunArtifacts:
    task_id: str
    template_id: str
    topic_id: str
    status: str
    nodes: tuple[BeeWorkspaceRunNode, ...]
    report_title: str
    report_summary: str
    run_ids: tuple[str, ...] = ()
    action_ids: tuple[str, ...] = ()
    validation_ids: tuple[str, ...] = ()
    evidence_labels: tuple[str, ...] = ()
    memory_candidates: tuple[JSONObject, ...] = ()


@dataclass(frozen=True)
class BeeWorkspaceRunArtifactPaths:
    run_dir: Path
    task_json_path: Path
    report_path: Path
    evidence_dir: Path
    memory_candidates_path: Path | None = None


@dataclass(frozen=True)
class BeeWorkspaceCommandIntent:
    name: str
    profile: str
    policy: str
    category: str
    validation_label: str | None = None
    status: str = "declared"
    metadata: JSONObject | None = None


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


def build_bee_manifest_from_workspace_template(
    template: BeeWorkspaceTemplate,
) -> BeeTaskManifest:
    """Build the existing Bee manifest shape from a workspace template."""

    metadata = dict(template.metadata)
    manifest_metadata = dict(metadata.get("metadata", {}))
    manifest_metadata["template_id"] = template.template_id
    metadata["metadata"] = manifest_metadata
    return parse_bee_task_manifest(metadata)


def load_bee_workspace_command_intents(
    template: BeeWorkspaceTemplate,
) -> tuple[BeeWorkspaceCommandIntent, ...]:
    """Load non-executing command intent metadata from commands.yaml."""

    if template.commands_path is None:
        return ()
    loaded = yaml.safe_load(template.commands_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(
            f"Bee commands.yaml must be an object: {template.commands_path}"
        )
    commands = loaded.get("commands", ())
    if not isinstance(commands, list):
        raise TypeError("Bee commands.yaml commands must be a list")
    intents = tuple(
        _parse_command_intent(item, index) for index, item in enumerate(commands)
    )
    names = [intent.name for intent in intents]
    if len(set(names)) != len(names):
        raise ValueError("Bee commands.yaml command names must be unique")
    return intents


def write_bee_workspace_run_artifacts(
    workspace_root: Path | str,
    artifacts: BeeWorkspaceRunArtifacts,
) -> BeeWorkspaceRunArtifactPaths:
    """Write sanitized workspace-local Bee run artifacts."""

    _validate_run_artifacts(artifacts)
    workspace_path = Path(workspace_root)
    bee_root = workspace_path / _BEE_DIR
    if bee_root.is_symlink():
        raise ValueError(f"Bee workspace root must not be a symlink: {bee_root}")
    if bee_root.exists():
        bee_root = _require_child_path_in_root(
            bee_root,
            workspace_path.resolve(strict=True),
            label="Bee workspace root",
            expected_kind="directory",
        )
    runs_root = bee_root / _RUNS_DIR
    if runs_root.is_symlink():
        raise ValueError(f"Bee runs root must not be a symlink: {runs_root}")
    runs_root.mkdir(parents=True, exist_ok=True)
    runs_root = _require_directory_in_root(runs_root, runs_root, label="Bee runs root")
    run_dir = _require_child_path_in_root(
        runs_root / artifacts.task_id,
        runs_root,
        label="Bee run path",
        expected_kind="directory",
    )
    if run_dir.exists() and not run_dir.is_dir():
        raise ValueError(f"Bee run path must be a directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir = _require_child_path_in_root(
        run_dir,
        runs_root,
        label="Bee run path",
        expected_kind="directory",
    )

    evidence_dir = run_dir / _EVIDENCE_DIR
    if evidence_dir.exists() or evidence_dir.is_symlink():
        evidence_dir = _require_child_path_in_root(
            evidence_dir,
            runs_root,
            label="Bee run evidence",
            expected_kind="directory",
        )
    evidence_dir.mkdir(exist_ok=True)
    evidence_dir = _require_child_path_in_root(
        evidence_dir,
        runs_root,
        label="Bee run evidence",
        expected_kind="directory",
    )

    task_json_path = _writable_run_file(run_dir / _TASK_JSON_FILE, runs_root)
    report_path = _writable_run_file(run_dir / _REPORT_FILE, runs_root)
    memory_candidates_path = (
        _writable_run_file(run_dir / _MEMORY_CANDIDATES_FILE, runs_root)
        if artifacts.memory_candidates
        else None
    )
    task_json = _task_json_payload(
        artifacts, has_memory=memory_candidates_path is not None
    )
    task_json_path.write_text(
        json.dumps(task_json, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_report_markdown(artifacts), encoding="utf-8")
    if memory_candidates_path is not None:
        memory_candidates_path.write_text(
            yaml.safe_dump(
                list(artifacts.memory_candidates),
                sort_keys=True,
                allow_unicode=False,
            ),
            encoding="utf-8",
        )
    return BeeWorkspaceRunArtifactPaths(
        run_dir=run_dir,
        task_json_path=task_json_path,
        report_path=report_path,
        evidence_dir=evidence_dir,
        memory_candidates_path=memory_candidates_path,
    )


def _templates_root(workspace_root: Path) -> Path:
    return workspace_root / _BEE_DIR / _TEMPLATES_DIR


def _parse_command_intent(raw_value: object, index: int) -> BeeWorkspaceCommandIntent:
    if not isinstance(raw_value, dict):
        raise TypeError(f"commands[{index}] must be an object")
    raw = dict(raw_value)
    _reject_unknown_command_keys(raw, index)
    _validate_artifact_json(f"commands[{index}]", raw)
    name = _required_command_string(raw, "name", index)
    profile = _required_command_string(raw, "profile", index)
    policy = _required_command_string(raw, "policy", index)
    category = _required_command_string(raw, "category", index)
    validation_label = _optional_command_string(raw, "validation_label", index)
    status = raw.get("status", "declared")
    if not isinstance(status, str):
        raise TypeError(f"commands[{index}].status must be a string")
    if status not in _COMMAND_INTENT_STATUSES:
        raise ValueError(f"commands[{index}].status is not supported: {status}")
    metadata = raw.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise TypeError(f"commands[{index}].metadata must be an object")
    return BeeWorkspaceCommandIntent(
        name=name,
        profile=profile,
        policy=policy,
        category=category,
        validation_label=validation_label,
        status=status,
        metadata=cast(JSONObject, dict(metadata)) if metadata is not None else None,
    )


def _reject_unknown_command_keys(raw: dict[object, object], index: int) -> None:
    for key in raw:
        if not isinstance(key, str):
            raise TypeError(f"commands[{index}] keys must be strings")
        if key not in _ALLOWED_COMMAND_INTENT_KEYS:
            _reject_forbidden_artifact_key(f"commands[{index}]", key)
            raise ValueError(f"commands[{index}].{key} is not supported")


def _required_command_string(
    raw: dict[object, object],
    key: str,
    index: int,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(f"commands[{index}].{key} must be a string")
    _require_safe_template_id(value)
    return value


def _optional_command_string(
    raw: dict[object, object],
    key: str,
    index: int,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"commands[{index}].{key} must be a string")
    _require_safe_template_id(value)
    return value


def _task_json_payload(
    artifacts: BeeWorkspaceRunArtifacts,
    *,
    has_memory: bool,
) -> JSONObject:
    nodes = [
        {
            "node_id": node.node_id,
            "status": node.status,
            "run_id": node.run_id,
            "action_ids": list(node.action_ids),
            "validation_ids": list(node.validation_ids),
            "attempts": node.attempts,
        }
        for node in artifacts.nodes
    ]
    payload: JSONObject = {
        "artifact_role": "sanitized_mirror",
        "source_of_truth": "durable_bee_stores",
        "task_id": artifacts.task_id,
        "template_id": artifacts.template_id,
        "topic_id": artifacts.topic_id,
        "status": artifacts.status,
        "nodes": nodes,
        "node_attempts": {node.node_id: node.attempts for node in artifacts.nodes},
        "run_ids": list(artifacts.run_ids),
        "action_ids": list(artifacts.action_ids),
        "validation_ids": list(artifacts.validation_ids),
        "report_path": _REPORT_FILE,
    }
    if has_memory:
        payload["memory_candidates_path"] = _MEMORY_CANDIDATES_FILE
    return payload


def _report_markdown(artifacts: BeeWorkspaceRunArtifacts) -> str:
    return (
        f"# {artifacts.report_title}\n\n"
        f"- task_id: {artifacts.task_id}\n"
        f"- template_id: {artifacts.template_id}\n"
        f"- topic_id: {artifacts.topic_id}\n"
        f"- status: {artifacts.status}\n"
        f"- summary: {artifacts.report_summary}\n"
    )


def _validate_run_artifacts(artifacts: BeeWorkspaceRunArtifacts) -> None:
    _require_safe_template_id(artifacts.task_id)
    _require_safe_template_id(artifacts.template_id)
    _require_safe_template_id(artifacts.topic_id)
    _require_safe_template_id(artifacts.status)
    _require_safe_report_value("report_title", artifacts.report_title)
    _require_safe_report_value("report_summary", artifacts.report_summary)
    for value in artifacts.run_ids:
        _require_safe_template_id(value)
    for value in artifacts.action_ids:
        _require_safe_template_id(value)
    for value in artifacts.validation_ids:
        _require_safe_template_id(value)
    for value in artifacts.evidence_labels:
        _require_safe_report_value("evidence_label", value)
    for node in artifacts.nodes:
        _validate_run_node(node)
    for candidate in artifacts.memory_candidates:
        _validate_artifact_json("memory_candidate", candidate)


def _validate_run_node(node: BeeWorkspaceRunNode) -> None:
    _require_safe_template_id(node.node_id)
    _require_safe_template_id(node.status)
    if node.run_id is not None:
        _require_safe_template_id(node.run_id)
    for value in node.action_ids:
        _require_safe_template_id(value)
    for value in node.validation_ids:
        _require_safe_template_id(value)
    if node.attempts < 0:
        raise ValueError(f"Bee node attempts must be non-negative: {node.node_id}")


def _validate_artifact_json(path: str, value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _reject_forbidden_artifact_key(path, key)
            _validate_artifact_json(f"{path}.{key}", item)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_artifact_json(f"{path}[{index}]", item)
        return
    if isinstance(value, str):
        _require_safe_report_value(path, value)
        return
    if isinstance(value, int | float | bool) or value is None:
        return
    raise TypeError(f"{path} contains unsupported JSON value")


def _reject_forbidden_artifact_key(path: str, key: str) -> None:
    normalized = _normalize_artifact_key(key)
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    for forbidden in _FORBIDDEN_ARTIFACT_KEY_PARTS:
        compact_forbidden = re.sub(r"[^a-z0-9]", "", forbidden)
        if (
            normalized == forbidden
            or normalized.startswith(f"{forbidden}_")
            or normalized.endswith(f"_{forbidden}")
            or f"_{forbidden}_" in normalized
            or (
                forbidden in _COMPACT_FORBIDDEN_ARTIFACT_KEY_PARTS
                and compact_forbidden in compact
            )
        ):
            raise ValueError(f"{path}.{key} uses forbidden sensitive field")


def _normalize_artifact_key(key: str) -> str:
    with_separators = key.strip().replace("-", "_")
    with_separators = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", with_separators)
    return with_separators.lower()


def _require_safe_report_value(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > _MAX_ARTIFACT_TEXT_CHARS:
        raise ValueError(f"{name} exceeds maximum {_MAX_ARTIFACT_TEXT_CHARS} chars")
    normalized = value.strip().lower()
    if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(f"{name} contains secret-like value")
    if any(marker in normalized for marker in _FORBIDDEN_REPORT_VALUE_MARKERS):
        raise ValueError(f"{name} contains forbidden raw output marker")


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


def _require_directory_in_root(path: Path, root: Path, *, label: str) -> Path:
    return _require_child_path_in_root(
        path,
        root,
        label=label,
        expected_kind="directory",
    )


def _require_child_path_in_root(
    path: Path,
    root: Path,
    *,
    label: str,
    expected_kind: str,
) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not path.exists():
        resolved_parent = path.parent.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        if not resolved_parent.is_relative_to(resolved_root):
            raise ValueError(f"{label} must stay under Bee runs root: {path}")
        return path
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"{label} must stay under Bee runs root: {path}")
    if expected_kind == "directory" and not resolved_path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    if expected_kind == "file" and not resolved_path.is_file():
        raise ValueError(f"{label} must be a file: {path}")
    return resolved_path


def _writable_run_file(path: Path, runs_root: Path) -> Path:
    if path.exists() or path.is_symlink():
        return _require_child_path_in_root(
            path,
            runs_root,
            label="Bee run artifact",
            expected_kind="file",
        )
    resolved_parent = path.parent.resolve(strict=True)
    resolved_root = runs_root.resolve(strict=True)
    if not resolved_parent.is_relative_to(resolved_root):
        raise ValueError(f"Bee run artifact must stay under Bee runs root: {path}")
    return path


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
