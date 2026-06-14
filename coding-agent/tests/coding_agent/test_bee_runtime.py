from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from agentkit.observability import SpanRecord
from agentkit.tape.tape import Tape
from coding_agent.bee.runtime import (
    BEE_TASK_ABORTED,
    BEE_TASK_FINALIZED,
    BEE_TASK_STARTED,
    BeeNodeLaunchIntent,
    BeeNodeRecord,
    BeeTaskLifecycle,
    BeeTaskManifest,
    BeeTaskPlanner,
    BeeTaskRecord,
    PGBeeTaskStore,
    build_bee_launch_metadata,
    parse_bee_task_manifest,
)
from coding_agent.observability import (
    PrometheusMetricsObservationSink,
    PrometheusMetricsRecorder,
)
from coding_agent.server.developer_console import (
    ConsoleBeeNodeSummary,
    ConsoleBeePage,
    render_console_bee_page,
)
from coding_agent.topics.lifecycle import find_topic_anchors
from coding_agent.topics.store import JSONObject, TopicAnchorRecord, TopicRecord


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
        if "SELECT * FROM bee_task_nodes" in query and "node_id = $2" in query:
            task_id, node_id = args
            return self.nodes.get((cast(str, task_id), cast(str, node_id)))
        if "INSERT INTO bee_task_nodes" in query:
            row = _node_row(*args)
            key = (cast(str, row["task_id"]), cast(str, row["node_id"]))
            self.nodes[key] = row
            return row
        if "AND status = 'pending'" in query:
            return self._claim_ready_node(args)
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

    def _claim_ready_node(self, args: tuple[object, ...]) -> dict[str, object] | None:
        task_id, node_id, updated_at, metadata = args
        row = self.nodes.get((cast(str, task_id), cast(str, node_id)))
        if row is None or row["status"] != "pending":
            return None
        row.update(
            {
                "status": "ready",
                "run_id": None,
                "updated_at": updated_at,
                "started_at": None,
                "finished_at": None,
                "metadata": metadata,
            }
        )
        return row


class FakeBeeTopicAnchorStore:
    def __init__(self) -> None:
        self.anchors: list[TopicAnchorRecord] = []

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record


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
    assert manifest.nodes[1].command_ref == "pytest_smoke"
    assert manifest.nodes[1].metadata == {"expected_policy": "validation"}


