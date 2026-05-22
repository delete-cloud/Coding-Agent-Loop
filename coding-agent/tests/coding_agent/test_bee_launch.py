from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from agentkit.tape.tape import Tape
from coding_agent.bee_launch import (
    BeeInputBinding,
    BeeTaskLifecycleController,
    BeeLaunchOrchestrator,
    BeeLaunchRecord,
    BeeLaunchRequest,
    BeeTemplateResolution,
    PGBeeLaunchStore,
    ProactiveBeeLaunchOrchestrator,
    ScheduledBeeLaunchOrchestrator,
    build_bee_launch_plan,
)
from coding_agent.bee_runtime import BeeNodeRecord, BeeTaskLifecycle, BeeTaskRecord
from coding_agent.observability import prometheus_metrics_text, reset_prometheus_metrics
from coding_agent.scheduled_runs import ScheduledLaunchIntent, ScheduleTriggerRecord
from coding_agent.topic_lifecycle import TopicLifecycle
from coding_agent.topic_store import JSONObject, TopicAnchorRecord, TopicRecord


class FakeBeeLaunchPool:
    def __init__(self) -> None:
        self.launches: dict[str, dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def get_pool(self) -> FakeBeeLaunchPool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS bee_launches" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO bee_launches" in query:
            row = _launch_row(*args)
            self.launches[cast(str, row["launch_id"])] = row
            return row
        if "SELECT * FROM bee_launches WHERE launch_id = $1" in query:
            return self.launches.get(cast(str, args[0]))
        if "UPDATE bee_launches" in query and "task_id = $2" in query:
            return self._attach_result(args)
        if "UPDATE bee_launches" in query:
            return self._update_status(args)
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM bee_launches" not in query:
            raise AssertionError(f"unexpected fetch query: {query}")
        source, status, session_id, topic_id, limit = args
        rows = [
            row
            for row in self.launches.values()
            if (source is None or row["source"] == source)
            and (status is None or row["status"] == status)
            and (session_id is None or row["session_id"] == session_id)
            and (topic_id is None or row["topic_id"] == topic_id)
        ]
        rows.sort(
            key=lambda row: (cast(datetime, row["requested_at"]), row["launch_id"])
        )
        return rows[: cast(int, limit)]

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeBeeLaunchPool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _update_status(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            launch_id,
            status,
            launched_at,
            finished_at,
            error_type,
            error_message,
            metadata,
        ) = args
        row = self.launches.get(cast(str, launch_id))
        if row is None:
            return None
        row.update(
            {
                "status": status,
                "launched_at": launched_at,
                "finished_at": finished_at,
                "error_type": error_type,
                "error_message": error_message,
                "metadata": metadata,
            }
        )
        return row

    def _attach_result(self, args: tuple[object, ...]) -> dict[str, object] | None:
        launch_id, task_id, topic_id, session_id, launched_at, metadata = args
        row = self.launches.get(cast(str, launch_id))
        if row is None:
            return None
        row.update(
            {
                "task_id": task_id,
                "topic_id": topic_id,
                "session_id": session_id,
                "status": "launched",
                "launched_at": launched_at,
                "metadata": metadata,
            }
        )
        return row


class FakeTopicStore:
    def __init__(self) -> None:
        self.topics: dict[str, TopicRecord] = {}
        self.anchors: list[TopicAnchorRecord] = []

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        self.topics.setdefault(record.topic_id, record)
        return self.topics[record.topic_id]

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        topic = self.topics[topic_id]
        finalized = replace(
            topic,
            status="finalized",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = finalized
        return finalized

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: JSONObject,
    ) -> TopicRecord:
        topic = self.topics[topic_id]
        aborted = replace(
            topic,
            status="aborted",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=metadata,
        )
        self.topics[topic_id] = aborted
        return aborted

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record

    async def load_topic(self, topic_id: str) -> TopicRecord | None:
        return self.topics.get(topic_id)

    async def find_open_topic(
        self,
        *,
        session_id: str,
        tape_id: str,
    ) -> TopicRecord | None:
        for topic in self.topics.values():
            if (
                topic.session_id == session_id
                and topic.tape_id == tape_id
                and topic.status == "open"
            ):
                return topic
        return None


class FakeBeeTaskStore:
    def __init__(self) -> None:
        self.tasks: dict[str, BeeTaskRecord] = {}
        self.nodes: dict[tuple[str, str], BeeNodeRecord] = {}
        self.upserted_task_ids: list[str] = []

    async def upsert_task(self, record: BeeTaskRecord) -> BeeTaskRecord:
        self.tasks[record.task_id] = record
        self.upserted_task_ids.append(record.task_id)
        return record

    async def upsert_node(self, record: BeeNodeRecord) -> BeeNodeRecord:
        if record.task_id not in self.tasks:
            raise KeyError(f"Bee task not found for node: {record.task_id}")
        for dependency in record.depends_on:
            if (record.task_id, dependency) not in self.nodes:
                raise ValueError(f"Bee node dependencies not found: {dependency}")
        self.nodes[(record.task_id, record.node_id)] = record
        return record

    async def load_task(self, task_id: str) -> BeeTaskRecord | None:
        return self.tasks.get(task_id)

    async def list_nodes(self, task_id: str) -> list[BeeNodeRecord]:
        return [
            node
            for (row_task_id, _), node in self.nodes.items()
            if row_task_id == task_id
        ]

    async def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        summary: str | None,
        updated_at: datetime,
        metadata: JSONObject,
    ) -> BeeTaskRecord:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Bee task not found: {task_id}")
        updated = replace(
            task,
            status=status,
            summary=summary,
            updated_at=updated_at,
            metadata=metadata,
        )
        self.tasks[task_id] = updated
        return updated

    async def update_node_status(
        self,
        *,
        task_id: str,
        node_id: str,
        status: str,
        run_id: str | None,
        updated_at: datetime,
        started_at: datetime | None,
        finished_at: datetime | None,
        metadata: JSONObject,
    ) -> BeeNodeRecord:
        node = self.nodes.get((task_id, node_id))
        if node is None:
            raise KeyError(f"Bee node not found: {task_id}/{node_id}")
        updated = replace(
            node,
            status=status,
            run_id=run_id,
            updated_at=updated_at,
            started_at=started_at,
            finished_at=finished_at,
            metadata=metadata,
        )
        self.nodes[(task_id, node_id)] = updated
        return updated


