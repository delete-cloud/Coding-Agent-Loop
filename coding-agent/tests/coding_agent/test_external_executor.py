from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from coding_agent.bee_command_bridge import (
    complete_bee_node_from_bridge_result,
    plan_bee_command_intent,
)
from coding_agent.bee_workspace import (
    build_bee_manifest_from_workspace_template,
    load_bee_workspace_template,
)
from coding_agent.external_executor import (
    DockerExecutorAdapter,
    ExecutorCapability,
    ExecutorEvidence,
    ExecutorPlan,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorRunRecord,
    LocalExecutorAdapter,
    PGExecutorRunStore,
    build_docker_executor_plan_from_local_plan,
    build_local_executor_plan_from_bee_command_plan,
    executor_result_completion_evidence,
)


class FakeExecutorRunPool:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def get_pool(self) -> FakeExecutorRunPool:
        return self

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "CREATE TABLE IF NOT EXISTS executor_runs" in query:
            return "CREATE TABLE"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "INSERT INTO executor_runs" in query:
            row = _run_row(*args)
            if cast(str, row["executor_run_id"]) in self.runs:
                return None
            self.runs[cast(str, row["executor_run_id"])] = row
            return row
        if "SELECT * FROM executor_runs WHERE executor_run_id = $1" in query:
            return self.runs.get(cast(str, args[0]))
        if "UPDATE executor_runs" in query and "sanitized_summary = $6" in query:
            return self._attach_result(args)
        if "UPDATE executor_runs" in query:
            return self._update_status(args)
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT * FROM executor_runs" not in query:
            raise AssertionError(f"unexpected fetch query: {query}")
        task_id, node_id, executor_kind, status, limit = args
        rows = [
            row
            for row in self.runs.values()
            if (task_id is None or row["task_id"] == task_id)
            and (node_id is None or row["node_id"] == node_id)
            and (executor_kind is None or row["executor_kind"] == executor_kind)
            and (status is None or row["status"] == status)
        ]
        rows.sort(
            key=lambda row: (
                cast(datetime, row["requested_at"]),
                row["executor_run_id"],
            )
        )
        return rows[: cast(int, limit)]

    async def close(self) -> None:
        return None

    async def acquire(self) -> FakeExecutorRunPool:
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")

    def _update_status(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            executor_run_id,
            status,
            submitted_at,
            started_at,
            finished_at,
            error_type,
            error_message,
            metadata,
        ) = args
        row = self.runs.get(cast(str, executor_run_id))
        if row is None:
            return None
        row.update({
            "status": status,
            "submitted_at": submitted_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "error_type": error_type,
            "error_message": error_message,
            "metadata": metadata,
        })
        return row

    def _attach_result(self, args: tuple[object, ...]) -> dict[str, object] | None:
        (
            executor_run_id,
            status,
            finished_at,
            error_type,
            error_message,
            sanitized_summary,
            evidence,
            metadata,
        ) = args
        row = self.runs.get(cast(str, executor_run_id))
        if row is None:
            return None
        row.update({
            "status": status,
            "finished_at": finished_at,
            "error_type": error_type,
            "error_message": error_message,
            "sanitized_summary": sanitized_summary,
            "evidence": evidence,
            "metadata": metadata,
        })
        return row


class FakeExecutor:
    kind = "fixture"

    def capability(self) -> ExecutorCapability:
        return ExecutorCapability(
            executor_kind="fixture",
            enabled=True,
            available=True,
            status="available",
            reason="fixture executor registered",
        )

    async def submit(self, plan: ExecutorPlan) -> ExecutorResult:
        return ExecutorResult(
            status="succeeded",
            sanitized_summary=f"{plan.executor_kind} plan accepted",
        )


class FakeDockerCapabilityClient:
    def __init__(self, available: bool) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available


@pytest.fixture
def fake_pool() -> FakeExecutorRunPool:
    return FakeExecutorRunPool()


@pytest.fixture
def store(fake_pool: FakeExecutorRunPool) -> PGExecutorRunStore:
    return PGExecutorRunStore(pool=fake_pool)  # type: ignore[arg-type]


