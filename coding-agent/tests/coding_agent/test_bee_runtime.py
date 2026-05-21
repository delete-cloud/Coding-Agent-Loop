from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from coding_agent.bee_runtime import (
    BeeNodeRecord,
    BeeTaskManifest,
    BeeTaskRecord,
    PGBeeTaskStore,
    parse_bee_task_manifest,
)
from coding_agent.topic_store import JSONObject


class FakeBeePool:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, object]] = {}
        self.nodes: dict[tuple[str, str], dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def get_pool(self) -> FakeBeePool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS bee_tasks" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO bee_tasks" in query:
            row = _task_row(*args)
            self.tasks[cast(str, row["task_id"])] = row
            return row
        if "SELECT * FROM bee_tasks WHERE task_id = $1" in query:
            return self.tasks.get(cast(str, args[0]))
        if "UPDATE bee_tasks" in query:
            return self._update_task(args)
        if "INSERT INTO bee_task_nodes" in query:
            row = _node_row(*args)
            key = (cast(str, row["task_id"]), cast(str, row["node_id"]))
            self.nodes[key] = row
            return row
        if "UPDATE bee_task_nodes" in query:
            return self._update_node(args)
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM bee_tasks" in query:
            session_id, topic_id, status, limit = args
            rows = [
                row
                for row in self.tasks.values()
                if (session_id is None or row["session_id"] == session_id)
                and (topic_id is None or row["topic_id"] == topic_id)
                and (status is None or row["status"] == status)
            ]
            rows.sort(
                key=lambda row: (cast(datetime, row["created_at"]), row["task_id"])
            )
            return rows[: cast(int, limit)]
        if "SELECT * FROM bee_task_nodes" in query:
            task_id = cast(str, args[0])
            rows = [
                row
                for (row_task_id, _), row in self.nodes.items()
                if row_task_id == task_id
            ]
            rows.sort(
                key=lambda row: (cast(datetime, row["created_at"]), row["node_id"])
            )
            return rows
        raise AssertionError(f"unexpected fetch query: {query}")

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeBeePool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _update_task(self, args: tuple[object, ...]) -> dict[str, object] | None:
        task_id, status, summary, updated_at, metadata = args
        row = self.tasks.get(cast(str, task_id))
        if row is None:
            return None
        row.update(
            {
                "status": status,
                "summary": summary,
                "updated_at": updated_at,
                "metadata": metadata,
            }
        )
        return row

    def _update_node(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            task_id,
            node_id,
            status,
            run_id,
            updated_at,
            started_at,
            finished_at,
            metadata,
        ) = args
        row = self.nodes.get((cast(str, task_id), cast(str, node_id)))
        if row is None:
            return None
        row.update(
            {
                "status": status,
                "run_id": run_id,
                "updated_at": updated_at,
                "started_at": started_at,
                "finished_at": finished_at,
                "metadata": metadata,
            }
        )
        return row


def test_bee_manifest_parses_safe_fixture() -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())

    assert manifest == BeeTaskManifest(
        version=1,
        kind="maintenance",
        profile="local",
        title="Refresh release docs",
        summary="Check release docs and validation status.",
        context_profile="repo",
        validation_profile="pytest",
        workspace_policy="default",
        topic=manifest.topic,
        nodes=manifest.nodes,
        metadata={"source": "fixture", "risk": "low"},
    )
    assert manifest.topic.session_id == "session-alpha"
    assert manifest.topic.topic_id == "topic-alpha"
    assert manifest.topic.tape_id == "tape-alpha"
    assert manifest.topic.metadata == {"source": "manual"}
    assert [node.node_id for node in manifest.nodes] == [
        "node-plan",
        "node-validate",
    ]
    assert manifest.nodes[1].depends_on == ("node-plan",)
    assert manifest.nodes[1].validation_profile == "pytest"
    assert manifest.nodes[1].metadata == {"expected_policy": "validation"}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("metadata", "prompt"), "raw prompt"),
        (("metadata", "nested", "message"), "raw message"),
        (("nodes", 0, "metadata", "stdout"), "raw output"),
        (("topic", "metadata", "secret_name"), "token"),
        (("metadata", "environment", "GITHUB_TOKEN"), "ghp_example"),
        (("metadata", "api_key"), "abc123"),
        (("metadata", "password"), "abc123"),
        (("metadata", "bearer_token"), "abc123"),
        (("metadata", "GITHUB_TOKEN"), "abc123"),
        (("metadata", "AWS_SESSION_TOKEN"), "abc123"),
    ],
)
def test_bee_manifest_rejects_raw_sensitive_fields(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        parse_bee_task_manifest(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("metadata", "safe_marker"), "token=abc123"),
        (("nodes", 0, "metadata", "safe_label"), "sk-test-value"),
        (("topic", "title_hint"), "secret=hidden"),
        (("metadata", "safe_marker_2"), "ghp_example"),
    ],
)
def test_bee_manifest_rejects_secret_like_values(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="secret-like value"):
        parse_bee_task_manifest(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("nodes", 0, "command"), "pytest"),
        (("nodes", 0, "run_command"), "pytest"),
        (("nodes", 0, "runCommand"), "pytest"),
        (("nodes", 0, "shell_command"), "pytest"),
        (("nodes", 0, "shellCommand"), "pytest"),
        (("nodes", 0, "command_spec"), "pytest"),
        (("nodes", 0, "commandSpec"), "pytest"),
        (("nodes", 0, "pre_commands"), "pytest"),
        (("nodes", 0, "preCommands"), "pytest"),
        (("nodes", 0, "metadata", "executor"), "local"),
        (("metadata", "script"), "run checks"),
    ],
)
def test_bee_manifest_rejects_executable_fields(
    path: tuple[str | int, ...],
    value: str,
) -> None:
    raw = _safe_manifest()
    _set_path(raw, path, value)

    with pytest.raises(ValueError, match="forbidden executable field"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_unknown_node_dependencies() -> None:
    raw = _safe_manifest()
    raw["nodes"][1]["depends_on"] = ["node-missing"]  # type: ignore[index]

    with pytest.raises(ValueError, match="dependencies not found: node-missing"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_self_dependency() -> None:
    raw = _safe_manifest()
    raw["nodes"][0]["depends_on"] = ["node-plan"]  # type: ignore[index]

    with pytest.raises(ValueError, match="cannot depend on itself: node-plan"):
        parse_bee_task_manifest(raw)


def test_bee_manifest_rejects_dependency_cycles() -> None:
    raw = _safe_manifest()
    raw["nodes"][0]["depends_on"] = ["node-validate"]  # type: ignore[index]
    raw["nodes"][1]["depends_on"] = ["node-plan"]  # type: ignore[index]

    with pytest.raises(ValueError, match="dependency cycle includes"):
        parse_bee_task_manifest(raw)


@pytest.mark.asyncio
async def test_bee_store_schema_is_idempotent() -> None:
    pool = FakeBeePool()
    store = PGBeeTaskStore(pool=pool)  # type: ignore[arg-type]

    await store.load_task("task-missing")
    await store.list_tasks(session_id="session-alpha")

    schema_calls = [
        query
        for query, _ in pool.executed
        if "CREATE TABLE IF NOT EXISTS bee_tasks" in query
    ]
    assert len(schema_calls) == 1


@pytest.mark.asyncio
async def test_bee_store_create_update_list_task_and_nodes() -> None:
    pool = FakeBeePool()
    store = PGBeeTaskStore(pool=pool)  # type: ignore[arg-type]
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    task = _task_record(now)
    plan_node = _node_record(now, node_id="node-plan")
    validate_node = _node_record(
        now + timedelta(minutes=1),
        node_id="node-validate",
        kind="validation",
        depends_on=("node-plan",),
    )

    stored_task = await store.upsert_task(task)
    assert stored_task == task
    assert await store.load_task("task-alpha") == task
    assert await store.list_tasks(session_id="session-alpha") == [task]
    assert await store.list_tasks(topic_id="topic-alpha", status="pending") == [task]

    running = await store.update_task_status(
        "task-alpha",
        status="running",
        summary="Task is active",
        updated_at=now + timedelta(minutes=2),
        metadata={"phase": "launch"},
    )
    assert running.status == "running"
    assert running.summary == "Task is active"
    assert running.metadata == {"phase": "launch"}

    assert await store.upsert_node(plan_node) == plan_node
    assert await store.upsert_node(validate_node) == validate_node
    assert await store.list_nodes("task-alpha") == [plan_node, validate_node]

    launched = await store.update_node_status(
        task_id="task-alpha",
        node_id="node-plan",
        status="running",
        run_id="run-alpha",
        updated_at=now + timedelta(minutes=3),
        started_at=now + timedelta(minutes=3),
        finished_at=None,
        metadata={"launch_kind": "manual"},
    )
    assert launched.status == "running"
    assert launched.run_id == "run-alpha"
    assert launched.metadata == {"launch_kind": "manual"}


def test_bee_task_record_rejects_invalid_status_and_unsafe_metadata() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)

    with pytest.raises(ValueError, match="task status"):
        replace(_task_record(now), status="done")

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        replace(_task_record(now), metadata={"GITHUB_TOKEN": "abc123"})


def test_bee_node_record_rejects_invalid_status_and_self_dependency() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)

    with pytest.raises(ValueError, match="node status"):
        replace(_node_record(now, node_id="node-plan"), status="waiting")

    with pytest.raises(ValueError, match="cannot depend on itself"):
        replace(
            _node_record(now, node_id="node-plan"),
            depends_on=("node-plan",),
        )


