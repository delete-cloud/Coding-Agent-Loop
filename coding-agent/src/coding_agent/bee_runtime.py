"""Generic Bee workflow task manifest records.

Bee is a Coding Agent product/runtime profile over Topic. This module only
parses sanitized task intent; it does not execute nodes or bypass action safety.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from typing import Protocol

from agentkit.storage.pg import AsyncPGPool, PGPool
from agentkit.tape.anchor import Anchor
from agentkit.tape.tape import Tape
from coding_agent.topic_store import (
    JSONObject,
    JSONValue,
    TopicAnchorRecord,
    TopicRecord,
)

_MANIFEST_VERSION: Final[int] = 1
_MAX_SAFE_LABEL_CHARS: Final[int] = 128
_MAX_DISPLAY_TEXT_CHARS: Final[int] = 256
_MAX_METADATA_STRING_CHARS: Final[int] = 256
_MAX_NODES: Final[int] = 64
_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "running", "completed", "failed", "cancelled"}
)
_NODE_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "ready", "running", "completed", "failed", "skipped"}
)
_TASK_FINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled"}
)
_RESERVED_ANCHOR_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {"encoded_anchor_type", "product_anchor_type", "task_status"}
)
_FORBIDDEN_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "bearer",
        "command_output",
        "credential",
        "credentials",
        "content",
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

BeeTaskStatus = str
BeeNodeStatus = str

BEE_TASK_STARTED = "bee_task_started"
BEE_TASK_FINALIZED = "bee_task_finalized"
BEE_TASK_ABORTED = "bee_task_aborted"
_BEE_ENCODED_ANCHOR_TYPE = "context"


class BeeTopicAnchorStore(Protocol):
    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord: ...


class BeeTaskPlannerStore(Protocol):
    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        topic_id: str | None = None,
        status: BeeTaskStatus | None = None,
        limit: int = 100,
    ) -> list[BeeTaskRecord]: ...

    async def list_nodes(self, task_id: str) -> list[BeeNodeRecord]: ...

    async def update_node_status(
        self,
        *,
        task_id: str,
        node_id: str,
        status: BeeNodeStatus,
        run_id: str | None,
        updated_at: datetime,
        started_at: datetime | None,
        finished_at: datetime | None,
        metadata: JSONObject,
    ) -> BeeNodeRecord: ...


@dataclass(frozen=True)
class BeeTaskRecord:
    task_id: str
    topic_id: str
    session_id: str
    kind: str
    profile: str
    status: BeeTaskStatus
    title: str
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("topic_id", self.topic_id)
        _require_non_empty("session_id", self.session_id)
        _require_safe_label("kind", self.kind)
        _require_safe_label("profile", self.profile)
        _require_status("task status", self.status, _TASK_STATUSES)
        _require_display_text("title", self.title)
        _require_optional_display_text("summary", self.summary)
        _require_datetime("created_at", self.created_at)
        _require_datetime("updated_at", self.updated_at)
        _require_safe_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class BeeNodeRecord:
    node_id: str
    task_id: str
    kind: str
    profile: str
    status: BeeNodeStatus
    title: str
    created_at: datetime
    updated_at: datetime
    depends_on: tuple[str, ...] = ()
    run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("node_id", self.node_id)
        _require_non_empty("task_id", self.task_id)
        _require_safe_label("kind", self.kind)
        _require_safe_label("profile", self.profile)
        _require_status("node status", self.status, _NODE_STATUSES)
        _require_display_text("title", self.title)
        for dependency in self.depends_on:
            _require_non_empty("depends_on", dependency)
        if self.node_id in self.depends_on:
            raise ValueError(f"Bee node cannot depend on itself: {self.node_id}")
        _require_optional_id("run_id", self.run_id)
        _require_datetime("created_at", self.created_at)
        _require_datetime("updated_at", self.updated_at)
        if self.started_at is not None:
            _require_datetime("started_at", self.started_at)
        if self.finished_at is not None:
            _require_datetime("finished_at", self.finished_at)
        _require_safe_json_object("metadata", self.metadata)


@dataclass(frozen=True)
class BeeNodeLaunchIntent:
    task_id: str
    node_id: str
    topic_id: str
    session_id: str
    task_kind: str
    task_profile: str
    node_kind: str
    node_profile: str
    reason: str
    planned_at: datetime
    metadata: JSONObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("node_id", self.node_id)
        _require_non_empty("topic_id", self.topic_id)
        _require_non_empty("session_id", self.session_id)
        _require_safe_label("task_kind", self.task_kind)
        _require_safe_label("task_profile", self.task_profile)
        _require_safe_label("node_kind", self.node_kind)
        _require_safe_label("node_profile", self.node_profile)
        _require_safe_label("reason", self.reason)
        _require_datetime("planned_at", self.planned_at)
        _require_safe_json_object("metadata", self.metadata)


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
    "bearer ",
    "gho_",
    "ghp_",
    "github_pat_",
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


class PGBeeTaskStore:
    _CREATE_SCHEMA_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS bee_tasks (
        task_id TEXT PRIMARY KEY,
        topic_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        profile TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE INDEX IF NOT EXISTS bee_tasks_session_status_created_idx
        ON bee_tasks (session_id, status, created_at, task_id);

    CREATE INDEX IF NOT EXISTS bee_tasks_topic_status_created_idx
        ON bee_tasks (topic_id, status, created_at, task_id);

    CREATE TABLE IF NOT EXISTS bee_task_nodes (
        node_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        profile TEXT NOT NULL,
        status TEXT NOT NULL,
        title TEXT NOT NULL,
        depends_on JSONB NOT NULL DEFAULT '[]'::jsonb,
        run_id TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (task_id, node_id),
        FOREIGN KEY (task_id) REFERENCES bee_tasks(task_id) ON DELETE CASCADE
    );

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'bee_task_nodes_task_id_fkey'
        ) THEN
            ALTER TABLE bee_task_nodes
                ADD CONSTRAINT bee_task_nodes_task_id_fkey
                FOREIGN KEY (task_id)
                REFERENCES bee_tasks(task_id)
                ON DELETE CASCADE
                NOT VALID;
        END IF;
    END
    $$;

    CREATE INDEX IF NOT EXISTS bee_task_nodes_task_status_idx
        ON bee_task_nodes (task_id, status, node_id);
    """
    _UPSERT_TASK_SQL: Final[str] = """
    INSERT INTO bee_tasks (
        task_id,
        topic_id,
        session_id,
        kind,
        profile,
        status,
        title,
        summary,
        created_at,
        updated_at,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
    ON CONFLICT (task_id)
    DO UPDATE SET
        topic_id = EXCLUDED.topic_id,
        session_id = EXCLUDED.session_id,
        kind = EXCLUDED.kind,
        profile = EXCLUDED.profile,
        status = EXCLUDED.status,
        title = EXCLUDED.title,
        summary = EXCLUDED.summary,
        updated_at = EXCLUDED.updated_at,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _SELECT_TASK_SQL: Final[str] = "SELECT * FROM bee_tasks WHERE task_id = $1"
    _LIST_TASKS_SQL: Final[str] = """
    SELECT * FROM bee_tasks
    WHERE ($1::text IS NULL OR session_id = $1)
      AND ($2::text IS NULL OR topic_id = $2)
      AND ($3::text IS NULL OR status = $3)
    ORDER BY created_at, task_id
    LIMIT $4
    """
    _UPDATE_TASK_STATUS_SQL: Final[str] = """
    UPDATE bee_tasks
    SET status = $2,
        summary = $3,
        updated_at = $4,
        metadata = $5::jsonb
    WHERE task_id = $1
    RETURNING *
    """
    _UPSERT_NODE_SQL: Final[str] = """
    INSERT INTO bee_task_nodes (
        node_id,
        task_id,
        kind,
        profile,
        status,
        title,
        depends_on,
        run_id,
        created_at,
        updated_at,
        started_at,
        finished_at,
        metadata
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13::jsonb)
    ON CONFLICT (task_id, node_id)
    DO UPDATE SET
        kind = EXCLUDED.kind,
        profile = EXCLUDED.profile,
        status = EXCLUDED.status,
        title = EXCLUDED.title,
        depends_on = EXCLUDED.depends_on,
        run_id = EXCLUDED.run_id,
        updated_at = EXCLUDED.updated_at,
        started_at = EXCLUDED.started_at,
        finished_at = EXCLUDED.finished_at,
        metadata = EXCLUDED.metadata
    RETURNING *
    """
    _LIST_NODES_SQL: Final[str] = """
    SELECT * FROM bee_task_nodes
    WHERE task_id = $1
    ORDER BY created_at, node_id
    """
    _SELECT_NODE_SQL: Final[str] = """
    SELECT * FROM bee_task_nodes
    WHERE task_id = $1 AND node_id = $2
    """
    _UPDATE_NODE_STATUS_SQL: Final[str] = """
    UPDATE bee_task_nodes
    SET status = $3,
        run_id = $4,
        updated_at = $5,
        started_at = $6,
        finished_at = $7,
        metadata = $8::jsonb
    WHERE task_id = $1 AND node_id = $2
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

    async def upsert_task(self, record: BeeTaskRecord) -> BeeTaskRecord:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPSERT_TASK_SQL,
            record.task_id,
            record.topic_id,
            record.session_id,
            record.kind,
            record.profile,
            record.status,
            record.title,
            record.summary,
            record.created_at,
            record.updated_at,
            record.metadata,
        )
        return _bee_task_from_row(_required_row(row, "bee task upsert"))

    async def load_task(self, task_id: str) -> BeeTaskRecord | None:
        _require_non_empty("task_id", task_id)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_TASK_SQL, task_id)
        if row is None:
            return None
        return _bee_task_from_row(row)

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        topic_id: str | None = None,
        status: BeeTaskStatus | None = None,
        limit: int = 100,
    ) -> list[BeeTaskRecord]:
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if topic_id is not None:
            _require_non_empty("topic_id", topic_id)
        if status is not None:
            _require_status("task status", status, _TASK_STATUSES)
        _require_positive_int("limit", limit)
        pool = await self._ensure_schema()
        rows = await pool.fetch(
            self._LIST_TASKS_SQL, session_id, topic_id, status, limit
        )
        return [_bee_task_from_row(row) for row in rows]

    async def update_task_status(
        self,
        task_id: str,
        *,
        status: BeeTaskStatus,
        summary: str | None,
        updated_at: datetime,
        metadata: JSONObject,
    ) -> BeeTaskRecord:
        _require_non_empty("task_id", task_id)
        _require_status("task status", status, _TASK_STATUSES)
        _require_optional_display_text("summary", summary)
        _require_datetime("updated_at", updated_at)
        _require_safe_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_TASK_STATUS_SQL,
            task_id,
            status,
            summary,
            updated_at,
            metadata,
        )
        if row is None:
            raise KeyError(f"Bee task not found: {task_id}")
        return _bee_task_from_row(row)

    async def upsert_node(self, record: BeeNodeRecord) -> BeeNodeRecord:
        pool = await self._ensure_schema()
        await self._require_task_exists(pool, record.task_id)
        await self._require_dependencies_exist(pool, record)
        row = await pool.fetchrow(
            self._UPSERT_NODE_SQL,
            record.node_id,
            record.task_id,
            record.kind,
            record.profile,
            record.status,
            record.title,
            list(record.depends_on),
            record.run_id,
            record.created_at,
            record.updated_at,
            record.started_at,
            record.finished_at,
            record.metadata,
        )
        return _bee_node_from_row(_required_row(row, "bee node upsert"))

    async def _require_task_exists(
        self,
        pool: AsyncPGPool,
        task_id: str,
    ) -> None:
        row = await pool.fetchrow(self._SELECT_TASK_SQL, task_id)
        if row is None:
            raise KeyError(f"Bee task not found for node: {task_id}")

    async def _require_dependencies_exist(
        self,
        pool: AsyncPGPool,
        record: BeeNodeRecord,
    ) -> None:
        missing: list[str] = []
        for dependency_id in record.depends_on:
            row = await pool.fetchrow(
                self._SELECT_NODE_SQL,
                record.task_id,
                dependency_id,
            )
            if row is None:
                missing.append(dependency_id)
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise ValueError(f"Bee node dependencies not found: {missing_list}")

    async def list_nodes(self, task_id: str) -> list[BeeNodeRecord]:
        _require_non_empty("task_id", task_id)
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_NODES_SQL, task_id)
        return [_bee_node_from_row(row) for row in rows]

    async def update_node_status(
        self,
        *,
        task_id: str,
        node_id: str,
        status: BeeNodeStatus,
        run_id: str | None,
        updated_at: datetime,
        started_at: datetime | None,
        finished_at: datetime | None,
        metadata: JSONObject,
    ) -> BeeNodeRecord:
        _require_non_empty("task_id", task_id)
        _require_non_empty("node_id", node_id)
        _require_status("node status", status, _NODE_STATUSES)
        _require_optional_id("run_id", run_id)
        _require_datetime("updated_at", updated_at)
        if started_at is not None:
            _require_datetime("started_at", started_at)
        if finished_at is not None:
            _require_datetime("finished_at", finished_at)
        _require_safe_json_object("metadata", metadata)
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            self._UPDATE_NODE_STATUS_SQL,
            task_id,
            node_id,
            status,
            run_id,
            updated_at,
            started_at,
            finished_at,
            metadata,
        )
        if row is None:
            raise KeyError(f"Bee node not found: {task_id}/{node_id}")
        return _bee_node_from_row(row)