def test_executor_registry_resolves_known_and_rejects_unknown_kind() -> None:
    registry = ExecutorRegistry()
    executor = FakeExecutor()

    registry.register(executor)

    assert registry.resolve("fixture") is executor
    assert registry.list_kinds() == ("fixture",)
    with pytest.raises(KeyError, match="executor not registered"):
        registry.resolve("local")
    with pytest.raises(ValueError, match="executor already registered"):
        registry.register(executor)
    with pytest.raises(ValueError, match="executor kind"):
        registry.resolve("unknown")


@pytest.mark.asyncio
async def test_executor_run_store_schema_is_idempotent(
    store: PGExecutorRunStore,
    fake_pool: FakeExecutorRunPool,
) -> None:
    await store.create_executor_run(_run())
    await store.load_executor_run("executor-run-1")
    await store.list_executor_runs(task_id="task-1")

    schema_calls = [
        query
        for query, _args in fake_pool.executed
        if "CREATE TABLE IF NOT EXISTS executor_runs" in query
    ]

    assert len(schema_calls) == 1
    assert "executor_runs_task_node_idx" in schema_calls[0]
    assert "executor_runs_kind_status_idx" in schema_calls[0]
    assert "bee_tasks" not in schema_calls[0]


@pytest.mark.asyncio
async def test_executor_run_store_create_update_attach_and_list(
    store: PGExecutorRunStore,
) -> None:
    created = await store.create_executor_run(_run())
    other = await store.create_executor_run(
        _run("executor-run-2", task_id="task-1", node_id="node-2")
    )
    updated = await store.update_executor_run_status(
        "executor-run-1",
        status="running",
        submitted_at=_dt(10),
        started_at=_dt(11),
        finished_at=None,
        error_type=None,
        error_message=None,
        metadata={"phase": "running"},
    )
    attached = await store.attach_executor_result(
        "executor-run-1",
        result=ExecutorResult(
            status="succeeded",
            sanitized_summary="Validation passed with sanitized evidence",
            finished_at=_dt(12),
            evidence=(
                ExecutorEvidence(
                    evidence_kind="validation_report",
                    evidence_ref="evidence/validation-report.md",
                    summary="Sanitized validation report",
                    metadata={"profile": "fixture"},
                ),
            ),
            metadata={"phase": "result_attached"},
        ),
    )
    loaded = await store.load_executor_run("executor-run-1")
    by_task_node = await store.list_executor_runs(task_id="task-1", node_id="node-1")
    by_kind_status = await store.list_executor_runs(
        executor_kind="fixture",
        status="planned",
    )

    assert created.executor_run_id == "executor-run-1"
    assert updated.status == "running"
    assert updated.started_at == _dt(11)
    assert attached.status == "succeeded"
    assert attached.finished_at == _dt(12)
    assert attached.sanitized_summary == "Validation passed with sanitized evidence"
    assert attached.evidence[0].evidence_ref == "evidence/validation-report.md"
    assert loaded == attached
    assert by_task_node == [attached]
    assert by_kind_status == [other]


@pytest.mark.asyncio
async def test_executor_run_store_duplicate_create_does_not_rewrite_attempt(
    store: PGExecutorRunStore,
) -> None:
    created = await store.create_executor_run(_run())

    with pytest.raises(ValueError, match="executor run already exists"):
        await store.create_executor_run(
            _run(
                task_id="different-task",
                node_id="different-node",
                status="running",
            )
        )

    assert await store.load_executor_run("executor-run-1") == created


