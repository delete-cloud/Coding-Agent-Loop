"""Durable Bee launch records.

Bee launch records describe how a Bee task was requested. They do not create
tasks, execute nodes, or grant command execution rights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from agentkit.storage.pg import AsyncPGPool, PGPool
from coding_agent.bee_runtime import BeeTaskManifest
from coding_agent.bee_workspace import (
    BeeWorkspaceTemplate,
    build_bee_manifest_from_workspace_template,
    load_bee_workspace_command_intents,
    load_bee_workspace_template,
)
from coding_agent.topic_store import JSONObject, JSONValue

BeeLaunchSource = str
BeeLaunchStatus = str

_MAX_SAFE_LABEL_CHARS: Final[int] = 128
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_LAUNCH_SOURCES: Final[frozenset[str]] = frozenset(
    {"manual", "schedule", "proactive_signal"}
)
_LAUNCH_STATUSES: Final[frozenset[str]] = frozenset(
    {"planned", "launching", "launched", "failed", "cancelled"}
)
_FORBIDDEN_METADATA_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "args",
        "argv",
        "bearer",
        "cmd",
        "command",
        "commands",
        "command_output",
        "content",
        "credential",
        "credentials",
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
    }
)
_FORBIDDEN_LABEL_VALUE_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "bearer",
        "command_output",
        "content",
        "credential",
        "credentials",
        "env",
        "environment",
        "key",
        "message",
        "password",
        "prompt",
        "result",
        "secret",
        "stderr",
        "stdout",
        "text",
        "token",
    }
)
_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "-----begin ",
    "akia",
    "bearer ",
    "gho_",
    "ghp_",
    "kubeconfig",
    "password=",
    "private key",
    "secret=",
    "token=",
)


@dataclass(frozen=True)
class BeeLaunchRecord:
    launch_id: str
    source: BeeLaunchSource
    template_id: str
    status: BeeLaunchStatus
    requested_at: datetime
    task_id: str | None = None
    topic_id: str | None = None
    session_id: str | None = None
    workspace_ref: str | None = None
    schedule_id: str | None = None
    signal_id: str | None = None
    launched_at: datetime | None = None
    finished_at: datetime | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("launch_id", self.launch_id)
        _require_status("launch source", self.source, _LAUNCH_SOURCES)
        _require_non_empty("template_id", self.template_id)
        _require_status("launch status", self.status, _LAUNCH_STATUSES)
        _require_datetime("requested_at", self.requested_at)
        _require_optional_id("task_id", self.task_id)
        _require_optional_id("topic_id", self.topic_id)
        _require_optional_id("session_id", self.session_id)
        _require_optional_label("workspace_ref", self.workspace_ref)
        _require_optional_id("schedule_id", self.schedule_id)
        _require_optional_id("signal_id", self.signal_id)
        _require_optional_datetime("launched_at", self.launched_at)
        _require_optional_datetime("finished_at", self.finished_at)
        _require_optional_label("error_type", self.error_type)
        _require_optional_display_text("error_message", self.error_message)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class BeeLaunchRequest:
    launch_id: str
    source: BeeLaunchSource
    template_id: str
    workspace_root: Path
    requested_at: datetime
    inputs: JSONObject = field(default_factory=dict)
    topic_policy: JSONObject = field(default_factory=dict)
    workspace_policy: JSONObject = field(default_factory=dict)
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("launch_id", self.launch_id)
        _require_status("launch source", self.source, _LAUNCH_SOURCES)
        _require_non_empty("template_id", self.template_id)
        if not isinstance(self.workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        _require_datetime("requested_at", self.requested_at)
        _require_json_object("inputs", self.inputs)
        _require_json_object("topic_policy", self.topic_policy)
        _require_json_object("workspace_policy", self.workspace_policy)
        _require_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class BeeTemplateResolution:
    template_id: str
    template_kind: str
    template_profile: str
    template_title: str
    node_ids: tuple[str, ...]
    command_intent_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("template_id", self.template_id)
        _require_optional_label("template_kind", self.template_kind)
        _require_optional_label("template_profile", self.template_profile)
        _require_optional_display_text("template_title", self.template_title)
        for node_id in self.node_ids:
            _require_non_empty("node_id", node_id)
        for command_intent_name in self.command_intent_names:
            _require_optional_label("command_intent_name", command_intent_name)


@dataclass(frozen=True)
class BeeInputBinding:
    inputs: JSONObject
    required_input_names: tuple[str, ...] = ()
    defaulted_input_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_json_object("inputs", self.inputs)
        for input_name in self.required_input_names:
            _require_optional_label("required_input_name", input_name)
        for input_name in self.defaulted_input_names:
            _require_optional_label("defaulted_input_name", input_name)


@dataclass(frozen=True)
class BeeLaunchPlan:
    launch_id: str
    source: BeeLaunchSource
    requested_at: datetime
    template: BeeWorkspaceTemplate
    manifest: BeeTaskManifest
    resolution: BeeTemplateResolution
    input_binding: BeeInputBinding
    topic_policy: JSONObject
    workspace_policy: JSONObject
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("launch_id", self.launch_id)
        _require_status("launch source", self.source, _LAUNCH_SOURCES)
        _require_datetime("requested_at", self.requested_at)
        _require_json_object("topic_policy", self.topic_policy)
        _require_json_object("workspace_policy", self.workspace_policy)
        _require_json_object("metadata", self.metadata)


class PGBeeLaunchStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS bee_launches (
        launch_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        template_id TEXT NOT NULL,
        task_id TEXT,
        topic_id TEXT,
        session_id TEXT,
        workspace_ref TEXT,
        schedule_id TEXT,
        signal_id TEXT,
        status TEXT NOT NULL,
        requested_at TIMESTAMPTZ NOT NULL,
        launched_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        error_type TEXT,
        error_message TEXT,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS bee_launches_source_status_requested_idx
        ON bee_launches (source, status, requested_at, launch_id);

    CREATE INDEX IF NOT EXISTS bee_launches_schedule_requested_idx
        ON bee_launches (schedule_id, requested_at, launch_id);

    CREATE INDEX IF NOT EXISTS bee_launches_signal_requested_idx
        ON bee_launches (signal_id, requested_at, launch_id);
    """
    _UPSERT_LAUNCH_SQL: Final[str] = """
    INSERT INTO bee_launches (
        launch_id,
        source,
        template_id,
        task_id,
        topic_id,
        session_id,
        workspace_ref,
        schedule_id,
        signal_id,
        status,
        requested_at,
        launched_at,
        finished_at,
        error_type,
        error_message,
        metadata
    )
    VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8,
        $9, $10, $11, $12, $13, $14, $15, $16::jsonb
    )
    ON CONFLICT (launch_id)
    DO UPDATE SET
        source = EXCLUDED.source,
        template_id = EXCLUDED.template_id,
        task_id = EXCLUDED.task_id,
        topic_id = EXCLUDED.topic_id,
        session_id = EXCLUDED.session_id,
        workspace_ref = EXCLUDED.workspace_ref,
        schedule_id = EXCLUDED.schedule_id,
        signal_id = EXCLUDED.signal_id,
        status = EXCLUDED.status,
        requested_at = EXCLUDED.requested_at,
        launched_at = EXCLUDED.launched_at,
        finished_at = EXCLUDED.finished_at,
        error_type = EXCLUDED.error_type,
        error_message = EXCLUDED.error_message,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _SELECT_LAUNCH_SQL: Final[str] = """
    SELECT * FROM bee_launches WHERE launch_id = $1
    """
    _LIST_LAUNCHES_SQL: Final[str] = """
    SELECT * FROM bee_launches
    WHERE ($1::text IS NULL OR source = $1)
      AND ($2::text IS NULL OR status = $2)
      AND ($3::text IS NULL OR session_id = $3)
      AND ($4::text IS NULL OR topic_id = $4)
    ORDER BY requested_at, launch_id
    LIMIT $5
    """
    _UPDATE_LAUNCH_STATUS_SQL: Final[str] = """
    UPDATE bee_launches
    SET status = $2,
        launched_at = $3,
        finished_at = $4,
        error_type = $5,
        error_message = $6,
        metadata = $7::jsonb
    WHERE launch_id = $1
    RETURNING *
    """
    _ATTACH_LAUNCH_RESULT_SQL: Final[str] = """
    UPDATE bee_launches
    SET task_id = $2,
        topic_id = $3,
        session_id = $4,
        status = 'launched',
        launched_at = $5,
        metadata = $6::jsonb
    WHERE launch_id = $1
    RETURNING *
    """

    def __init__(self, *, pool: PGPool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_SCHEMA_SQL)
            self._schema_ready = True
        return pool

    async def create_launch(self, record: BeeLaunchRecord) -> BeeLaunchRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_LAUNCH_SQL,
            record.launch_id,
            record.source,
            record.template_id,
            record.task_id,
            record.topic_id,
            record.session_id,
            record.workspace_ref,
            record.schedule_id,
            record.signal_id,
            record.status,
            record.requested_at,
            record.launched_at,
            record.finished_at,
            record.error_type,
            record.error_message,
            record.metadata,
        )
        return _launch_from_row(_required_row(row, "bee launch upsert"))

    async def load_launch(self, launch_id: str) -> BeeLaunchRecord | None:
        _require_non_empty("launch_id", launch_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_LAUNCH_SQL, launch_id)
        return None if row is None else _launch_from_row(row)

    async def list_launches(
        self,
        *,
        source: BeeLaunchSource | None = None,
        status: BeeLaunchStatus | None = None,
        session_id: str | None = None,
        topic_id: str | None = None,
        limit: int = 100,
    ) -> list[BeeLaunchRecord]:
        if source is not None:
            _require_status("launch source", source, _LAUNCH_SOURCES)
        if status is not None:
            _require_status("launch status", status, _LAUNCH_STATUSES)
        _require_optional_id("session_id", session_id)
        _require_optional_id("topic_id", topic_id)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_LAUNCHES_SQL, source, status, session_id, topic_id, limit
        )
        return [_launch_from_row(row) for row in rows]

    async def update_launch_status(
        self,
        launch_id: str,
        *,
        status: BeeLaunchStatus,
        launched_at: datetime | None,
        finished_at: datetime | None,
        error_type: str | None,
        error_message: str | None,
        metadata: JSONObject,
    ) -> BeeLaunchRecord:
        _require_non_empty("launch_id", launch_id)
        _require_status("launch status", status, _LAUNCH_STATUSES)
        _require_optional_datetime("launched_at", launched_at)
        _require_optional_datetime("finished_at", finished_at)
        _require_optional_label("error_type", error_type)
        _require_optional_display_text("error_message", error_message)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_LAUNCH_STATUS_SQL,
            launch_id,
            status,
            launched_at,
            finished_at,
            error_type,
            error_message,
            metadata,
        )
        if row is None:
            raise KeyError(f"Bee launch not found: {launch_id}")
        return _launch_from_row(row)

    async def attach_launch_result(
        self,
        launch_id: str,
        *,
        task_id: str,
        topic_id: str,
        session_id: str,
        launched_at: datetime,
        metadata: JSONObject,
    ) -> BeeLaunchRecord:
        _require_non_empty("launch_id", launch_id)
        _require_non_empty("task_id", task_id)
        _require_non_empty("topic_id", topic_id)
        _require_non_empty("session_id", session_id)
        _require_datetime("launched_at", launched_at)
        _require_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._ATTACH_LAUNCH_RESULT_SQL,
            launch_id,
            task_id,
            topic_id,
            session_id,
            launched_at,
            metadata,
        )
        if row is None:
            raise KeyError(f"Bee launch not found: {launch_id}")
        return _launch_from_row(row)


def build_bee_launch_plan(request: BeeLaunchRequest) -> BeeLaunchPlan:
    """Resolve a Bee template and bind safe launch inputs without execution."""

    if not request.workspace_root.exists():
        raise FileNotFoundError(
            f"Bee launch workspace not found: {request.workspace_root}"
        )
    if request.workspace_root.is_symlink():
        raise ValueError(
            f"Bee launch workspace must not be a symlink: {request.workspace_root}"
        )
    if not request.workspace_root.is_dir():
        raise ValueError(
            f"Bee launch workspace must be a directory: {request.workspace_root}"
        )
    bee_root = request.workspace_root / ".bee"
    if bee_root.is_symlink():
        raise ValueError(f"Bee launch .bee root must not be a symlink: {bee_root}")
    template = load_bee_workspace_template(
        request.workspace_root,
        request.template_id,
    )
    manifest = build_bee_manifest_from_workspace_template(template)
    command_intents = load_bee_workspace_command_intents(template)
    input_binding = _bind_launch_inputs(
        provided=request.inputs,
        template_metadata=template.metadata,
    )
    return BeeLaunchPlan(
        launch_id=request.launch_id,
        source=request.source,
        requested_at=request.requested_at,
        template=template,
        manifest=manifest,
        resolution=BeeTemplateResolution(
            template_id=template.template_id,
            template_kind=manifest.kind,
            template_profile=manifest.profile,
            template_title=manifest.title,
            node_ids=tuple(node.node_id for node in manifest.nodes),
            command_intent_names=tuple(intent.name for intent in command_intents),
        ),
        input_binding=input_binding,
        topic_policy=dict(request.topic_policy),
        workspace_policy=dict(request.workspace_policy),
        metadata=dict(request.metadata),
    )


def _bind_launch_inputs(
    *,
    provided: JSONObject,
    template_metadata: JSONObject,
) -> BeeInputBinding:
    inputs_contract = template_metadata.get("inputs", {})
    if inputs_contract is None:
        inputs_contract = {}
    if not isinstance(inputs_contract, dict):
        raise TypeError("Bee template inputs must be an object")
    required_names = _input_name_tuple(inputs_contract.get("required", ()))
    defaults = _input_defaults(inputs_contract.get("defaults", {}))
    allowed_names = set(required_names) | set(defaults)
    unknown_names = sorted(name for name in provided if name not in allowed_names)
    if unknown_names:
        raise ValueError("unknown Bee launch inputs: " + ", ".join(unknown_names))
    missing_names = sorted(name for name in required_names if name not in provided)
    if missing_names:
        raise ValueError(
            "missing required Bee launch inputs: " + ", ".join(missing_names)
        )
    bound_inputs: JSONObject = dict(defaults)
    bound_inputs.update(provided)
    defaulted_names = tuple(name for name in defaults if name not in provided)
    return BeeInputBinding(
        inputs=bound_inputs,
        required_input_names=required_names,
        defaulted_input_names=defaulted_names,
    )


def _input_name_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError("Bee template inputs.required must be a list")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("Bee template required input names must be strings")
        _require_optional_label("input_name", item)
        names.append(item)
    if len(set(names)) != len(names):
        raise ValueError("Bee template required input names must be unique")
    return tuple(names)


def _input_defaults(value: object) -> JSONObject:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("Bee template inputs.defaults must be an object")
    defaults = dict(value)
    _require_json_object("inputs.defaults", defaults)
    for key in defaults:
        _require_optional_label("input_name", key)
    return defaults


def _required_row(
    row: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _launch_from_row(row: dict[str, object]) -> BeeLaunchRecord:
    return BeeLaunchRecord(
        launch_id=_required_str(row, "launch_id", context="bee launch row"),
        source=_required_str(row, "source", context="bee launch row"),
        template_id=_required_str(row, "template_id", context="bee launch row"),
        task_id=_optional_str(row, "task_id", context="bee launch row"),
        topic_id=_optional_str(row, "topic_id", context="bee launch row"),
        session_id=_optional_str(row, "session_id", context="bee launch row"),
        workspace_ref=_optional_str(row, "workspace_ref", context="bee launch row"),
        schedule_id=_optional_str(row, "schedule_id", context="bee launch row"),
        signal_id=_optional_str(row, "signal_id", context="bee launch row"),
        status=_required_str(row, "status", context="bee launch row"),
        requested_at=_required_datetime(row, "requested_at", context="bee launch row"),
        launched_at=_optional_datetime(row, "launched_at", context="bee launch row"),
        finished_at=_optional_datetime(row, "finished_at", context="bee launch row"),
        error_type=_optional_str(row, "error_type", context="bee launch row"),
        error_message=_optional_str(row, "error_message", context="bee launch row"),
        metadata=_required_json_object(row, "metadata", context="bee launch row"),
    )


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_optional_id(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)


def _require_optional_label(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_SAFE_LABEL_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_SAFE_LABEL_CHARS} characters"
        )
    folded = value.casefold()
    if any(part in folded for part in _FORBIDDEN_LABEL_VALUE_PARTS):
        raise ValueError(f"{field_name} must not contain sensitive label text")
    _reject_secret_shaped_value(field_name, value)


def _require_status(field_name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_optional_datetime(field_name: str, value: datetime | None) -> None:
    if value is not None:
        _require_datetime(field_name, value)


def _require_optional_display_text(field_name: str, value: str | None) -> None:
    if value is None:
        return
    _require_non_empty(field_name, value)
    if len(value) > _MAX_DISPLAY_TEXT_CHARS:
        raise ValueError(
            f"{field_name} must be at most {_MAX_DISPLAY_TEXT_CHARS} characters"
        )
    _reject_secret_shaped_value(field_name, value)


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int")


def _require_json_object(field_name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        key_folded = key.casefold()
        if any(part in key_folded for part in _FORBIDDEN_METADATA_KEY_PARTS):
            raise ValueError(f"{field_name} contains forbidden metadata key: {key}")
        _require_json_value(f"{field_name}.{key}", item)


def _require_json_value(field_name: str, value: JSONValue) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            if len(value) > _MAX_METADATA_STRING_CHARS:
                raise ValueError(
                    f"{field_name} must be at most {_MAX_METADATA_STRING_CHARS} characters"
                )
            _reject_secret_shaped_value(field_name, value)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_value(f"{field_name}[{index}]", item)
        return
    if isinstance(value, dict):
        _require_json_object(field_name, value)
        return
    raise TypeError(f"{field_name} must be JSON-safe")


def _reject_secret_shaped_value(field_name: str, value: str) -> None:
    folded = value.casefold()
    if any(marker in folded for marker in _SECRET_VALUE_MARKERS):
        raise ValueError(f"{field_name} must not contain secret-shaped values")


def _required_str(row: dict[str, object], key: str, *, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string {key}")
    return value


def _optional_str(row: dict[str, object], key: str, *, context: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string or None {key}")
    return value


def _required_datetime(row: dict[str, object], key: str, *, context: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime {key}")
    return value


def _optional_datetime(
    row: dict[str, object], key: str, *, context: str
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object], key: str, *, context: str
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include dict {key}")
    _require_json_object(key, value)
    return value