class BeeTaskLifecycle:
    def __init__(self, *, anchor_store: BeeTopicAnchorStore) -> None:
        self._anchor_store = anchor_store

    async def start_task(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        task: BeeTaskRecord,
        metadata: JSONObject | None = None,
    ) -> TopicAnchorRecord:
        caller_metadata = dict(metadata or {})
        _reject_reserved_anchor_metadata(caller_metadata)
        return await self._write_task_anchor(
            tape=tape,
            topic=topic,
            task=task,
            product_anchor_type=BEE_TASK_STARTED,
            label="Bee task started",
            metadata=caller_metadata,
        )

    async def finalize_task(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        task: BeeTaskRecord,
        status: BeeTaskStatus,
        metadata: JSONObject | None = None,
    ) -> TopicAnchorRecord:
        _require_status("final task status", status, _TASK_FINAL_STATUSES)
        if task.status != status:
            raise ValueError(
                f"task {task.task_id} status {task.status} does not match {status}"
            )
        product_anchor_type = (
            BEE_TASK_ABORTED if status == "cancelled" else BEE_TASK_FINALIZED
        )
        caller_metadata = dict(metadata or {})
        _reject_reserved_anchor_metadata(caller_metadata)
        return await self._write_task_anchor(
            tape=tape,
            topic=topic,
            task=task,
            product_anchor_type=product_anchor_type,
            label=f"Bee task {status}",
            metadata={**caller_metadata, "task_status": status},
        )

    async def _write_task_anchor(
        self,
        *,
        tape: Tape,
        topic: TopicRecord,
        task: BeeTaskRecord,
        product_anchor_type: str,
        label: str,
        metadata: JSONObject,
    ) -> TopicAnchorRecord:
        _require_matching_topic(tape=tape, topic=topic, task=task)
        _require_safe_label("product_anchor_type", product_anchor_type)
        _require_safe_json_object("metadata", metadata)
        anchor = Anchor(
            anchor_type=_BEE_ENCODED_ANCHOR_TYPE,
            payload={"label": label},
            meta={
                "topic_id": topic.topic_id,
                "task_id": task.task_id,
                "product_anchor_type": product_anchor_type,
                "skip": True,
            },
        )
        seq = _append_anchor(tape, anchor)
        record = TopicAnchorRecord(
            topic_id=topic.topic_id,
            tape_id=topic.tape_id,
            seq=seq,
            anchor_type=product_anchor_type,
            entry_id=anchor.id,
            metadata={
                **metadata,
                "encoded_anchor_type": _BEE_ENCODED_ANCHOR_TYPE,
                "product_anchor_type": product_anchor_type,
            },
        )
        try:
            return await self._anchor_store.record_topic_anchor(record)
        except Exception:
            _remove_anchor_by_id(tape, entry_id=anchor.id)
            raise