def test_executor_records_reject_sensitive_metadata_and_raw_summaries() -> None:
    with pytest.raises(ValueError, match="executor kind"):
        _run(executor_kind="ssh")

    with pytest.raises(ValueError, match="forbidden metadata key"):
        _run(metadata={"stdout": "do not store"})

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorResult(
            status="failed",
            sanitized_summary="token=secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorResult(
            status="failed",
            error_message="sk-test-secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/report.md",
            summary="sk-test-secret leaked",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        _run(metadata={"phase": "sk-test-secret leaked"})

    with pytest.raises(ValueError, match="forbidden metadata key"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/report.md",
            metadata={"raw_log": "unsafe"},
        )

    with pytest.raises(ValueError, match="sensitive reference text"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="pods/raw-pod-name",
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        ExecutorEvidence(
            evidence_kind="artifact",
            evidence_ref="evidence/token=secret.md",
        )


@pytest.mark.asyncio
async def test_executor_run_store_missing_rows_fail_fast(
    store: PGExecutorRunStore,
) -> None:
    with pytest.raises(KeyError, match="executor run not found"):
        await store.update_executor_run_status(
            "missing-run",
            status="failed",
            submitted_at=None,
            started_at=None,
            finished_at=_dt(12),
            error_type="not_found",
            error_message="Executor record missing",
            metadata={"phase": "test"},
        )

    with pytest.raises(KeyError, match="executor run not found"):
        await store.attach_executor_result(
            "missing-run",
            result=ExecutorResult(
                status="failed",
                sanitized_summary="Executor run missing",
                finished_at=_dt(12),
            ),
        )


@pytest.mark.asyncio
async def test_local_executor_runs_approved_plan_and_records_sanitized_result(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    executor_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(9),
        executor_run_id="executor-run-local",
        launch_id="launch-1",
        topic_id="topic-1",
    )
    seen_plans: list[ExecutorPlan] = []

    def runner(plan: ExecutorPlan, workspace_root: Path) -> ExecutorResult:
        seen_plans.append(plan)
        assert workspace_root == workspace.resolve()
        return ExecutorResult(
            status="succeeded",
            sanitized_summary="Local validation passed",
            finished_at=_dt(12),
            evidence=(
                ExecutorEvidence(
                    evidence_kind="validation_report",
                    evidence_ref="evidence/validation-report.md",
                    summary="Sanitized validation report",
                ),
            ),
            metadata={"phase": "local_runner_complete"},
        )

    result = await LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda workspace_ref: (
            workspace if workspace_ref == "workspace-alpha" else None
        ),
        now=_clock(10, 11, 12),
    ).submit(executor_plan)
    record = await store.load_executor_run("executor-run-local")
    completion = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        evidence=executor_result_completion_evidence(result),
    )

    assert seen_plans == [executor_plan]
    assert result.status == "succeeded"
    assert record is not None
    assert record.status == "succeeded"
    assert record.sanitized_summary == "Local executor succeeded"
    assert record.evidence[0].evidence_ref == "evidence/validation-report.md"
    assert record.evidence[0].summary is None
    assert record.metadata == {"phase": "local_runner_result"}
    assert completion.status == "completed"
    assert completion.will_complete is True


@pytest.mark.asyncio
async def test_local_executor_records_failed_result_without_completing_node(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    executor_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(9),
        executor_run_id="executor-run-failed",
    )

    async def runner(_plan: ExecutorPlan, workspace_root: Path) -> ExecutorResult:
        assert workspace_root == workspace.resolve()
        return ExecutorResult(
            status="failed",
            sanitized_summary="Local validation failed",
            finished_at=_dt(12),
            evidence=(
                ExecutorEvidence(
                    evidence_kind="validation_report",
                    evidence_ref="evidence/validation-report.md",
                    summary="Sanitized validation report",
                ),
            ),
            metadata={"phase": "local_runner_complete"},
        )

    result = await LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda _workspace_ref: workspace,
        now=_clock(10, 11, 12),
    ).submit(executor_plan)
    record = await store.load_executor_run("executor-run-failed")
    completion = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        evidence=executor_result_completion_evidence(result),
    )

    assert result.status == "failed"
    assert record is not None
    assert record.status == "failed"
    assert record.sanitized_summary == "Local executor failed"
    assert record.error_message == "local executor returned failed status"
    assert completion.status == "evidence_failed"
    assert completion.will_complete is False


def test_local_executor_rejects_denied_or_approval_required_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    denied = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="echo hello && rm -rf /",
        workspace_root=workspace,
        cwd=workspace,
    )
    approval_required = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="rm -rf build",
        workspace_root=workspace,
        cwd=workspace,
    )

    with pytest.raises(ValueError, match="ready Bee command plan"):
        build_local_executor_plan_from_bee_command_plan(
            command_plan=denied,
            task_id="task-1",
            workspace_ref="workspace-alpha",
            approved_workspace_root=workspace,
            requested_at=_dt(9),
        )
    with pytest.raises(ValueError, match="ready Bee command plan"):
        build_local_executor_plan_from_bee_command_plan(
            command_plan=approval_required,
            task_id="task-1",
            workspace_ref="workspace-alpha",
            approved_workspace_root=workspace,
            requested_at=_dt(9),
        )