class FakeScheduleTriggerStore:
    def __init__(self) -> None:
        self.triggers: dict[str, ScheduleTriggerRecord] = {}

    async def record_trigger(
        self,
        record: ScheduleTriggerRecord,
    ) -> ScheduleTriggerRecord:
        self.triggers[record.trigger_id] = record
        return record


class FakeClock:
    def __init__(self) -> None:
        self.now = _dt(9)

    def __call__(self) -> datetime:
        value = self.now
        self.now = self.now.replace(minute=self.now.minute + 1)
        return value


@pytest.fixture
def fake_pool() -> FakeBeeLaunchPool:
    return FakeBeeLaunchPool()


@pytest.fixture
def store(fake_pool: FakeBeeLaunchPool) -> PGBeeLaunchStore:
    return PGBeeLaunchStore(pool=fake_pool)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bee_launch_store_schema_is_idempotent(
    store: PGBeeLaunchStore,
    fake_pool: FakeBeeLaunchPool,
) -> None:
    await store.create_launch(_launch())
    await store.load_launch("launch-1")
    await store.list_launches(source="manual")

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS bee_launches" in query
    ]

    assert len(schema_calls) == 1
    assert "bee_launches_source_status_requested_idx" in schema_calls[0]
    assert "bee_launches_schedule_requested_idx" in schema_calls[0]
    assert "bee_launches_signal_requested_idx" in schema_calls[0]
    assert "bee_tasks" not in schema_calls[0]


@pytest.mark.asyncio
async def test_bee_launch_store_create_load_list_update_and_attach(
    store: PGBeeLaunchStore,
) -> None:
    created = await store.create_launch(_launch())
    await store.create_launch(_launch("launch-2", status="planned"))

    updated = await store.update_launch_status(
        "launch-1",
        status="launching",
        launched_at=None,
        finished_at=None,
        error_type=None,
        error_message=None,
        metadata={"phase": "resolving"},
    )
    attached = await store.attach_launch_result(
        "launch-1",
        task_id="task-1",
        topic_id="topic-1",
        session_id="session-1",
        launched_at=_dt(10),
        metadata={"phase": "created_task"},
    )
    loaded = await store.load_launch("launch-1")
    planned = await store.list_launches(status="planned")
    launched = await store.list_launches(source="manual", topic_id="topic-1")

    assert created.launch_id == "launch-1"
    assert updated.status == "launching"
    assert attached.status == "launched"
    assert attached.task_id == "task-1"
    assert attached.topic_id == "topic-1"
    assert attached.session_id == "session-1"
    assert loaded == attached
    assert planned == [_launch("launch-2", status="planned")]
    assert launched == [attached]


@pytest.mark.asyncio
async def test_bee_launch_store_links_schedule_and_signal_metadata(
    store: PGBeeLaunchStore,
) -> None:
    schedule_launch = await store.create_launch(
        _launch(
            "launch-schedule",
            source="schedule",
            schedule_id="schedule-1",
            metadata={"launch_mode": "scheduled"},
        )
    )
    signal_launch = await store.create_launch(
        _launch(
            "launch-signal",
            source="proactive_signal",
            signal_id="signal-1",
            metadata={"signal_kind": "repo_activity"},
        )
    )

    scheduled = await store.list_launches(source="schedule")
    signals = await store.list_launches(source="proactive_signal")

    assert schedule_launch.schedule_id == "schedule-1"
    assert signal_launch.signal_id == "signal-1"
    assert scheduled == [schedule_launch]
    assert signals == [signal_launch]


def test_bee_launch_record_rejects_invalid_values_and_sensitive_metadata() -> None:
    with pytest.raises(ValueError, match="launch source"):
        _launch(source="timer")

    with pytest.raises(ValueError, match="launch status"):
        _launch(status="executing")

    with pytest.raises(ValueError, match="forbidden metadata key"):
        _launch(metadata={"prompt": "do not store"})

    with pytest.raises(ValueError, match="secret-shaped"):
        _launch(error_message="token=secret")


@pytest.mark.asyncio
async def test_bee_launch_store_missing_rows_fail_fast(
    store: PGBeeLaunchStore,
) -> None:
    with pytest.raises(KeyError, match="Bee launch not found"):
        await store.update_launch_status(
            "missing-launch",
            status="failed",
            launched_at=None,
            finished_at=_dt(11),
            error_type="not_found",
            error_message="Launch record missing",
            metadata={"phase": "test"},
        )

    with pytest.raises(KeyError, match="Bee launch not found"):
        await store.attach_launch_result(
            "missing-launch",
            task_id="task-1",
            topic_id="topic-1",
            session_id="session-1",
            launched_at=_dt(10),
            metadata={},
        )