class BeeTaskPlanner:
    def __init__(self, *, store: BeeTaskPlannerStore) -> None:
        self._store = store

    async def plan_ready_nodes(
        self,
        *,
        now: datetime,
        max_nodes: int,
        max_tasks: int = 100,
        session_id: str | None = None,
        topic_id: str | None = None,
    ) -> list[BeeNodeLaunchIntent]:
        _require_datetime("now", now)
        _require_positive_int("max_nodes", max_nodes)
        _require_positive_int("max_tasks", max_tasks)
        if session_id is not None:
            _require_non_empty("session_id", session_id)
        if topic_id is not None:
            _require_non_empty("topic_id", topic_id)

        tasks = await self._store.list_tasks(
            session_id=session_id,
            topic_id=topic_id,
            status="running",
            limit=max_tasks,
        )
        intents: list[BeeNodeLaunchIntent] = []
        for task in tasks:
            nodes = await self._store.list_nodes(task.task_id)
            completed_node_ids = {
                node.node_id for node in nodes if node.status == "completed"
            }
            for node in nodes:
                if len(intents) >= max_nodes:
                    return intents
                if node.status != "pending":
                    continue
                if not _node_dependencies_ready(node, completed_node_ids):
                    continue
                updated_node = await self._store.update_node_status(
                    task_id=node.task_id,
                    node_id=node.node_id,
                    status="ready",
                    run_id=None,
                    updated_at=now,
                    started_at=None,
                    finished_at=None,
                    metadata={
                        **node.metadata,
                        "planner_state": "ready",
                        "planner_reason": "dependencies_ready",
                    },
                )
                intents.append(
                    BeeNodeLaunchIntent(
                        task_id=task.task_id,
                        node_id=updated_node.node_id,
                        topic_id=task.topic_id,
                        session_id=task.session_id,
                        task_kind=task.kind,
                        task_profile=task.profile,
                        node_kind=updated_node.kind,
                        node_profile=updated_node.profile,
                        reason="dependencies_ready",
                        planned_at=now,
                        metadata={
                            "task_kind": task.kind,
                            "task_profile": task.profile,
                            "node_kind": updated_node.kind,
                            "node_profile": updated_node.profile,
                        },
                    )
                )
        return intents