def test_local_executor_rejects_forged_ready_allow_command_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    forged = replace(command_plan, authorization_token=None)

    with pytest.raises(ValueError, match="authorized Bee command plan"):
        build_local_executor_plan_from_bee_command_plan(
            command_plan=forged,
            task_id="task-1",
            workspace_ref="workspace-alpha",
            approved_workspace_root=workspace,
            requested_at=_dt(9),
        )
    forged_resolution = replace(
        command_plan.resolution,
        template_id="template-forged",
        node_id="node-forged",
    )
    forged_with_token = replace(command_plan, resolution=forged_resolution)

    with pytest.raises(ValueError, match="authorized Bee command plan"):
        build_local_executor_plan_from_bee_command_plan(
            command_plan=forged_with_token,
            task_id="task-1",
            workspace_ref="workspace-alpha",
            approved_workspace_root=workspace,
            requested_at=_dt(9),
        )


@pytest.mark.asyncio
async def test_local_executor_rejects_forged_executor_plan_replace(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    approved_workspace = tmp_path / "approved"
    forged_workspace = tmp_path / "forged"
    approved_workspace.mkdir()
    forged_workspace.mkdir()
    template = _write_template_with_commands(approved_workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=approved_workspace,
        cwd=approved_workspace,
    )
    executor_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="approved-workspace",
        approved_workspace_root=approved_workspace,
        requested_at=_dt(9),
        executor_run_id="executor-run-signed",
    )
    forged_plan = replace(
        executor_plan,
        workspace_ref="forged-workspace",
        approved_workspace_hash=executor_plan.approved_workspace_hash,
    )
    calls: list[ExecutorPlan] = []

    def runner(plan: ExecutorPlan, _workspace_root: Path) -> ExecutorResult:
        calls.append(plan)
        return ExecutorResult(status="succeeded")

    with pytest.raises(ValueError, match="signed executor plan"):
        await LocalExecutorAdapter(
            store=store,
            runner=runner,
            workspace_resolver=lambda workspace_ref: (
                forged_workspace
                if workspace_ref == "forged-workspace"
                else approved_workspace
            ),
            now=_clock(10),
        ).submit(forged_plan)

    assert calls == []
    assert await store.list_executor_runs() == []


@pytest.mark.asyncio
async def test_local_executor_rejects_unauthorized_or_missing_workspace_plan(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    calls: list[ExecutorPlan] = []

    workspace = tmp_path / "repo"
    workspace.mkdir()

    def runner(plan: ExecutorPlan, _workspace_root: Path) -> ExecutorResult:
        calls.append(plan)
        return ExecutorResult(status="succeeded")

    adapter = LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda _workspace_ref: workspace,
        now=_clock(10),
    )
    unauthorized = ExecutorPlan(
        executor_kind="local",
        executor_run_id="executor-run-unauthorized",
        task_id="task-1",
        node_id="node-1",
        workspace_ref="workspace-alpha",
        requested_at=_dt(9),
        metadata={"policy_decision": "deny", "approval_route": "deny"},
    )
    wrong_kind = ExecutorPlan(
        executor_kind="fixture",
        task_id="task-1",
        node_id="node-1",
        workspace_ref="workspace-alpha",
        requested_at=_dt(9),
        metadata={"policy_decision": "allow", "approval_route": "allow"},
    )
    missing_workspace = ExecutorPlan(
        executor_kind="local",
        executor_run_id="executor-run-missing-workspace",
        task_id="task-1",
        node_id="node-1",
        workspace_ref="workspace-alpha",
        requested_at=_dt(9),
        metadata={"policy_decision": "allow", "approval_route": "allow"},
    )

    with pytest.raises(ValueError, match="authorized executor plan"):
        await adapter.submit(unauthorized)
    with pytest.raises(ValueError, match="executor_kind='local'"):
        await adapter.submit(wrong_kind)
    with pytest.raises(ValueError, match="authorized executor plan"):
        await adapter.submit(missing_workspace)

    assert calls == []
    assert await store.list_executor_runs() == []


