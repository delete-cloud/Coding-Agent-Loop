"""Generic Bee workflow task manifest records.

Bee is a Coding Agent product/runtime profile over Topic. This module only
parses sanitized task intent; it does not execute nodes or bypass action safety.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from coding_agent.topic_store import JSONObject, JSONValue

_MANIFEST_VERSION: Final[int] = 1
_MAX_SAFE_LABEL_CHARS: Final[int] = 128
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_MAX_NODES: Final[int] = 64
_FORBIDDEN_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "command_output",
        "content",
        "env",
        "message",
        "prompt",
        "result",
        "secret",
        "stderr",
        "stdout",
        "text",
    }
)
_FORBIDDEN_EXECUTABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "argv",
        "cmd",
        "command",
        "commands",
        "exec",
        "executor",
        "script",
        "shell",
    }
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "password=",
    "secret=",
    "sk-",
    "token=",
)


@dataclass(frozen=True)
class BeeTopicBinding:
    session_id: str
    topic_id: str | None = None
    tape_id: str | None = None
    title_hint: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("topic.session_id", self.session_id)
        _require_optional_id("topic.topic_id", self.topic_id)
        _require_optional_id("topic.tape_id", self.tape_id)
        _require_optional_display_text("topic.title_hint", self.title_hint)
        _require_safe_json_object("topic.metadata", self.metadata)


@dataclass(frozen=True)
class BeeNodeManifest:
    node_id: str
    kind: str
    profile: str
    title: str
    depends_on: tuple[str, ...] = ()
    context_profile: str | None = None
    validation_profile: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("node_id", self.node_id)
        _require_safe_label("kind", self.kind)
        _require_safe_label("profile", self.profile)
        _require_display_text("title", self.title)
        for dependency in self.depends_on:
            _require_non_empty("depends_on", dependency)
        _require_optional_safe_label("context_profile", self.context_profile)
        _require_optional_safe_label("validation_profile", self.validation_profile)
        _require_safe_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class BeeTaskManifest:
    version: int
    kind: str
    profile: str
    title: str
    topic: BeeTopicBinding
    summary: str | None = None
    context_profile: str | None = None
    validation_profile: str | None = None
    workspace_policy: str | None = None
    nodes: tuple[BeeNodeManifest, ...] = ()
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version != _MANIFEST_VERSION:
            raise ValueError(f"unsupported Bee manifest version: {self.version}")
        _require_safe_label("kind", self.kind)
        _require_safe_label("profile", self.profile)
        _require_display_text("title", self.title)
        _require_optional_display_text("summary", self.summary)
        _require_optional_safe_label("context_profile", self.context_profile)
        _require_optional_safe_label("validation_profile", self.validation_profile)
        _require_optional_safe_label("workspace_policy", self.workspace_policy)
        if len(self.nodes) > _MAX_NODES:
            raise ValueError(f"Bee manifest nodes exceeds maximum {_MAX_NODES}")
        _require_unique_node_ids(self.nodes)
        _require_safe_json_object("metadata", self.metadata)


def parse_bee_task_manifest(raw: JSONObject) -> BeeTaskManifest:
    """Parse a sanitized Bee task manifest.

    The parser validates the whole raw object before extracting known fields so
    rejected sensitive or executable fields cannot be hidden in unknown keys.
    """

    _require_safe_json_object("manifest", raw)
    version = _require_int(raw, "version")
    topic = _parse_topic_binding(_require_object(raw, "topic"))
    nodes = tuple(
        _parse_node_manifest(item, index)
        for index, item in enumerate(_require_list(raw, "nodes"))
    )
    return BeeTaskManifest(
        version=version,
        kind=_require_string(raw, "kind"),
        profile=_require_string(raw, "profile"),
        title=_require_string(raw, "title"),
        summary=_optional_string(raw, "summary"),
        topic=topic,
        context_profile=_optional_string(raw, "context_profile"),
        validation_profile=_optional_string(raw, "validation_profile"),
        workspace_policy=_optional_string(raw, "workspace_policy"),
        nodes=nodes,
        metadata=dict(_optional_object(raw, "metadata")),
    )


def _parse_topic_binding(raw: JSONObject) -> BeeTopicBinding:
    return BeeTopicBinding(
        session_id=_require_string(raw, "session_id"),
        topic_id=_optional_string(raw, "topic_id"),
        tape_id=_optional_string(raw, "tape_id"),
        title_hint=_optional_string(raw, "title_hint"),
        metadata=dict(_optional_object(raw, "metadata")),
    )


def _parse_node_manifest(raw_value: JSONValue, index: int) -> BeeNodeManifest:
    if not isinstance(raw_value, dict):
        raise TypeError(f"nodes[{index}] must be an object")
    raw = dict(raw_value)
    depends_on = tuple(_require_string_list(raw, "depends_on", default=()))
    return BeeNodeManifest(
        node_id=_require_string(raw, "node_id"),
        kind=_require_string(raw, "kind"),
        profile=_require_string(raw, "profile"),
        title=_require_string(raw, "title"),
        depends_on=depends_on,
        context_profile=_optional_string(raw, "context_profile"),
        validation_profile=_optional_string(raw, "validation_profile"),
        metadata=dict(_optional_object(raw, "metadata")),
    )


def _require_safe_json_object(name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    _validate_safe_json(name, value)


def _validate_safe_json(path: str, value: JSONValue) -> None:
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
        _reject_secret_like_value(path, value)
        if len(value) > _MAX_METADATA_STRING_CHARS:
            raise ValueError(
                f"{path} string exceeds maximum {_MAX_METADATA_STRING_CHARS} chars"
            )
        return
    if isinstance(value, int | float | bool) or value is None:
        return
    raise TypeError(f"{path} contains unsupported JSON value")


def _reject_forbidden_key(path: str, key: str) -> None:
    normalized = key.strip().lower().replace("-", "_")
    for forbidden in _FORBIDDEN_KEY_PARTS:
        if _key_contains_token(normalized, forbidden):
            raise ValueError(f"{path}.{key} uses forbidden sensitive field")
    if normalized in _FORBIDDEN_EXECUTABLE_KEYS:
        raise ValueError(f"{path}.{key} uses forbidden executable field")


def _key_contains_token(normalized_key: str, forbidden: str) -> bool:
    return (
        normalized_key == forbidden
        or normalized_key.startswith(f"{forbidden}_")
        or normalized_key.endswith(f"_{forbidden}")
        or f"_{forbidden}_" in normalized_key
    )


def _reject_secret_like_value(path: str, value: str) -> None:
    normalized = value.strip().lower()
    if any(marker in normalized for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(f"{path} contains secret-like value")


def _require_unique_node_ids(nodes: tuple[BeeNodeManifest, ...]) -> None:
    seen: set[str] = set()
    for node in nodes:
        if node.node_id in seen:
            raise ValueError(f"duplicate Bee node id: {node.node_id}")
        seen.add(node.node_id)
    missing = {
        dependency
        for node in nodes
        for dependency in node.depends_on
        if dependency not in seen
    }
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"Bee node dependencies not found: {missing_list}")


def _require_object(raw: JSONObject, key: str) -> JSONObject:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return dict(value)


def _optional_object(raw: JSONObject, key: str) -> JSONObject:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return dict(value)


def _require_list(raw: JSONObject, key: str) -> list[JSONValue]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


def _require_string(raw: JSONObject, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_string(raw: JSONObject, key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _require_string_list(
    raw: JSONObject,
    key: str,
    *,
    default: tuple[str, ...],
) -> list[str]:
    value = raw.get(key, list(default))
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{key}[{index}] must be a string")
        result.append(item)
    return result


def _require_int(raw: JSONObject, key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_optional_id(name: str, value: str | None) -> None:
    if value is not None:
        _require_non_empty(name, value)


def _require_safe_label(name: str, value: str) -> None:
    _require_non_empty(name, value)
    if len(value) > _MAX_SAFE_LABEL_CHARS:
        raise ValueError(f"{name} exceeds maximum {_MAX_SAFE_LABEL_CHARS} chars")
    _reject_secret_like_value(name, value)


def _require_optional_safe_label(name: str, value: str | None) -> None:
    if value is not None:
        _require_safe_label(name, value)


def _require_display_text(name: str, value: str) -> None:
    _require_non_empty(name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(f"{name} exceeds maximum {_MAX_DISPLAY_TEXT_CHARS} chars")
    _reject_secret_like_value(name, value)


def _require_optional_display_text(name: str, value: str | None) -> None:
    if value is not None:
        _require_display_text(name, value)
