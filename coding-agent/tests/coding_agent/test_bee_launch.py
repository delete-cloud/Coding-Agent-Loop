from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from coding_agent.bee_launch import (
    BeeInputBinding,
    BeeLaunchRecord,
    BeeLaunchRequest,
    BeeTemplateResolution,
    PGBeeLaunchStore,
    build_bee_launch_plan,
)


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