def test_bee_node_manifest_accepts_safe_command_ref() -> None:
    raw = _safe_manifest()
    raw["nodes"][0]["command_ref"] = "plan_check"  # type: ignore[index]

    manifest = parse_bee_task_manifest(raw)

    assert manifest.nodes[0].command_ref == "plan_check"


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
        (("nodes", 0, "commands"), ["pytest"]),
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
    assert "ALTER TABLE bee_task_nodes" in schema_calls[0]
    assert "NOT VALID" in schema_calls[0]


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
    assert await store.load_task("bee-task-alpha") == task
    assert await store.list_tasks(session_id="session-alpha") == [task]
    assert await store.list_tasks(topic_id="topic-alpha", status="pending") == [task]

    running = await store.update_task_status(
        "bee-task-alpha",
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
    assert await store.list_nodes("bee-task-alpha") == [plan_node, validate_node]

    launched = await store.update_node_status(
        task_id="bee-task-alpha",
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


@pytest.mark.asyncio
async def test_bee_store_rejects_orphan_nodes_and_missing_dependencies() -> None:
    pool = FakeBeePool()
    store = PGBeeTaskStore(pool=pool)  # type: ignore[arg-type]
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)

    with pytest.raises(KeyError, match="Bee task not found for node"):
        await store.upsert_node(_node_record(now, node_id="node-orphan"))

    await store.upsert_task(_task_record(now))
    with pytest.raises(ValueError, match="dependencies not found: node-missing"):
        await store.upsert_node(
            _node_record(
                now,
                node_id="node-validate",
                kind="validation",
                depends_on=("node-missing",),
            )
        )


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


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_writes_safe_task_anchors() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    tape = Tape(tape_id="tape-alpha")
    topic = _topic_record(now)
    task = _task_record(now)
    store = FakeBeeTopicAnchorStore()
    lifecycle = BeeTaskLifecycle(anchor_store=store)

    started = await lifecycle.start_task(
        tape=tape,
        topic=topic,
        task=task,
        metadata={"task_kind": "maintenance"},
    )
    finalized = await lifecycle.finalize_task(
        tape=tape,
        topic=topic,
        task=replace(task, status="completed"),
        status="completed",
        metadata={"final_state": "completed"},
    )

    assert [record.anchor_type for record in store.anchors] == [
        BEE_TASK_STARTED,
        BEE_TASK_FINALIZED,
    ]
    assert started.metadata == {
        "encoded_anchor_type": "context",
        "product_anchor_type": BEE_TASK_STARTED,
        "task_kind": "maintenance",
    }
    assert finalized.metadata["task_status"] == "completed"

    anchors = find_topic_anchors(tape)
    assert [anchor.product_anchor_type for anchor in anchors] == [
        BEE_TASK_STARTED,
        BEE_TASK_FINALIZED,
    ]
    entries = tape.snapshot()
    assert entries[0].payload == {"label": "Bee task started"}
    assert entries[1].payload == {"label": "Bee task completed"}
    assert entries[0].meta["skip"] is True
    rendered = repr([entry.to_dict() for entry in entries]).lower()
    for forbidden in (
        "prompt",
        "message",
        "content",
        "command_output",
        "stdout",
        "stderr",
        "secret",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_accounts_for_legacy_topic_plugin_boundary() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    tape = Tape(tape_id="tape-alpha")
    topic = _topic_record(now)
    task = _task_record(now)
    store = FakeBeeTopicAnchorStore()
    lifecycle = BeeTaskLifecycle(anchor_store=store)

    await lifecycle.start_task(tape=tape, topic=topic, task=task)
    await lifecycle.finalize_task(
        tape=tape,
        topic=topic,
        task=replace(task, status="cancelled"),
        status="cancelled",
    )

    entries = tape.snapshot()
    assert [entry.anchor_type for entry in entries] == ["context", "context"]
    assert [entry.meta["product_anchor_type"] for entry in entries] == [
        BEE_TASK_STARTED,
        BEE_TASK_ABORTED,
    ]
    assert all(
        entry.anchor_type not in {"topic_start", "topic_end"} for entry in entries
    )
    assert [record.metadata["encoded_anchor_type"] for record in store.anchors] == [
        "context",
        "context",
    ]


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_rolls_back_tape_anchor_on_store_failure() -> None:
    class FailingAnchorStore(FakeBeeTopicAnchorStore):
        async def record_topic_anchor(
            self,
            record: TopicAnchorRecord,
        ) -> TopicAnchorRecord:
            raise RuntimeError("anchor store unavailable")

    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    tape = Tape(tape_id="tape-alpha")
    lifecycle = BeeTaskLifecycle(anchor_store=FailingAnchorStore())

    with pytest.raises(RuntimeError, match="anchor store unavailable"):
        await lifecycle.start_task(
            tape=tape,
            topic=_topic_record(now),
            task=_task_record(now),
        )

    assert tape.snapshot() == ()


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_rejects_closed_topic() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    topic = replace(_topic_record(now), status="finalized", topic_finalized_seq=0)
    lifecycle = BeeTaskLifecycle(anchor_store=FakeBeeTopicAnchorStore())

    with pytest.raises(ValueError, match="require an open topic"):
        await lifecycle.start_task(
            tape=Tape(tape_id="tape-alpha"),
            topic=topic,
            task=_task_record(now),
        )


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_rejects_non_final_or_mismatched_status() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    lifecycle = BeeTaskLifecycle(anchor_store=FakeBeeTopicAnchorStore())

    with pytest.raises(ValueError, match="final task status"):
        await lifecycle.finalize_task(
            tape=Tape(tape_id="tape-alpha"),
            topic=_topic_record(now),
            task=replace(_task_record(now), status="running"),
            status="running",
        )

    with pytest.raises(ValueError, match="does not match completed"):
        await lifecycle.finalize_task(
            tape=Tape(tape_id="tape-alpha"),
            topic=_topic_record(now),
            task=replace(_task_record(now), status="pending"),
            status="completed",
        )


@pytest.mark.asyncio
async def test_bee_topic_lifecycle_rejects_reserved_anchor_metadata() -> None:
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    lifecycle = BeeTaskLifecycle(anchor_store=FakeBeeTopicAnchorStore())

    with pytest.raises(ValueError, match="reserved key: product_anchor_type"):
        await lifecycle.start_task(
            tape=Tape(tape_id="tape-alpha"),
            topic=_topic_record(now),
            task=_task_record(now),
            metadata={"product_anchor_type": "topic_finalized"},
        )

    with pytest.raises(ValueError, match="reserved key: task_status"):
        await lifecycle.finalize_task(
            tape=Tape(tape_id="tape-alpha"),
            topic=_topic_record(now),
            task=replace(_task_record(now), status="completed"),
            status="completed",
            metadata={"task_status": "running"},
        )


@pytest.mark.asyncio
async def test_bee_planner_returns_bounded_launch_intents_without_execution() -> None:
    pool = FakeBeePool()
    store = PGBeeTaskStore(pool=pool)  # type: ignore[arg-type]
    planner = BeeTaskPlanner(store=store)
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    planned_at = now + timedelta(minutes=10)

    await store.upsert_task(replace(_task_record(now), status="running"))
    await store.upsert_node(
        replace(_node_record(now, node_id="node-plan"), status="completed")
    )
    await store.upsert_node(
        _node_record(
            now + timedelta(minutes=1),
            node_id="node-validate",
            kind="validation",
            depends_on=("node-plan",),
        )
    )
    await store.upsert_node(
        _node_record(
            now + timedelta(minutes=2),
            node_id="node-report",
            kind="report",
        )
    )

    intents = await planner.plan_ready_nodes(now=planned_at, max_nodes=1)

    assert [intent.node_id for intent in intents] == ["node-validate"]
    assert intents[0].task_id == "bee-task-alpha"
    assert intents[0].topic_id == "topic-alpha"
    assert intents[0].session_id == "session-alpha"
    assert intents[0].node_kind == "validation"
    assert intents[0].reason == "dependencies_ready"
    assert intents[0].planned_at == planned_at
    assert intents[0].metadata == {
        "task_kind": "maintenance",
        "task_profile": "local",
        "node_kind": "validation",
        "node_profile": "default",
    }
    updated_nodes = await store.list_nodes("bee-task-alpha")
    node_by_id = {node.node_id: node for node in updated_nodes}
    assert node_by_id["node-validate"].status == "ready"
    assert node_by_id["node-validate"].run_id is None
    assert node_by_id["node-validate"].started_at is None
    assert node_by_id["node-validate"].finished_at is None
    assert node_by_id["node-validate"].metadata == {
        "source": "fixture",
        "planner_state": "ready",
        "planner_reason": "dependencies_ready",
    }
    assert node_by_id["node-report"].status == "pending"
    assert all("runtime_runs" not in query for query, _ in pool.executed)


@pytest.mark.asyncio
async def test_bee_planner_skips_blocked_non_pending_and_non_running_tasks() -> None:
    pool = FakeBeePool()
    store = PGBeeTaskStore(pool=pool)  # type: ignore[arg-type]
    planner = BeeTaskPlanner(store=store)
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)

    await store.upsert_task(replace(_task_record(now), status="running"))
    await store.upsert_node(
        replace(_node_record(now, node_id="node-plan"), status="running")
    )
    await store.upsert_node(
        _node_record(
            now + timedelta(minutes=1),
            node_id="node-validate",
            kind="validation",
            depends_on=("node-plan",),
        )
    )
    await store.upsert_node(
        replace(
            _node_record(
                now + timedelta(minutes=2),
                node_id="node-ready",
                kind="validation",
            ),
            status="ready",
        )
    )
    await store.upsert_task(
        replace(
            _task_record(now + timedelta(minutes=3)),
            task_id="bee-beta",
            topic_id="topic-beta",
            session_id="session-beta",
            status="pending",
        )
    )
    await store.upsert_node(
        replace(
            _node_record(now + timedelta(minutes=3), node_id="node-beta"),
            task_id="bee-beta",
        )
    )

    assert (
        await planner.plan_ready_nodes(
            now=now + timedelta(minutes=10),
            max_nodes=10,
        )
        == []
    )
    assert [
        (node.node_id, node.status) for node in await store.list_nodes("bee-task-alpha")
    ] == [
        ("node-plan", "running"),
        ("node-validate", "pending"),
        ("node-ready", "ready"),
    ]
    assert [node.status for node in await store.list_nodes("bee-beta")] == ["pending"]

    await store.update_task_status(
        "bee-beta",
        status="running",
        summary=None,
        updated_at=now + timedelta(minutes=11),
        metadata={"source": "fixture"},
    )
    assert (
        await planner.plan_ready_nodes(
            now=now + timedelta(minutes=12),
            max_nodes=1,
            max_tasks=1,
        )
        == []
    )

    beta_intents = await planner.plan_ready_nodes(
        now=now + timedelta(minutes=13),
        max_nodes=1,
        max_tasks=2,
    )
    assert [(intent.task_id, intent.node_id) for intent in beta_intents] == [
        ("bee-beta", "node-beta")
    ]
    assert [node.status for node in await store.list_nodes("bee-beta")] == ["ready"]


@pytest.mark.asyncio
async def test_bee_planner_atomically_claims_ready_node_once() -> None:
    class RaceStore:
        def __init__(self) -> None:
            self.task = replace(_task_record(now), status="running")
            self.node = _node_record(now, node_id="node-plan")
            self.claims = 0

        async def list_tasks(
            self,
            *,
            session_id: str | None = None,
            topic_id: str | None = None,
            status: str | None = None,
            limit: int = 100,
        ) -> list[BeeTaskRecord]:
            assert session_id is None
            assert topic_id is None
            assert status == "running"
            assert limit == 100
            return [self.task]

        async def list_nodes(self, task_id: str) -> list[BeeNodeRecord]:
            assert task_id == "bee-task-alpha"
            return [replace(self.node, status="pending")]

        async def claim_ready_node(
            self,
            *,
            task_id: str,
            node_id: str,
            updated_at: datetime,
            metadata: JSONObject,
        ) -> BeeNodeRecord | None:
            assert task_id == "bee-task-alpha"
            assert node_id == "node-plan"
            assert updated_at == planned_at
            assert metadata["planner_reason"] == "dependencies_ready"
            self.claims += 1
            if self.claims > 1:
                return None
            self.node = replace(
                self.node,
                status="ready",
                updated_at=updated_at,
                metadata=dict(metadata),
            )
            return self.node

    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    planned_at = now + timedelta(minutes=10)
    store = RaceStore()
    planner = BeeTaskPlanner(store=store)

    first = await planner.plan_ready_nodes(now=planned_at, max_nodes=1)
    second = await planner.plan_ready_nodes(now=planned_at, max_nodes=1)

    assert [intent.node_id for intent in first] == ["node-plan"]
    assert second == []
    assert store.claims == 2


def test_bee_launch_metadata_preserves_approval_and_workspace_policy() -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())
    intent = _launch_intent(manifest, node_id="node-validate")

    metadata = build_bee_launch_metadata(manifest=manifest, intent=intent)

    assert metadata == {
        "bee_runtime": "task_launch",
        "launch_kind": "durable_run",
        "task_id": "bee-task-alpha",
        "node_id": "node-validate",
        "topic_id": "topic-alpha",
        "session_id": "session-alpha",
        "task_kind": "maintenance",
        "task_profile": "local",
        "node_kind": "validation",
        "node_profile": "default",
        "approval_policy": "existing_runtime_policy",
        "action_policy": "existing_action_safety",
        "workspace_binding": "existing_workspace_provider",
        "workspace_policy": "default",
        "context_profile": "repo",
        "context_reference": "profile_only",
        "validation_profile": "pytest",
        "validation_reference": "profile_only",
        "command_ref": "pytest_smoke",
        "command_reference": "workspace_intent_only",
    }