@pytest.mark.asyncio
async def test_local_executor_rejects_missing_or_mismatched_workspace_binding(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    approved_workspace = tmp_path / "approved"
    mismatched_workspace = tmp_path / "mismatched"
    approved_workspace.mkdir()
    mismatched_workspace.mkdir()
    template = _write_template_with_commands(approved_workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=approved_workspace,
        cwd=approved_workspace,
    )
    executor_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="workspace-alpha",
        approved_workspace_root=approved_workspace,
        requested_at=_dt(9),
        executor_run_id="executor-run-bound",
    )
    calls: list[ExecutorPlan] = []

    def runner(plan: ExecutorPlan, _workspace_root: Path) -> ExecutorResult:
        calls.append(plan)
        return ExecutorResult(status="succeeded")

    missing_adapter = LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda _workspace_ref: None,
        now=_clock(10),
    )
    mismatched_adapter = LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda _workspace_ref: mismatched_workspace,
        now=_clock(10),
    )

    with pytest.raises(ValueError, match="workspace not found"):
        await missing_adapter.submit(executor_plan)
    with pytest.raises(ValueError, match="workspace binding mismatch"):
        await mismatched_adapter.submit(executor_plan)

    assert calls == []
    assert await store.list_executor_runs() == []


@pytest.mark.asyncio
async def test_local_executor_rejects_runner_raw_evidence_ref_before_storage(
    store: PGExecutorRunStore,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    executor_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(9),
        executor_run_id="executor-run-raw-text",
    )

    def runner(_plan: ExecutorPlan, _workspace_root: Path) -> ExecutorResult:
        return ExecutorResult(
            status="succeeded",
            sanitized_summary="uv run pytest tests/coding_agent/test_external_executor.py -v",
            evidence=(
                ExecutorEvidence(
                    evidence_kind="validation_report",
                    evidence_ref="stdout: pytest failed",
                    summary="Traceback most recent call last",
                    metadata={"detail": "uv run pytest tests"},
                ),
            ),
            metadata={"detail": "Traceback most recent call last"},
        )

    with pytest.raises(ValueError, match="whitespace"):
        await LocalExecutorAdapter(
            store=store,
            runner=runner,
            workspace_resolver=lambda _workspace_ref: workspace,
            now=_clock(10),
        ).submit(executor_plan)

    record = await store.load_executor_run("executor-run-raw-text")
    assert record is not None
    assert record.status == "running"
    assert record.sanitized_summary is None
    assert record.evidence == ()
    serialized = str(record)
    assert "uv run pytest" not in serialized
    assert "Traceback" not in serialized
    assert "stdout:" not in serialized


def test_executor_evidence_rejects_traceback_summary() -> None:
    with pytest.raises(ValueError, match="raw execution text"):
        ExecutorEvidence(
            evidence_kind="validation_report",
            evidence_ref="evidence/validation-report.md",
            summary="Traceback most recent call last",
        )


def test_executor_evidence_rejects_raw_command_ref() -> None:
    with pytest.raises(ValueError, match="whitespace"):
        ExecutorEvidence(
            evidence_kind="validation_report",
            evidence_ref="uv run pytest tests/coding_agent/test_external_executor.py -v",
        )