def test_bee_launch_plan_resolves_workspace_template(tmp_path: Path) -> None:
    _write_template(tmp_path, template_id="template-alpha")

    plan = build_bee_launch_plan(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            topic_policy={"mode": "create", "session_id": "session-alpha"},
            workspace_policy={"workspace_ref": "local", "artifact_mode": "enabled"},
            requested_at=_dt(9),
        )
    )

    assert plan.launch_id == "launch-1"
    assert plan.source == "manual"
    assert plan.template.template_id == "template-alpha"
    assert plan.resolution == BeeTemplateResolution(
        template_id="template-alpha",
        template_kind="maintenance",
        template_profile="local",
        template_title="Local template",
        node_ids=("node-plan",),
        command_intent_names=(),
    )
    assert plan.input_binding == BeeInputBinding(
        inputs={"region": "us-test-1", "severity": "low"},
        required_input_names=("region",),
        defaulted_input_names=("severity",),
    )
    assert plan.topic_policy == {"mode": "create", "session_id": "session-alpha"}
    assert plan.workspace_policy == {
        "workspace_ref": "local",
        "artifact_mode": "enabled",
    }


def test_bee_launch_plan_rejects_missing_template(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Bee template not found"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="missing-template",
                workspace_root=tmp_path,
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_rejects_invalid_template(tmp_path: Path) -> None:
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    _write_template(
        tmp_path,
        template_id="template-alpha",
        metadata_extra="metadata:\n  prompt: unsafe\n",
    )

    with pytest.raises(ValueError, match="forbidden sensitive field"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id=template_dir.name,
                workspace_root=tmp_path,
                inputs={"region": "us-test-1"},
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_rejects_missing_required_input(tmp_path: Path) -> None:
    _write_template(tmp_path, template_id="template-alpha")

    with pytest.raises(ValueError, match="missing required Bee launch inputs"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=tmp_path,
                inputs={},
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_binds_default_inputs(tmp_path: Path) -> None:
    _write_template(tmp_path, template_id="template-alpha")

    plan = build_bee_launch_plan(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            requested_at=_dt(9),
        )
    )

    assert plan.input_binding.inputs == {"region": "us-test-1", "severity": "low"}
    assert plan.input_binding.defaulted_input_names == ("severity",)


def test_bee_launch_plan_rejects_unknown_input_by_default(tmp_path: Path) -> None:
    _write_template(tmp_path, template_id="template-alpha")

    with pytest.raises(ValueError, match="unknown Bee launch inputs"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=tmp_path,
                inputs={"region": "us-test-1", "extra": "value"},
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_rejects_missing_workspace(tmp_path: Path) -> None:
    missing_workspace = tmp_path / "missing"

    with pytest.raises(FileNotFoundError, match="Bee launch workspace not found"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=missing_workspace,
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_rejects_symlinked_bee_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside"
    _write_template(outside_root, template_id="template-alpha")
    (workspace_root / ".bee").symlink_to(
        outside_root / ".bee", target_is_directory=True
    )

    with pytest.raises(ValueError, match="Bee launch .bee root must not be a symlink"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=workspace_root,
                inputs={"region": "us-test-1"},
                requested_at=_dt(9),
            )
        )


def test_bee_launch_request_rejects_executable_shaped_input_values() -> None:
    with pytest.raises(ValueError, match="forbidden metadata key"):
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=Path("."),
            inputs={"region": {"cmd": "status"}},
            requested_at=_dt(9),
        )


def test_bee_launch_plan_rejects_executable_shaped_template_defaults(
    tmp_path: Path,
) -> None:
    _write_template(
        tmp_path,
        template_id="template-alpha",
        input_defaults_extra="    script: status\n",
    )

    with pytest.raises(ValueError, match="forbidden executable field"):
        build_bee_launch_plan(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=tmp_path,
                inputs={"region": "us-test-1"},
                requested_at=_dt(9),
            )
        )


def test_bee_launch_plan_accepts_safe_command_intent_names_with_tool_words(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    commands_path = tmp_path / ".bee" / "templates" / "template-alpha" / "commands.yaml"
    commands_path.write_text(
        "\n".join(
            [
                "commands:",
                "  - name: shellcheck",
                "    profile: lint",
                "    policy: local_readonly",
                "    category: validation",
                "  - name: exec-validation",
                "    profile: lint",
                "    policy: local_readonly",
                "    category: validation",
            ]
        ),
        encoding="utf-8",
    )

    plan = build_bee_launch_plan(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            requested_at=_dt(9),
        )
    )

    assert plan.resolution.command_intent_names == (
        "shellcheck",
        "exec-validation",
    )


def test_bee_launch_request_rejects_unsafe_inputs_and_policy() -> None:
    with pytest.raises(ValueError, match="forbidden metadata key"):
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=Path("."),
            inputs={"prompt": "do not store"},
            requested_at=_dt(9),
        )

    with pytest.raises(ValueError, match="forbidden metadata key"):
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=Path("."),
            topic_policy={"mode": "create", "token": "secret"},
            requested_at=_dt(9),
        )


@pytest.mark.asyncio
async def test_manual_bee_launch_creates_topic_task_and_artifact(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    topic_store = FakeTopicStore()
    task_store = FakeBeeTaskStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=FakeClock(),
            topic_id_factory=lambda: "topic-launch",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: f"bee-task-{plan.launch_id}",
    )
    tape = Tape(tape_id="tape-alpha")

    result = await launcher.launch_manual(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            workspace_policy={"artifact_mode": "enabled"},
            requested_at=_dt(9),
        ),
        tape=tape,
        write_workspace_artifacts=True,
    )

    assert result.launch_id == "launch-1"
    assert result.task_id == "bee-task-launch-1"
    assert result.topic_id == "topic-launch"
    assert result.status == "launched"
    assert (await launch_store.load_launch("launch-1")).task_id == result.task_id
    assert task_store.tasks[result.task_id] == BeeTaskRecord(
        task_id=result.task_id,
        topic_id="topic-launch",
        session_id="session-alpha",
        kind="maintenance",
        profile="local",
        status="pending",
        title="Local template",
        summary=None,
        created_at=_dt(9),
        updated_at=_dt(9),
        metadata={
            "template_id": "template-alpha",
            "launch_id": "launch-1",
            "launch_source": "manual",
        },
    )
    assert [node.node_id for node in await task_store.list_nodes(result.task_id)] == [
        "node-plan"
    ]
    task_json = (tmp_path / ".bee" / "runs" / result.task_id / "task.json").read_text(
        encoding="utf-8"
    )
    assert '"task_id": "bee-task-launch-1"' in task_json
    assert '"template_id": "template-alpha"' in task_json
    assert "command_output" not in task_json