def _node_dependencies_ready(
    node: BeeNodeRecord,
    completed_node_ids: set[str],
) -> bool:
    return all(dependency_id in completed_node_ids for dependency_id in node.depends_on)


def _append_anchor(tape: Tape, anchor: Anchor) -> int:
    with tape._lock:
        seq = len(tape._entries)
        tape._entries.append(anchor)
        return seq


def _remove_anchor_by_id(tape: Tape, *, entry_id: str) -> None:
    with tape._lock:
        for index, entry in enumerate(tape._entries):
            if entry.id == entry_id:
                del tape._entries[index]
                return


def _parse_topic_binding(raw: JSONObject) -> BeeTopicBinding:
    return BeeTopicBinding(
        session_id=_require_string(raw, "session_id"),
        topic_id=_optional_string(raw, "topic_id"),
        tape_id=_optional_string(raw, "tape_id"),
        title_hint=_optional_string(raw, "title_hint"),
        metadata=dict(_optional_object(raw, "metadata")),
    )


def _require_matching_topic(
    *,
    tape: Tape,
    topic: TopicRecord,
    task: BeeTaskRecord,
) -> None:
    if tape.tape_id != topic.tape_id:
        raise ValueError(f"topic {topic.topic_id} belongs to tape {topic.tape_id}")
    if task.topic_id != topic.topic_id:
        raise ValueError(f"task {task.task_id} belongs to topic {task.topic_id}")
    if task.session_id != topic.session_id:
        raise ValueError(f"task {task.task_id} belongs to session {task.session_id}")
    if topic.status != "open":
        raise ValueError(f"Bee task anchors require an open topic: {topic.topic_id}")