def test_bee_context_and_validation_metadata_is_reference_only() -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())
    intent = _launch_intent(manifest, node_id="node-plan")

    metadata = build_bee_launch_metadata(manifest=manifest, intent=intent)

    assert metadata["context_profile"] == "repo"
    assert metadata["context_reference"] == "profile_only"
    assert metadata["validation_profile"] == "pytest"
    assert metadata["validation_reference"] == "profile_only"
    for forbidden in (
        "prompt",
        "message",
        "content",
        "command_output",
        "stdout",
        "stderr",
        "secret",
        "text",
    ):
        assert forbidden not in metadata


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("session_id", "session-other", "session mismatch"),
        ("topic_id", "topic-other", "topic mismatch"),
        ("task_kind", "ops", "task kind mismatch"),
        ("task_profile", "remote", "task profile mismatch"),
        ("node_kind", "analysis", "node kind mismatch"),
        ("node_profile", "remote", "node profile mismatch"),
        ("node_id", "node-missing", "node not found"),
    ],
)
def test_bee_launch_metadata_rejects_mismatched_manifest_and_intent(
    field: str,
    value: str,
    match: str,
) -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())
    intent = replace(
        _launch_intent(manifest, node_id="node-validate"), **{field: value}
    )

    with pytest.raises(ValueError, match=match):
        build_bee_launch_metadata(manifest=manifest, intent=intent)