def _safe_manifest() -> JSONObject:
    return {
        "version": 1,
        "kind": "maintenance",
        "profile": "local",
        "title": "Refresh release docs",
        "summary": "Check release docs and validation status.",
        "context_profile": "repo",
        "validation_profile": "pytest",
        "workspace_policy": "default",
        "topic": {
            "session_id": "session-alpha",
            "topic_id": "topic-alpha",
            "tape_id": "tape-alpha",
            "title_hint": "Release docs",
            "metadata": {"source": "manual"},
        },
        "nodes": [
            {
                "node_id": "node-plan",
                "kind": "analysis",
                "profile": "default",
                "title": "Plan update",
                "depends_on": [],
                "context_profile": "repo",
                "metadata": {"expected_policy": "read_only"},
            },
            {
                "node_id": "node-validate",
                "kind": "validation",
                "profile": "default",
                "title": "Run validation",
                "depends_on": ["node-plan"],
                "validation_profile": "pytest",
                "metadata": {"expected_policy": "validation"},
            },
        ],
        "metadata": {"source": "fixture", "risk": "low"},
    }


def _task_record(now: datetime) -> BeeTaskRecord:
    return BeeTaskRecord(
        task_id="task-alpha",
        topic_id="topic-alpha",
        session_id="session-alpha",
        kind="maintenance",
        profile="local",
        status="pending",
        title="Refresh release docs",
        summary=None,
        created_at=now,
        updated_at=now,
        metadata={"source": "fixture"},
    )


