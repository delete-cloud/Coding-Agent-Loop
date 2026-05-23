"""Generic Bee template pack manifest loading.

Template pack discovery is static metadata loading. It validates manifests and
workspace templates but never executes commands or creates durable Bee runs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

import yaml

from coding_agent.bee_workspace import (
    BeeWorkspaceTemplate,
    discover_bee_workspace_templates,
    load_bee_workspace_template,
)
from coding_agent.topic_store import JSONObject

_MANIFEST_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("bee-pack.yaml", "yaml"),
    ("bee-pack.json", "json"),
    (".bee/pack.yaml", "yaml"),
    (".bee/pack.json", "json"),
)
_SAFE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FORBIDDEN_PACK_KEY_PARTS: Final[frozenset[str]] = frozenset({
    "api_key",
    "apikey",
    "args",
    "argv",
    "cmd",
    "command",
    "commands",
    "command_output",
    "credential",
    "credentials",
    "content",
    "env",
    "environment",
    "exec",
    "executor",
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
})
_COMPACT_FORBIDDEN_PACK_KEY_PARTS: Final[frozenset[str]] = frozenset({
    "api_key",
    "apikey",
    "args",
    "argv",
    "cmd",
    "command",
    "commands",
    "command_output",
    "credential",
    "credentials",
    "env",
    "environment",
    "exec",
    "executor",
    "key",
    "password",
    "prompt",
    "secret",
    "shell",
    "stderr",
    "stdout",
    "token",
})
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "bearer ",
    "gho_",
    "ghp_",
    "github_pat_",
    "password=",
    "secret=",
    "sk-",
    "token=",
)
_MAX_SAFE_TEXT_CHARS: Final[int] = 256


class BeeTemplatePackSource(StrEnum):
    LOCAL_WORKSPACE = "local_workspace"
    FIXTURE = "fixture"
    IMPORTED = "imported"


@dataclass(frozen=True)
class BeePackManifest:
    pack_id: str
    name: str
    version: str
    template_ids: tuple[str, ...]
    description: str | None = None
    domain_profile: str | None = None
    default_workspace_policy: JSONObject | None = None
    default_topic_policy: JSONObject | None = None
    default_memory_policy: JSONObject | None = None
    tags: tuple[str, ...] = ()
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class BeeTemplatePack:
    manifest: BeePackManifest
    root: Path
    source: BeeTemplatePackSource
    templates: tuple[BeeWorkspaceTemplate, ...]
    manifest_path: Path | None = None


def load_bee_template_pack(
    root: Path | str,
    *,
    source: BeeTemplatePackSource = BeeTemplatePackSource.LOCAL_WORKSPACE,
) -> BeeTemplatePack:
    """Load and validate a Bee template pack from a workspace root."""

    pack_root = Path(root)
    manifest_path, raw_manifest = _load_manifest_file(pack_root)
    if raw_manifest is None:
        return _load_implicit_local_pack(pack_root, source=source)

    manifest = _parse_manifest(raw_manifest, manifest_path=manifest_path)
    templates = tuple(
        load_bee_workspace_template(pack_root, template_id)
        for template_id in manifest.template_ids
    )
    return BeeTemplatePack(
        manifest=manifest,
        root=pack_root,
        source=source,
        templates=templates,
        manifest_path=manifest_path,
    )


def _load_manifest_file(root: Path) -> tuple[Path | None, JSONObject | None]:
    for relative_path, manifest_format in _MANIFEST_CANDIDATES:
        candidate = root / relative_path
        if candidate.is_symlink():
            raise ValueError(f"Bee pack manifest must not be a symlink: {candidate}")
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise ValueError(f"Bee pack manifest must be a file: {candidate}")
        loaded = _read_manifest(candidate, manifest_format)
        if not isinstance(loaded, dict):
            raise TypeError(f"Bee pack manifest must be an object: {candidate}")
        return candidate, cast(JSONObject, dict(loaded))
    return None, None


def _read_manifest(path: Path, manifest_format: str) -> object:
    if manifest_format == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if manifest_format == "yaml":
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    raise ValueError(f"unsupported Bee pack manifest format: {manifest_format}")


def _load_implicit_local_pack(
    root: Path,
    *,
    source: BeeTemplatePackSource,
) -> BeeTemplatePack:
    templates = tuple(discover_bee_workspace_templates(root))
    if not templates:
        raise FileNotFoundError(
            "Bee pack manifest not found and no .bee/templates exist"
        )
    template_ids = tuple(template.template_id for template in templates)
    manifest = BeePackManifest(
        pack_id="local",
        name="Local Bee Template Pack",
        version="0.0.0",
        description="Implicit local pack derived from .bee/templates.",
        template_ids=template_ids,
    )
    return BeeTemplatePack(
        manifest=manifest,
        root=root,
        source=source,
        templates=templates,
        manifest_path=None,
    )


def _parse_manifest(
    raw: Mapping[str, Any], *, manifest_path: Path | None
) -> BeePackManifest:
    _validate_safe_json("bee_pack_manifest", raw)
    pack_id = _required_safe_id(raw, "pack_id", manifest_path)
    name = _required_safe_text(raw, "name", manifest_path)
    version = _required_safe_text(raw, "version", manifest_path)
    template_ids = _template_ids(raw.get("templates"), manifest_path)
    description = _optional_safe_text(raw, "description", manifest_path)
    domain_profile = _optional_safe_id(raw, "domain_profile", manifest_path)
    tags = _tags(raw.get("tags"), manifest_path)
    return BeePackManifest(
        pack_id=pack_id,
        name=name,
        version=version,
        description=description,
        domain_profile=domain_profile,
        template_ids=template_ids,
        default_workspace_policy=_optional_policy(raw, "default_workspace_policy"),
        default_topic_policy=_optional_policy(raw, "default_topic_policy"),
        default_memory_policy=_optional_policy(raw, "default_memory_policy"),
        tags=tags,
        metadata=_optional_metadata(raw),
    )


def _template_ids(value: object, manifest_path: Path | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(_manifest_error("templates must be a list", manifest_path))
    ids: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            template_id = item
        elif isinstance(item, dict):
            template_id = _required_safe_id(
                item,
                "template_id",
                manifest_path,
                context=f"templates[{index}]",
            )
        else:
            raise TypeError(
                _manifest_error(
                    f"templates[{index}] must be a string or object", manifest_path
                )
            )
        _require_safe_id("template_id", template_id, manifest_path)
        ids.append(template_id)
    if len(set(ids)) != len(ids):
        raise ValueError(_manifest_error("template ids must be unique", manifest_path))
    return tuple(ids)


def _tags(value: object, manifest_path: Path | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(_manifest_error("tags must be a list", manifest_path))
    tags = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(
                _manifest_error(f"tags[{index}] must be a string", manifest_path)
            )
        _require_safe_id("tag", item, manifest_path)
        tags.append(item)
    if len(set(tags)) != len(tags):
        raise ValueError(_manifest_error("tags must be unique", manifest_path))
    return tuple(tags)


def _optional_policy(raw: Mapping[str, Any], key: str) -> JSONObject | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"Bee pack manifest {key} must be an object")
    return cast(JSONObject, dict(value))


def _optional_metadata(raw: Mapping[str, Any]) -> JSONObject:
    value = raw.get("metadata", {})
    if not isinstance(value, dict):
        raise TypeError("Bee pack manifest metadata must be an object")
    return cast(JSONObject, dict(value))


def _required_safe_id(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
    *,
    context: str = "manifest",
) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(
            _manifest_error(f"{context}.{key} must be a string", manifest_path)
        )
    _require_safe_id(key, value, manifest_path)
    return value


def _optional_safe_id(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_id(key, value, manifest_path)
    return value


def _require_safe_id(name: str, value: str, manifest_path: Path | None) -> None:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(_manifest_error(f"{name} is not a safe id", manifest_path))


def _required_safe_text(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_text(key, value, manifest_path)
    return value


def _optional_safe_text(
    raw: Mapping[str, Any],
    key: str,
    manifest_path: Path | None,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_manifest_error(f"{key} must be a string", manifest_path))
    _require_safe_text(key, value, manifest_path)
    return value


def _require_safe_text(name: str, value: str, manifest_path: Path | None) -> None:
    if not value:
        raise ValueError(_manifest_error(f"{name} must not be empty", manifest_path))
    if len(value) > _MAX_SAFE_TEXT_CHARS:
        raise ValueError(_manifest_error(f"{name} is too long", manifest_path))
    normalized = value.strip().lower()
    if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(
            _manifest_error(f"{name} contains secret-like value", manifest_path)
        )


def _validate_safe_json(path: str, value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            _reject_forbidden_key(path, key)
            _validate_safe_json(f"{path}.{key}", item)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_safe_json(f"{path}[{index}]", item)
        return
    if isinstance(value, str):
        normalized = value.strip().lower()
        if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
            raise ValueError(f"{path} contains secret-like value")
        return
    if isinstance(value, int | float | bool) or value is None:
        return
    raise TypeError(f"{path} contains unsupported JSON value")


def _reject_forbidden_key(path: str, key: str) -> None:
    normalized = key.strip().replace("-", "_").lower()
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    for forbidden in _FORBIDDEN_PACK_KEY_PARTS:
        compact_forbidden = re.sub(r"[^a-z0-9]", "", forbidden)
        if (
            normalized == forbidden
            or normalized.startswith(f"{forbidden}_")
            or normalized.endswith(f"_{forbidden}")
            or f"_{forbidden}_" in normalized
            or (
                forbidden in _COMPACT_FORBIDDEN_PACK_KEY_PARTS
                and compact_forbidden in compact
            )
        ):
            raise ValueError(f"{path}.{key} uses forbidden sensitive field")


def _manifest_error(message: str, manifest_path: Path | None) -> str:
    if manifest_path is None:
        return f"Bee pack manifest {message}"
    return f"Bee pack manifest {message}: {manifest_path}"