@pytest.mark.asyncio
async def test_bee_runtime_smoke_manifest_topic_launch_console_metrics() -> None:
    manifest = parse_bee_task_manifest(_safe_manifest())
    now = datetime(2026, 5, 22, 9, tzinfo=UTC)
    tape = Tape(tape_id="tape-alpha")
    topic = _topic_record(now)
    task = _task_record(now)
    bee_pool = FakeBeePool()
    bee_store = PGBeeTaskStore(pool=bee_pool)  # type: ignore[arg-type]
    anchor_store = FakeBeeTopicAnchorStore()
    lifecycle = BeeTaskLifecycle(anchor_store=anchor_store)

    await bee_store.upsert_task(replace(task, status="running"))
    await bee_store.upsert_node(
        replace(_node_record(now, node_id="node-plan"), status="completed")
    )
    await bee_store.upsert_node(
        _node_record(
            now + timedelta(minutes=1),
            node_id="node-validate",
            kind="validation",
            depends_on=("node-plan",),
        )
    )
    started_anchor = await lifecycle.start_task(
        tape=tape,
        topic=topic,
        task=task,
        metadata={"task_kind": "maintenance"},
    )

    intents = await BeeTaskPlanner(store=bee_store).plan_ready_nodes(
        now=now + timedelta(minutes=2),
        max_nodes=1,
    )
    launch_metadata = build_bee_launch_metadata(
        manifest=manifest,
        intent=intents[0],
    )
    completed_task = replace(task, status="completed")
    finalized_anchor = await lifecycle.finalize_task(
        tape=tape,
        topic=topic,
        task=completed_task,
        status="completed",
    )

    console_html = render_console_bee_page(
        ConsoleBeePage(
            tasks=(),
            nodes=(
                ConsoleBeeNodeSummary(
                    task_id=str(launch_metadata["task_id"]),
                    node_id=str(launch_metadata["node_id"]),
                    run_id="run-bee",
                    topic_id=str(launch_metadata["topic_id"]),
                    session_id=str(launch_metadata["session_id"]),
                    task_kind=str(launch_metadata["task_kind"]),
                    task_profile=str(launch_metadata["task_profile"]),
                    kind=str(launch_metadata["node_kind"]),
                    profile=str(launch_metadata["node_profile"]),
                    status="completed",
                    context_profile=str(launch_metadata["context_profile"]),
                    validation_profile=str(launch_metadata["validation_profile"]),
                    workspace_policy=str(launch_metadata["workspace_policy"]),
                    approval_policy=str(launch_metadata["approval_policy"]),
                    action_policy=str(launch_metadata["action_policy"]),
                    workspace_binding=str(launch_metadata["workspace_binding"]),
                ),
            ),
        )
    )
    recorder = PrometheusMetricsRecorder()
    PrometheusMetricsObservationSink(recorder=recorder).record_span(
        SpanRecord(
            name="runtime.stage.dispatch",
            status="ok",
            attributes={
                "task_id": str(launch_metadata["task_id"]),
                "node_id": str(launch_metadata["node_id"]),
                "topic_id": str(launch_metadata["topic_id"]),
                "run_id": "run-bee",
                "session_id": str(launch_metadata["session_id"]),
                "task_kind": str(launch_metadata["task_kind"]),
                "task_profile": str(launch_metadata["task_profile"]),
                "task_status": completed_task.status,
                "node_kind": str(launch_metadata["node_kind"]),
                "node_profile": str(launch_metadata["node_profile"]),
                "node_status": "completed",
            },
            duration_ms=1,
        )
    )
    metrics_text = recorder.exposition_text()

    assert [record.anchor_type for record in anchor_store.anchors] == [
        BEE_TASK_STARTED,
        BEE_TASK_FINALIZED,
    ]
    assert [anchor.product_anchor_type for anchor in find_topic_anchors(tape)] == [
        BEE_TASK_STARTED,
        BEE_TASK_FINALIZED,
    ]
    assert started_anchor.metadata["task_kind"] == "maintenance"
    assert finalized_anchor.metadata["task_status"] == "completed"
    assert [intent.node_id for intent in intents] == ["node-validate"]
    assert launch_metadata["approval_policy"] == "existing_runtime_policy"
    assert launch_metadata["action_policy"] == "existing_action_safety"
    assert launch_metadata["workspace_binding"] == "existing_workspace_provider"
    assert "Bee Node Launches" in console_html
    assert "bee-task-alpha" in console_html
    assert "node-validate" in console_html
    assert "existing_action_safety" in console_html
    assert 'task_kind="maintenance"' in metrics_text
    assert 'task_profile="local"' in metrics_text
    assert 'task_status="completed"' in metrics_text
    assert 'node_kind="validation"' in metrics_text
    assert 'node_profile="default"' in metrics_text
    assert 'node_status="completed"' in metrics_text
    for forbidden in (
        "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
        "raw prompt",
        "raw message",
        "command_output",
        "stdout",
        "stderr",
    ):
        assert forbidden not in console_html
    for forbidden in (
        "task_id",
        "node_id",
        "topic_id",
        "run_id",
        "session_id",
        "bee-task-alpha",
        "node-validate",
        "topic-alpha",
        "run-bee",
        "session-alpha",
    ):
        assert forbidden not in metrics_text


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
                "command_ref": "pytest_smoke",
                "metadata": {"expected_policy": "validation"},
            },
        ],
        "metadata": {"source": "fixture", "risk": "low"},
    }