@pytest.mark.asyncio
async def test_manual_bee_launch_continues_existing_topic(tmp_path: Path) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    topic_store = FakeTopicStore()
    task_store = FakeBeeTaskStore()
    lifecycle = TopicLifecycle(
        store=topic_store,
        now=FakeClock(),
        topic_id_factory=lambda: "topic-existing",
    )
    tape = Tape(tape_id="tape-alpha")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-alpha",
        kind="maintenance",
        title="Existing topic",
    )
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=lifecycle,
        now=FakeClock(),
        task_id_factory=lambda plan, existing_topic: (
            f"bee-task-{existing_topic.topic_id}"
        ),
    )

    result = await launcher.launch_manual(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            topic_policy={"mode": "continue", "topic_id": topic.topic_id},
            requested_at=_dt(9),
        ),
        tape=tape,
    )

    assert result.topic_id == "topic-existing"
    assert len(topic_store.topics) == 1
    assert result.task_id == "bee-task-topic-existing"


@pytest.mark.asyncio
async def test_manual_bee_launch_missing_template_fails(tmp_path: Path) -> None:
    launcher = BeeLaunchOrchestrator(
        launch_store=PGBeeLaunchStore(pool=FakeBeeLaunchPool()),  # type: ignore[arg-type]
        task_store=FakeBeeTaskStore(),
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
        now=FakeClock(),
    )

    with pytest.raises(FileNotFoundError, match="Bee template not found"):
        await launcher.launch_manual(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="missing-template",
                workspace_root=tmp_path,
                requested_at=_dt(9),
            ),
            tape=Tape(tape_id="tape-alpha"),
        )


@pytest.mark.asyncio
async def test_manual_bee_launch_invalid_input_fails_before_records(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=FakeBeeTaskStore(),
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
        now=FakeClock(),
    )

    with pytest.raises(ValueError, match="unknown Bee launch inputs"):
        await launcher.launch_manual(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=tmp_path,
                inputs={"region": "us-test-1", "extra": "value"},
                requested_at=_dt(9),
            ),
            tape=Tape(tape_id="tape-alpha"),
        )

    assert await launch_store.load_launch("launch-1") is None


@pytest.mark.asyncio
async def test_manual_bee_launch_rejects_artifact_write_without_policy(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=FakeClock(),
            topic_id_factory=lambda: "topic-launch",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: "bee-task-launch",
    )

    with pytest.raises(ValueError, match="workspace artifacts require"):
        await launcher.launch_manual(
            BeeLaunchRequest(
                launch_id="launch-1",
                source="manual",
                template_id="template-alpha",
                workspace_root=tmp_path,
                inputs={"region": "us-test-1"},
                requested_at=_dt(9),
            ),
            tape=Tape(tape_id="tape-alpha"),
            write_workspace_artifacts=True,
        )

    assert not (tmp_path / ".bee" / "runs").exists()
    assert await launch_store.load_launch("launch-1") is None
    assert topic_store.topics == {}
    assert task_store.tasks == {}
    assert task_store.nodes == {}


@pytest.mark.asyncio
async def test_manual_bee_launch_does_not_execute_command_intents(
    tmp_path: Path,
) -> None:
    _write_template_with_commands(tmp_path, template_id="template-alpha")
    task_store = FakeBeeTaskStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=PGBeeLaunchStore(pool=FakeBeeLaunchPool()),  # type: ignore[arg-type]
        task_store=task_store,
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(
            store=FakeTopicStore(),
            now=FakeClock(),
            topic_id_factory=lambda: "topic-launch",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: "bee-task-launch",
    )

    result = await launcher.launch_manual(
        BeeLaunchRequest(
            launch_id="launch-1",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            requested_at=_dt(9),
        ),
        tape=Tape(tape_id="tape-alpha"),
    )

    nodes = await task_store.list_nodes(result.task_id)
    assert [node.status for node in nodes] == ["pending"]
    assert nodes[0].metadata["command_ref"] == "shellcheck"