def test_docker_executor_capability_detection_and_dry_run(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    local_plan = _approved_local_plan(workspace)
    docker_plan = build_docker_executor_plan_from_local_plan(
        local_plan,
        executor_run_id="executor-run-docker",
    )
    disabled = DockerExecutorAdapter()
    unavailable = DockerExecutorAdapter(
        enabled=True,
        capability_client=FakeDockerCapabilityClient(False),
    )
    available = DockerExecutorAdapter(
        enabled=True,
        capability_client=FakeDockerCapabilityClient(True),
    )

    disabled_capability = disabled.capability()
    unavailable_capability = unavailable.capability()
    available_capability = available.capability()
    rendered = available.dry_run_render(docker_plan)

    assert disabled_capability.enabled is False
    assert disabled_capability.available is False
    assert disabled_capability.status == "disabled"
    assert unavailable_capability.enabled is True
    assert unavailable_capability.available is False
    assert unavailable_capability.status == "unavailable"
    assert available_capability.available is True
    assert available_capability.status == "available"
    assert rendered["executor_kind"] == "docker"
    assert rendered["mode"] == "dry_run"
    assert rendered["intent_category"] == "validation"
    assert rendered["intent_profile"] == "validation"
    assert rendered["timeout_seconds"] == 120
    serialized = str(rendered)
    for forbidden in (
        "task-1",
        "node-validate",
        "workspace-alpha",
        "pytest",
        "command",
        "stdout",
        "stderr",
        "secret",
    ):
        assert forbidden not in serialized


def test_docker_executor_rejects_denied_or_forged_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    adapter = DockerExecutorAdapter(
        enabled=True,
        capability_client=FakeDockerCapabilityClient(True),
    )
    local_plan = _approved_local_plan(workspace)
    docker_plan = build_docker_executor_plan_from_local_plan(local_plan)
    forged_kind = replace(docker_plan, executor_kind="local")
    forged_signature = replace(docker_plan, node_id="forged-node")
    unsigned = ExecutorPlan(
        executor_kind="docker",
        task_id="task-1",
        node_id="node-validate",
        workspace_ref="workspace-alpha",
        requested_at=_dt(9),
        metadata={"policy_decision": "allow", "approval_route": "allow"},
    )

    with pytest.raises(ValueError, match="executor_kind='docker'"):
        adapter.dry_run_render(forged_kind)
    with pytest.raises(ValueError, match="signed executor plan"):
        adapter.dry_run_render(forged_signature)
    with pytest.raises(ValueError, match="authorized executor plan"):
        adapter.dry_run_render(unsigned)


@pytest.mark.asyncio
async def test_docker_executor_submit_is_deferred(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    plan = build_docker_executor_plan_from_local_plan(_approved_local_plan(workspace))

    with pytest.raises(RuntimeError, match="deferred"):
        await DockerExecutorAdapter(
            enabled=True,
            capability_client=FakeDockerCapabilityClient(True),
        ).submit(plan)


def _run_row(*args: object) -> dict[str, object]:
    (
        executor_run_id,
        executor_kind,
        task_id,
        node_id,
        launch_id,
        topic_id,
        status,
        requested_at,
        submitted_at,
        started_at,
        finished_at,
        error_type,
        error_message,
        sanitized_summary,
        evidence,
        metadata,
    ) = args
    return {
        "executor_run_id": executor_run_id,
        "executor_kind": executor_kind,
        "task_id": task_id,
        "node_id": node_id,
        "launch_id": launch_id,
        "topic_id": topic_id,
        "status": status,
        "requested_at": requested_at,
        "submitted_at": submitted_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "error_type": error_type,
        "error_message": error_message,
        "sanitized_summary": sanitized_summary,
        "evidence": evidence,
        "metadata": metadata,
    }


def _run(
    executor_run_id: str = "executor-run-1",
    *,
    executor_kind: str = "fixture",
    task_id: str = "task-1",
    node_id: str = "node-1",
    status: str = "planned",
    metadata: dict[str, object] | None = None,
) -> ExecutorRunRecord:
    return ExecutorRunRecord(
        executor_run_id=executor_run_id,
        executor_kind=executor_kind,
        task_id=task_id,
        node_id=node_id,
        launch_id="launch-1",
        topic_id="topic-1",
        status=status,
        requested_at=_dt(9),
        metadata=cast(dict[str, object], metadata or {"phase": "planned"}),
    )


def _dt(hour: int) -> datetime:
    return datetime(2026, 5, 23, hour, tzinfo=UTC)


def _clock(*hours: int):
    values = [_dt(hour) for hour in hours]

    def now() -> datetime:
        if values:
            return values.pop(0)
        return _dt(23)

    return now


def _write_template_with_commands(tmp_path: Path):
    template_dir = tmp_path / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join([
            "version: 1",
            "template_id: template-alpha",
            "kind: maintenance",
            "profile: local",
            "title: Local template alpha",
            "summary: Safe local template",
            "topic:",
            "  session_id: session-alpha",
            "context_profile: default",
            "validation_profile: smoke",
            "workspace_policy: local",
            "nodes:",
            "  - node_id: node-validate",
            "    kind: validation",
            "    profile: smoke",
            "    title: Run smoke validation",
            "    command_ref: pytest_smoke",
        ]),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe local template\n",
        encoding="utf-8",
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join([
            "commands:",
            "  - name: pytest_smoke",
            "    profile: validation",
            "    policy: existing_command_policy",
            "    category: validation",
            "    validation_label: pytest_smoke",
            "    status: declared",
            "    metadata:",
            "      owner: local",
        ]),
        encoding="utf-8",
    )
    return load_bee_workspace_template(tmp_path, "template-alpha")


def _approved_local_plan(workspace: Path) -> ExecutorPlan:
    template = _write_template_with_commands(workspace)
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    return build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="task-1",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(9),
    )