def _task_record(now: datetime) -> BeeTaskRecord:
    return BeeTaskRecord(
        task_id="bee-task-alpha",
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


def _topic_record(now: datetime) -> TopicRecord:
    return TopicRecord(
        topic_id="topic-alpha",
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="coding",
        status="open",
        title="Release docs",
        summary=None,
        owner=None,
        topic_initial_seq=0,
        topic_finalized_seq=None,
        created_at=now,
        finalized_at=None,
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
        task_id="bee-task-alpha",
        kind=kind,
        profile="default",
        status="pending",
        title=f"{node_id} title",
        depends_on=depends_on,
        created_at=now,
        updated_at=now,
        metadata={"source": "fixture"},
    )


def _launch_intent(
    manifest: BeeTaskManifest,
    *,
    node_id: str,
) -> BeeNodeLaunchIntent:
    node_by_id = {node.node_id: node for node in manifest.nodes}
    node = node_by_id[node_id]
    return BeeNodeLaunchIntent(
        task_id="bee-task-alpha",
        node_id=node.node_id,
        topic_id="topic-alpha",
        session_id=manifest.topic.session_id,
        task_kind=manifest.kind,
        task_profile=manifest.profile,
        node_kind=node.kind,
        node_profile=node.profile,
        reason="dependencies_ready",
        planned_at=datetime(2026, 5, 22, 9, tzinfo=UTC),
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