def _reject_reserved_anchor_metadata(metadata: JSONObject) -> None:
    for key in metadata:
        if key in _RESERVED_ANCHOR_METADATA_KEYS:
            raise ValueError(f"Bee anchor metadata uses reserved key: {key}")


def _required_row(
    row: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _bee_task_from_row(row: dict[str, object]) -> BeeTaskRecord:
    return BeeTaskRecord(
        task_id=_required_str(row, "task_id", context="bee task row"),
        topic_id=_required_str(row, "topic_id", context="bee task row"),
        session_id=_required_str(row, "session_id", context="bee task row"),
        kind=_required_str(row, "kind", context="bee task row"),
        profile=_required_str(row, "profile", context="bee task row"),
        status=_required_str(row, "status", context="bee task row"),
        title=_required_str(row, "title", context="bee task row"),
        summary=_optional_str(row, "summary", context="bee task row"),
        created_at=_required_datetime(row, "created_at", context="bee task row"),
        updated_at=_required_datetime(row, "updated_at", context="bee task row"),
        metadata=_required_json_object(row, "metadata", context="bee task row"),
    )


def _bee_node_from_row(row: dict[str, object]) -> BeeNodeRecord:
    return BeeNodeRecord(
        node_id=_required_str(row, "node_id", context="bee node row"),
        task_id=_required_str(row, "task_id", context="bee node row"),
        kind=_required_str(row, "kind", context="bee node row"),
        profile=_required_str(row, "profile", context="bee node row"),
        status=_required_str(row, "status", context="bee node row"),
        title=_required_str(row, "title", context="bee node row"),
        depends_on=_required_string_tuple(
            row,
            "depends_on",
            context="bee node row",
        ),
        run_id=_optional_str(row, "run_id", context="bee node row"),
        created_at=_required_datetime(row, "created_at", context="bee node row"),
        updated_at=_required_datetime(row, "updated_at", context="bee node row"),
        started_at=_optional_datetime(row, "started_at", context="bee node row"),
        finished_at=_optional_datetime(row, "finished_at", context="bee node row"),
        metadata=_required_json_object(row, "metadata", context="bee node row"),
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
    normalized = _normalize_manifest_key(key)
    for forbidden in _FORBIDDEN_KEY_PARTS:
        if _key_contains_token(normalized, forbidden):
            raise ValueError(f"{path}.{key} uses forbidden sensitive field")
    for forbidden in _FORBIDDEN_EXECUTABLE_KEYS:
        if _key_contains_token(normalized, forbidden):
            raise ValueError(f"{path}.{key} uses forbidden executable field")


def _normalize_manifest_key(key: str) -> str:
    with_separators = key.strip().replace("-", "_")
    with_separators = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", with_separators)
    return with_separators.lower()


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
        if node.node_id in node.depends_on:
            raise ValueError(f"Bee node cannot depend on itself: {node.node_id}")
    missing = {
        dependency
        for node in nodes
        for dependency in node.depends_on
        if dependency not in seen
    }
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"Bee node dependencies not found: {missing_list}")
    _reject_dependency_cycles(nodes)


def _reject_dependency_cycles(nodes: tuple[BeeNodeManifest, ...]) -> None:
    dependencies_by_node = {node.node_id: set(node.depends_on) for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"Bee node dependency cycle includes: {node_id}")
        visiting.add(node_id)
        for dependency_id in dependencies_by_node[node_id]:
            visit(dependency_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node.node_id)


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


def _require_status(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")


def _require_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int")


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
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include object {key}")
    _require_safe_json_object(key, value)
    return dict(value)


def _required_string_tuple(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError(f"postgres {context} must include list {key}")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"postgres {context} must include string {key}[{index}]")
        result.append(item)
    return tuple(result)