def _node_record(
    now: datetime,
    *,
    node_id: str,
    kind: str = "analysis",
    depends_on: tuple[str, ...] = (),
) -> BeeNodeRecord:
    return BeeNodeRecord(
        node_id=node_id,
        task_id="task-alpha",
        kind=kind,
        profile="default",
        status="pending",
        title=f"{node_id} title",
        depends_on=depends_on,
        created_at=now,
        updated_at=now,
        metadata={"source": "fixture"},
    )


def _task_row(*args: object) -> dict[str, object]:
    (
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
        metadata,
    ) = args
    return {
        "task_id": task_id,
        "topic_id": topic_id,
        "session_id": session_id,
        "kind": kind,
        "profile": profile,
        "status": status,
        "title": title,
        "summary": summary,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": metadata,
    }


def _node_row(*args: object) -> dict[str, object]:
    (
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
        metadata,
    ) = args
    return {
        "node_id": node_id,
        "task_id": task_id,
        "kind": kind,
        "profile": profile,
        "status": status,
        "title": title,
        "depends_on": depends_on,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "metadata": metadata,
    }


def _set_path(raw: JSONObject, path: tuple[str | int, ...], value: str) -> None:
    cursor: object = raw
    for index, part in enumerate(path[:-1]):
        if isinstance(part, int):
            cursor = cursor[part]  # type: ignore[index]
        else:
            next_part = path[index + 1]
            if (
                isinstance(cursor, dict)
                and part not in cursor
                and isinstance(next_part, str)
            ):
                cursor[part] = {}
            cursor = cursor[part]  # type: ignore[index]
    last = path[-1]
    if isinstance(last, int):
        cursor[last] = value  # type: ignore[index]
    else:
        cursor[last] = value  # type: ignore[index]