@pytest.mark.asyncio
async def test_scheduled_bee_launch_creates_task_and_links_schedule(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    trigger_store = FakeScheduleTriggerStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=FakeClock(),
            topic_id_factory=lambda: "topic-scheduled",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: f"bee-task-{plan.launch_id}",
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    result = await scheduled_launcher.launch_due(
        _scheduled_intent(topic_id=None),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
        workspace_policy={"artifact_mode": "enabled"},
        write_workspace_artifacts=True,
    )

    launch = await launch_store.load_launch("launch-trigger-1")
    trigger = trigger_store.triggers["trigger-1"]
    assert result.source == "schedule"
    assert result.task_id == "bee-task-launch-trigger-1"
    assert result.topic_id == "topic-scheduled"
    assert launch is not None
    assert launch.schedule_id == "schedule-1"
    assert launch.task_id == result.task_id
    assert trigger.status == "launched"
    assert trigger.topic_id == result.topic_id
    assert trigger.metadata == {
        "trigger_kind": "schedule",
        "launch_id": result.launch_id,
        "task_id": result.task_id,
        "topic_id": result.topic_id,
        "launch_status": "launched",
    }
    assert (tmp_path / ".bee" / "runs" / result.task_id / "task.json").exists()


@pytest.mark.asyncio
async def test_scheduled_bee_launch_continues_topic_and_keeps_nodes_pending(
    tmp_path: Path,
) -> None:
    _write_template_with_commands(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    lifecycle = TopicLifecycle(
        store=topic_store,
        now=FakeClock(),
        topic_id_factory=lambda: "topic-existing",
    )
    tape = Tape(tape_id="tape-alpha")
    topic = await lifecycle.create_topic(
        tape=tape,
        session_id="session-alpha",
        kind="maintenance",
        title="Existing scheduled topic",
    )
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=lifecycle,
        now=FakeClock(),
        task_id_factory=lambda plan, existing_topic: (
            f"bee-task-{existing_topic.topic_id}"
        ),
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=FakeScheduleTriggerStore(),
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    result = await scheduled_launcher.launch_due(
        _scheduled_intent(topic_id=topic.topic_id),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=tape,
        inputs={"region": "us-test-1"},
    )

    nodes = await task_store.list_nodes(result.task_id)
    assert result.topic_id == topic.topic_id
    assert [node.status for node in nodes] == ["pending"]
    assert nodes[0].metadata["command_ref"] == "shellcheck"
    assert nodes[0].run_id is None


@pytest.mark.asyncio
async def test_scheduled_bee_launch_replay_returns_existing_launch_result(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=FakeClock(),
            topic_id_factory=lambda: "topic-scheduled",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: f"bee-task-{len(task_store.tasks) + 1}",
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=FakeScheduleTriggerStore(),
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )
    intent = _scheduled_intent(topic_id=None)

    first = await scheduled_launcher.launch_due(
        intent,
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )
    replayed = await scheduled_launcher.launch_due(
        intent,
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )

    assert replayed == first
    assert task_store.upserted_task_ids == ["bee-task-1"]
    assert sorted(task_store.tasks) == ["bee-task-1"]


@pytest.mark.asyncio
async def test_scheduled_bee_launch_replay_rejects_in_progress_launch(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    await launch_store.create_launch(
        _launch(
            "launch-trigger-1",
            source="schedule",
            schedule_id="schedule-1",
            status="launching",
        )
    )
    task_store = FakeBeeTaskStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
        now=FakeClock(),
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=FakeScheduleTriggerStore(),
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    with pytest.raises(ValueError, match="already exists"):
        await scheduled_launcher.launch_due(
            _scheduled_intent(topic_id=None),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
        )

    assert task_store.tasks == {}


@pytest.mark.asyncio
async def test_scheduled_bee_launch_rejects_mismatched_topic_policy(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launcher = BeeLaunchOrchestrator(
        launch_store=PGBeeLaunchStore(pool=FakeBeeLaunchPool()),  # type: ignore[arg-type]
        task_store=FakeBeeTaskStore(),
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
        now=FakeClock(),
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=FakeScheduleTriggerStore(),
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    with pytest.raises(ValueError, match="session_id must match intent"):
        await scheduled_launcher.launch_due(
            _scheduled_intent(topic_id="topic-intent"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
            topic_policy={"session_id": "other-session"},
        )
    with pytest.raises(ValueError, match="topic_id must match intent"):
        await scheduled_launcher.launch_due(
            _scheduled_intent(topic_id="topic-intent"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
            topic_policy={"topic_id": "other-topic"},
        )
    with pytest.raises(ValueError, match="mode must match intent"):
        await scheduled_launcher.launch_due(
            _scheduled_intent(topic_id="topic-intent"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
            topic_policy={"mode": "create"},
        )


@pytest.mark.asyncio
async def test_proactive_signal_bee_launch_creates_task_and_links_signal(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    trigger_store = FakeScheduleTriggerStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=FakeClock(),
            topic_id_factory=lambda: "topic-signal",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: f"bee-task-{plan.launch_id}",
    )
    signal_launcher = ProactiveBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    result = await signal_launcher.launch_signal(
        _signal_intent(topic_id=None),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )

    launch = await launch_store.load_launch("launch-signal-trigger-signal-1")
    trigger = trigger_store.triggers["signal-trigger-signal-1"]
    assert result.source == "proactive_signal"
    assert result.task_id == "bee-task-launch-signal-trigger-signal-1"
    assert result.topic_id == "topic-signal"
    assert launch is not None
    assert launch.signal_id == "signal-1"
    assert launch.task_id == result.task_id
    assert trigger.status == "launched"
    assert trigger.signal_id == "signal-1"
    assert trigger.topic_id == result.topic_id
    assert trigger.metadata == {
        "trigger_kind": "proactive_signal",
        "launch_id": result.launch_id,
        "task_id": result.task_id,
        "topic_id": result.topic_id,
        "launch_status": "launched",
    }


@pytest.mark.asyncio
async def test_proactive_signal_bee_launch_replay_and_policy_guards(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(
            store=FakeTopicStore(),
            now=FakeClock(),
            topic_id_factory=lambda: "topic-signal",
        ),
        now=FakeClock(),
        task_id_factory=lambda plan, topic: f"bee-task-{len(task_store.tasks) + 1}",
    )
    signal_launcher = ProactiveBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=FakeScheduleTriggerStore(),
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )
    intent = _signal_intent(topic_id=None)

    first = await signal_launcher.launch_signal(
        intent,
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )
    replayed = await signal_launcher.launch_signal(
        intent,
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )

    assert replayed == first
    assert task_store.upserted_task_ids == ["bee-task-1"]

    with pytest.raises(ValueError, match="session_id must match intent"):
        await signal_launcher.launch_signal(
            _signal_intent(topic_id="topic-intent"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
            topic_policy={"session_id": "other-session"},
        )
    with pytest.raises(ValueError, match="mode must match intent"):
        await signal_launcher.launch_signal(
            _signal_intent(topic_id="topic-intent"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
            topic_policy={"mode": "create"},
        )


@pytest.mark.asyncio
async def test_proactive_signal_bee_launch_replay_repairs_trigger_link(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    await launch_store.create_launch(
        BeeLaunchRecord(
            launch_id="launch-signal-trigger-signal-1",
            source="proactive_signal",
            template_id="template-alpha",
            status="launched",
            requested_at=_dt(9),
            task_id="bee-task-existing",
            topic_id="topic-signal",
            session_id="session-alpha",
            signal_id="signal-1",
            launched_at=_dt(9),
            metadata={"phase": "task_created"},
        )
    )
    trigger_store = FakeScheduleTriggerStore()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=FakeBeeTaskStore(),
        topic_store=FakeTopicStore(),
        topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
        now=FakeClock(),
    )
    signal_launcher = ProactiveBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    result = await signal_launcher.launch_signal(
        _signal_intent(topic_id="topic-signal"),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=Tape(tape_id="tape-alpha"),
        inputs={"region": "us-test-1"},
    )

    trigger = trigger_store.triggers["signal-trigger-signal-1"]
    assert result.task_id == "bee-task-existing"
    assert trigger.status == "launched"
    assert trigger.signal_id == "signal-1"
    assert trigger.metadata["task_id"] == "bee-task-existing"


@pytest.mark.asyncio
async def test_proactive_signal_bee_launch_rejects_existing_launch_mismatch(
    tmp_path: Path,
) -> None:
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    await launch_store.create_launch(
        BeeLaunchRecord(
            launch_id="launch-signal-trigger-signal-1",
            source="manual",
            template_id="template-alpha",
            status="launched",
            requested_at=_dt(9),
            task_id="bee-task-existing",
            topic_id="topic-signal",
            session_id="session-alpha",
            signal_id="signal-1",
            launched_at=_dt(9),
        )
    )
    trigger_store = FakeScheduleTriggerStore()
    signal_launcher = ProactiveBeeLaunchOrchestrator(
        launcher=BeeLaunchOrchestrator(
            launch_store=launch_store,
            task_store=FakeBeeTaskStore(),
            topic_store=FakeTopicStore(),
            topic_lifecycle=TopicLifecycle(store=FakeTopicStore()),
            now=FakeClock(),
        ),
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )

    with pytest.raises(ValueError, match="source mismatch"):
        await signal_launcher.launch_signal(
            _signal_intent(topic_id="topic-signal"),
            template_id="template-alpha",
            workspace_root=tmp_path,
            tape=Tape(tape_id="tape-alpha"),
            inputs={"region": "us-test-1"},
        )

    assert trigger_store.triggers == {}


@pytest.mark.asyncio
async def test_bee_launch_e2e_smoke(tmp_path: Path) -> None:
    reset_prometheus_metrics()
    _write_template(tmp_path, template_id="template-alpha")
    launch_store = PGBeeLaunchStore(pool=FakeBeeLaunchPool())  # type: ignore[arg-type]
    task_store = FakeBeeTaskStore()
    topic_store = FakeTopicStore()
    trigger_store = FakeScheduleTriggerStore()
    clock = FakeClock()
    launcher = BeeLaunchOrchestrator(
        launch_store=launch_store,
        task_store=task_store,
        topic_store=topic_store,
        topic_lifecycle=TopicLifecycle(
            store=topic_store,
            now=clock,
            topic_id_factory=lambda: f"topic-{len(topic_store.topics) + 1}",
        ),
        now=clock,
        task_id_factory=lambda plan, topic: f"bee-task-{plan.launch_id}",
    )
    scheduled_launcher = ScheduledBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )
    signal_launcher = ProactiveBeeLaunchOrchestrator(
        launcher=launcher,
        trigger_store=trigger_store,
        launch_id_factory=lambda intent: f"launch-{intent.trigger_id}",
    )
    tape = Tape(tape_id="tape-alpha")

    manual = await launcher.launch_manual(
        BeeLaunchRequest(
            launch_id="launch-manual",
            source="manual",
            template_id="template-alpha",
            workspace_root=tmp_path,
            inputs={"region": "us-test-1"},
            workspace_policy={"artifact_mode": "enabled"},
            requested_at=_dt(9),
        ),
        tape=tape,
        write_workspace_artifacts=True,
    )
    scheduled = await scheduled_launcher.launch_due(
        _scheduled_intent(topic_id=None),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=tape,
        inputs={"region": "us-test-1"},
    )
    proactive = await signal_launcher.launch_signal(
        _signal_intent(topic_id=None),
        template_id="template-alpha",
        workspace_root=tmp_path,
        tape=tape,
        inputs={"region": "us-test-1"},
    )

    launches = await launch_store.list_launches()
    assert {launch.source for launch in launches} == {
        "manual",
        "schedule",
        "proactive_signal",
    }
    assert all(launch.status == "launched" for launch in launches)
    assert manual.task_id in task_store.tasks
    assert scheduled.task_id in task_store.tasks
    assert proactive.task_id in task_store.tasks
    assert trigger_store.triggers["trigger-1"].metadata["launch_id"] == (
        scheduled.launch_id
    )
    assert trigger_store.triggers["signal-trigger-signal-1"].metadata["launch_id"] == (
        proactive.launch_id
    )
    assert (tmp_path / ".bee" / "runs" / manual.task_id / "task.json").exists()
    for task_id in (manual.task_id, scheduled.task_id, proactive.task_id):
        nodes = await task_store.list_nodes(task_id)
        assert [node.status for node in nodes] == ["pending"]
    metrics = prometheus_metrics_text()
    assert 'bee_launches_total{source="manual",status="launched"} 1' in metrics
    assert 'bee_launches_total{source="schedule",status="launched"} 1' in metrics
    assert (
        'bee_launches_total{source="proactive_signal",status="launched"} 1' in metrics
    )
    assert 'scheduled_bee_launches_total{status="launched"} 1' in metrics
    assert 'proactive_bee_launches_total{kind="unknown",status="launched"} 1' in metrics
    for forbidden in (
        "launch_id",
        "task_id",
        "topic_id",
        "run_id",
        "session_id",
        "schedule_id",
        "signal_id",
        "node_id",
    ):
        assert forbidden not in metrics
    reset_prometheus_metrics()


@pytest.mark.asyncio
async def test_bee_task_lifecycle_resume_partially_completed_task() -> None:
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    result = await controller.resume_task("bee-task-alpha")

    assert result.status == "running"
    nodes = {node.node_id: node for node in await store.list_nodes("bee-task-alpha")}
    assert nodes["node-plan"].status == "completed"
    assert nodes["node-validate"].status == "pending"
    assert nodes["node-validate"].run_id is None
    assert nodes["node-validate"].metadata["resume_reason"] == "manual_resume"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_resume_does_not_requeue_ready_node() -> None:
    store = _lifecycle_task_store()
    ready = store.nodes[("bee-task-alpha", "node-validate")]
    store.nodes[("bee-task-alpha", "node-validate")] = replace(
        ready,
        status="ready",
        run_id="run-ready",
    )
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    await controller.resume_task("bee-task-alpha")

    node = store.nodes[("bee-task-alpha", "node-validate")]
    assert node.status == "ready"
    assert node.run_id == "run-ready"
    assert "resume_reason" not in node.metadata


@pytest.mark.asyncio
async def test_bee_task_lifecycle_duplicate_resume_does_not_duplicate_attempts() -> (
    None
):
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    await controller.resume_task("bee-task-alpha")
    await controller.resume_task("bee-task-alpha")

    nodes = {node.node_id: node for node in await store.list_nodes("bee-task-alpha")}
    assert nodes["node-plan"].metadata["attempt_count"] == 1
    assert nodes["node-validate"].metadata["attempt_count"] == 2


@pytest.mark.asyncio
async def test_bee_task_lifecycle_retry_failed_node() -> None:
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    retried = await controller.retry_node(
        task_id="bee-task-alpha",
        node_id="node-validate",
    )

    assert retried.status == "pending"
    assert retried.run_id is None
    assert retried.started_at is None
    assert retried.finished_at is None
    assert retried.metadata["attempt_count"] == 3
    assert retried.metadata["previous_status"] == "failed"
    assert retried.metadata["evidence_ref"] == "evidence-node-validate"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_retry_completed_node_rejected() -> None:
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    with pytest.raises(ValueError, match="only failed Bee nodes can be retried"):
        await controller.retry_node(
            task_id="bee-task-alpha",
            node_id="node-plan",
        )


@pytest.mark.asyncio
async def test_bee_task_lifecycle_cancel_task() -> None:
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    cancelled = await controller.cancel_task("bee-task-alpha")

    assert cancelled.status == "cancelled"
    nodes = {node.node_id: node for node in await store.list_nodes("bee-task-alpha")}
    assert nodes["node-plan"].status == "completed"
    assert nodes["node-validate"].status == "skipped"
    assert nodes["node-validate"].run_id is None
    assert nodes["node-validate"].started_at is None
    assert nodes["node-validate"].finished_at == _dt(9)
    assert nodes["node-validate"].metadata["terminal_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_cancel_cleans_stale_skipped_run_linkage() -> None:
    store = _lifecycle_task_store()
    skipped = store.nodes[("bee-task-alpha", "node-validate")]
    store.nodes[("bee-task-alpha", "node-validate")] = replace(
        skipped,
        status="skipped",
        run_id="run-stale",
        started_at=_dt(8),
        finished_at=None,
        metadata={"attempt_count": 2},
    )
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    await controller.cancel_task("bee-task-alpha")

    node = store.nodes[("bee-task-alpha", "node-validate")]
    assert node.status == "skipped"
    assert node.run_id is None
    assert node.started_at is None
    assert node.finished_at == _dt(9)
    assert node.metadata["terminal_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_abort_partial_anchor_context_rejected_before_close() -> (
    None
):
    store = _lifecycle_task_store()
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    with pytest.raises(ValueError, match="abort anchor requires"):
        await controller.abort_task("bee-task-alpha", tape=Tape(tape_id="tape-alpha"))

    assert store.tasks["bee-task-alpha"].status == "running"
    assert store.nodes[("bee-task-alpha", "node-validate")].status == "failed"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_abort_anchor_failure_rejected_before_close() -> None:
    store = _lifecycle_task_store()
    topic = TopicRecord(
        topic_id="topic-alpha",
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="maintenance",
        status="open",
        title="Topic alpha",
        summary=None,
        owner=None,
        topic_initial_seq=0,
        topic_finalized_seq=None,
        created_at=_dt(8),
    )
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    with pytest.raises(RuntimeError, match="anchor unavailable"):
        await controller.abort_task(
            "bee-task-alpha",
            tape=Tape(tape_id="tape-alpha"),
            topic=topic,
            task_lifecycle=FailingBeeTaskLifecycle(),  # type: ignore[arg-type]
        )

    assert store.tasks["bee-task-alpha"].status == "running"
    assert store.nodes[("bee-task-alpha", "node-validate")].status == "failed"
    assert store.nodes[("bee-task-alpha", "node-validate")].run_id == "run-validate"


@pytest.mark.asyncio
async def test_bee_task_lifecycle_abort_task_writes_anchor() -> None:
    store = _lifecycle_task_store()
    anchor_store = FakeTopicStore()
    topic = TopicRecord(
        topic_id="topic-alpha",
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="maintenance",
        status="open",
        title="Topic alpha",
        summary=None,
        owner=None,
        topic_initial_seq=0,
        topic_finalized_seq=None,
        created_at=_dt(8),
    )
    anchor_store.topics[topic.topic_id] = topic
    tape = Tape(tape_id="tape-alpha")
    controller = BeeTaskLifecycleController(store=store, now=FakeClock())

    aborted = await controller.abort_task(
        "bee-task-alpha",
        tape=tape,
        topic=topic,
        task_lifecycle=BeeTaskLifecycle(anchor_store=anchor_store),
    )

    assert aborted.status == "cancelled"
    assert anchor_store.anchors[-1].anchor_type == "bee_task_aborted"
    assert tape.snapshot()[-1].meta["product_anchor_type"] == "bee_task_aborted"


class FailingBeeTaskLifecycle:
    async def finalize_task(self, **_kwargs: object) -> None:
        raise RuntimeError("anchor unavailable")


def _launch(
    launch_id: str = "launch-1",
    *,
    source: str = "manual",
    status: str = "planned",
    schedule_id: str | None = None,
    signal_id: str | None = None,
    metadata: dict[str, object] | None = None,
    error_message: str | None = None,
) -> BeeLaunchRecord:
    return BeeLaunchRecord(
        launch_id=launch_id,
        source=source,
        template_id="template-1",
        status=status,
        requested_at=_dt(9),
        task_id=None,
        topic_id=None,
        session_id=None,
        workspace_ref="workspace-local",
        schedule_id=schedule_id,
        signal_id=signal_id,
        launched_at=None,
        finished_at=None,
        error_type=None,
        error_message=error_message,
        metadata={} if metadata is None else metadata,
    )


def _launch_row(*args: object) -> dict[str, object]:
    (
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
        metadata,
    ) = args
    return {
        "launch_id": launch_id,
        "source": source,
        "template_id": template_id,
        "task_id": task_id,
        "topic_id": topic_id,
        "session_id": session_id,
        "workspace_ref": workspace_ref,
        "schedule_id": schedule_id,
        "signal_id": signal_id,
        "status": status,
        "requested_at": requested_at,
        "launched_at": launched_at,
        "finished_at": finished_at,
        "error_type": error_type,
        "error_message": error_message,
        "metadata": metadata,
    }


def _lifecycle_task_store() -> FakeBeeTaskStore:
    store = FakeBeeTaskStore()
    now = _dt(9)
    task = BeeTaskRecord(
        task_id="bee-task-alpha",
        topic_id="topic-alpha",
        session_id="session-alpha",
        kind="maintenance",
        profile="local",
        status="running",
        title="Lifecycle task",
        summary=None,
        created_at=now,
        updated_at=now,
        metadata={"launch_source": "manual"},
    )
    store.tasks[task.task_id] = task
    store.nodes[(task.task_id, "node-plan")] = BeeNodeRecord(
        node_id="node-plan",
        task_id=task.task_id,
        kind="analysis",
        profile="default",
        status="completed",
        title="Plan",
        created_at=now,
        updated_at=now,
        finished_at=now,
        metadata={"attempt_count": 1, "evidence_ref": "evidence-node-plan"},
    )
    store.nodes[(task.task_id, "node-validate")] = BeeNodeRecord(
        node_id="node-validate",
        task_id=task.task_id,
        kind="validation",
        profile="default",
        status="failed",
        title="Validate",
        depends_on=("node-plan",),
        run_id="run-validate",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
        metadata={"attempt_count": 2, "evidence_ref": "evidence-node-validate"},
    )
    return store


def _scheduled_intent(topic_id: str | None) -> ScheduledLaunchIntent:
    return ScheduledLaunchIntent(
        trigger_id="trigger-1",
        schedule_id="schedule-1",
        session_id="session-alpha",
        topic_id=topic_id,
        reason="schedule_due",
        due_at=_dt(8),
        planned_at=_dt(9),
        metadata={"schedule_kind": "interval"},
    )


def _signal_intent(topic_id: str | None) -> ScheduledLaunchIntent:
    return ScheduledLaunchIntent(
        trigger_id="signal-trigger-signal-1",
        schedule_id="signal:signal-1",
        session_id="session-alpha",
        topic_id=topic_id,
        reason="proactive_signal",
        due_at=_dt(8),
        planned_at=_dt(9),
        metadata={"signal_kind": "repo_activity"},
    )


def _dt(hour: int) -> datetime:
    return datetime(2026, 1, 2, hour, tzinfo=UTC)


def _write_template(
    workspace_root: Path,
    *,
    template_id: str,
    metadata_extra: str = "",
    input_defaults_extra: str = "",
) -> None:
    template_dir = workspace_root / ".bee" / "templates" / template_id
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                f"template_id: {template_id}",
                "kind: maintenance",
                "profile: local",
                "title: Local template",
                "topic:",
                "  session_id: session-alpha",
                "inputs:",
                "  required:",
                "    - region",
                "  defaults:",
                "    severity: low",
                input_defaults_extra.rstrip(),
                "nodes:",
                "  - node_id: node-plan",
                "    kind: analysis",
                "    profile: default",
                "    title: Plan local task",
                metadata_extra,
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )


def _write_template_with_commands(workspace_root: Path, *, template_id: str) -> None:
    template_dir = workspace_root / ".bee" / "templates" / template_id
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                f"template_id: {template_id}",
                "kind: maintenance",
                "profile: local",
                "title: Local template",
                "topic:",
                "  session_id: session-alpha",
                "inputs:",
                "  required:",
                "    - region",
                "nodes:",
                "  - node_id: node-plan",
                "    kind: validation",
                "    profile: default",
                "    title: Validate local task",
                "    command_ref: shellcheck",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join(
            [
                "commands:",
                "  - name: shellcheck",
                "    profile: lint",
                "    policy: local_readonly",
                "    category: validation",
            ]
        ),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n", encoding="utf-8"
    )
