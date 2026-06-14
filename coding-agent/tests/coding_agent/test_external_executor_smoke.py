from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coding_agent.bee.command_bridge import (
    complete_bee_node_from_bridge_result,
    plan_bee_command_intent,
)
from coding_agent.bee.workspace import (
    BeeWorkspaceExecutorRunArtifact,
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
    build_bee_manifest_from_workspace_template,
    load_bee_workspace_template,
    write_bee_workspace_run_artifacts,
)
from coding_agent.executors.external import (
    ArgoWorkflowExecutorAdapter,
    DockerExecutorAdapter,
    ExecutorEvidence,
    ExecutorPlan,
    ExecutorResult,
    ExecutorRunRecord,
    KubernetesJobExecutorAdapter,
    LocalExecutorAdapter,
    build_argo_workflow_executor_plan_from_local_plan,
    build_docker_executor_plan_from_local_plan,
    build_kubernetes_job_executor_plan_from_local_plan,
    build_local_executor_plan_from_bee_command_plan,
    executor_result_completion_evidence,
)
from coding_agent.observability import PrometheusMetricsRecorder
from coding_agent.server.developer_console import (
    ConsoleBeePage,
    ConsoleBeeRunArtifactSummary,
    ConsoleExecutorRunSummary,
    render_console_bee_page,
)


class InMemoryExecutorRunStore:
    def __init__(self) -> None:
        self.records: dict[str, ExecutorRunRecord] = {}

    async def create_executor_run(
        self,
        record: ExecutorRunRecord,
    ) -> ExecutorRunRecord:
        if record.executor_run_id in self.records:
            raise ValueError(f"executor run already exists: {record.executor_run_id}")
        self.records[record.executor_run_id] = record
        return record

    async def load_executor_run(
        self,
        executor_run_id: str,
    ) -> ExecutorRunRecord | None:
        return self.records.get(executor_run_id)

    async def list_executor_runs(
        self,
        *,
        task_id: str | None = None,
        node_id: str | None = None,
        executor_kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExecutorRunRecord]:
        records = []
        for record in self.records.values():
            if task_id is not None and record.task_id != task_id:
                continue
            if node_id is not None and record.node_id != node_id:
                continue
            if executor_kind is not None and record.executor_kind != executor_kind:
                continue
            if status is not None and record.status != status:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.executor_run_id)[:limit]

    async def update_executor_run_status(
        self,
        executor_run_id: str,
        *,
        status: str,
        submitted_at: datetime | None,
        started_at: datetime | None,
        finished_at: datetime | None,
        error_type: str | None,
        error_message: str | None,
        metadata: dict[str, object],
    ) -> ExecutorRunRecord:
        record = self.records[executor_run_id]
        updated = ExecutorRunRecord(
            executor_run_id=record.executor_run_id,
            executor_kind=record.executor_kind,
            task_id=record.task_id,
            node_id=record.node_id,
            launch_id=record.launch_id,
            topic_id=record.topic_id,
            status=status,
            requested_at=record.requested_at,
            submitted_at=submitted_at,
            started_at=started_at,
            finished_at=finished_at,
            error_type=error_type,
            error_message=error_message,
            sanitized_summary=record.sanitized_summary,
            evidence=record.evidence,
            metadata=metadata,
        )
        self.records[executor_run_id] = updated
        return updated

    async def attach_executor_result(
        self,
        executor_run_id: str,
        *,
        result: ExecutorResult,
    ) -> ExecutorRunRecord:
        record = self.records[executor_run_id]
        updated = ExecutorRunRecord(
            executor_run_id=record.executor_run_id,
            executor_kind=record.executor_kind,
            task_id=record.task_id,
            node_id=record.node_id,
            launch_id=record.launch_id,
            topic_id=record.topic_id,
            status=result.status,
            requested_at=record.requested_at,
            submitted_at=record.submitted_at,
            started_at=record.started_at,
            finished_at=result.finished_at,
            error_type=result.error_type,
            error_message=result.error_message,
            sanitized_summary=result.sanitized_summary,
            evidence=result.evidence,
            metadata=result.metadata,
        )
        self.records[executor_run_id] = updated
        return updated


@pytest.mark.asyncio
async def test_external_executor_e2e_smoke_local_artifacts_console_metrics(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_template(workspace)
    template = load_bee_workspace_template(workspace, "template-alpha")
    manifest = build_bee_manifest_from_workspace_template(template)
    command_plan = plan_bee_command_intent(
        template=template,
        node=manifest.nodes[0],
        command="pytest -q",
        workspace_root=workspace,
        cwd=workspace,
    )
    local_plan = build_local_executor_plan_from_bee_command_plan(
        command_plan=command_plan,
        task_id="bee-task-alpha",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(1),
        executor_run_id="executor-run-local",
        launch_id="launch-alpha",
        topic_id="topic-alpha",
    )
    store = InMemoryExecutorRunStore()

    def runner(plan: ExecutorPlan, workspace_root: Path) -> ExecutorResult:
        assert plan is local_plan
        assert workspace_root == workspace.resolve()
        return ExecutorResult(
            status="succeeded",
            sanitized_summary="Validation passed",
            finished_at=_dt(4),
            evidence=(
                ExecutorEvidence(
                    evidence_kind="sanitized_artifact",
                    evidence_ref="evidence/local-executor.md",
                    summary="Sanitized executor evidence",
                ),
            ),
            metadata={"phase": "runner_finished"},
        )

    result = await LocalExecutorAdapter(
        store=store,
        runner=runner,
        workspace_resolver=lambda workspace_ref: (
            workspace if workspace_ref == "workspace-alpha" else None
        ),
        now=_clock(2, 3, 4),
    ).submit(local_plan)
    record = await store.load_executor_run("executor-run-local")
    completion = complete_bee_node_from_bridge_result(
        node=manifest.nodes[0],
        evidence=executor_result_completion_evidence(result),
    )

    assert record is not None
    assert record.status == "succeeded"
    assert record.sanitized_summary == "Local executor succeeded"
    assert completion.status == "completed"

    artifact_paths = write_bee_workspace_run_artifacts(
        workspace,
        BeeWorkspaceRunArtifacts(
            task_id="bee-task-alpha",
            template_id="template-alpha",
            topic_id="topic-alpha",
            status="completed",
            nodes=(
                BeeWorkspaceRunNode(
                    node_id="node-validate",
                    status="completed",
                    run_id="run-alpha",
                    attempts=1,
                    executor_run_id=record.executor_run_id,
                    executor_kind=record.executor_kind,
                ),
            ),
            run_ids=("run-alpha",),
            report_title="Bee task completed",
            report_summary="Executor evidence accepted.",
            executor_runs=(
                BeeWorkspaceExecutorRunArtifact(
                    executor_run_id=record.executor_run_id,
                    executor_kind=record.executor_kind,
                    status=record.status,
                    executor_summary=record.sanitized_summary or "Executor succeeded",
                    task_id=record.task_id,
                    node_id=record.node_id,
                    launch_id=record.launch_id,
                    topic_id=record.topic_id,
                    executor_evidence_path="evidence/local-executor.md",
                ),
            ),
        ),
    )
    task_json = json.loads(artifact_paths.task_json_path.read_text(encoding="utf-8"))
    report = artifact_paths.report_path.read_text(encoding="utf-8")
    evidence = (artifact_paths.evidence_dir / "local-executor.md").read_text(
        encoding="utf-8"
    )

    assert task_json["executor_runs"][0]["executor_kind"] == "local"
    assert task_json["nodes"][0]["executor_run_id"] == "executor-run-local"
    assert "Local executor succeeded" in report
    assert "Local executor succeeded" in evidence

    html = render_console_bee_page(
        ConsoleBeePage(
            tasks=(),
            nodes=(),
            run_artifacts=(
                ConsoleBeeRunArtifactSummary(
                    task_id="bee-task-alpha",
                    template_id="template-alpha",
                    topic_id="topic-alpha",
                    status="completed",
                    node_count=1,
                    run_count=1,
                    action_count=0,
                    validation_count=0,
                    executor_count=1,
                    has_report=True,
                    has_memory_candidates=False,
                ),
            ),
            executor_runs=(
                ConsoleExecutorRunSummary(
                    executor_run_id=record.executor_run_id,
                    executor_kind=record.executor_kind,
                    status=record.status,
                    task_id=record.task_id,
                    node_id=record.node_id,
                    launch_id=record.launch_id,
                    topic_id=record.topic_id,
                    capability_status="available",
                    sanitized_summary=record.sanitized_summary,
                ),
            ),
        )
    )
    assert "Executor Runs" in html
    assert "executor-run-local" in html
    assert "Local executor succeeded" in html

    recorder = PrometheusMetricsRecorder()
    recorder.record_executor_run(
        executor_kind=record.executor_kind,
        status=record.status,
        duration_ms=20,
    )
    recorder.record_executor_capability(
        executor_kind=record.executor_kind,
        status="available",
    )
    metrics = recorder.exposition_text()
    assert 'executor_kind="local"' in metrics
    assert "executor-run-local" not in metrics
    assert "bee-task-alpha" not in metrics
    assert "node-validate" not in metrics

    combined = f"{json.dumps(task_json)}\n{report}\n{evidence}\n{html}\n{metrics}"
    for forbidden in ("stdout:", "stderr:", "raw_log", "command_output", "secret="):
        assert forbidden not in combined


def test_external_executor_e2e_smoke_optional_adapters_are_dry_run_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_template(workspace)
    local_plan = _authorized_local_plan(workspace)
    docker_plan = build_docker_executor_plan_from_local_plan(local_plan)
    kubernetes_plan = build_kubernetes_job_executor_plan_from_local_plan(local_plan)
    argo_plan = build_argo_workflow_executor_plan_from_local_plan(local_plan)

    docker_rendered = DockerExecutorAdapter(enabled=True).dry_run_render(docker_plan)
    kubernetes_rendered = KubernetesJobExecutorAdapter(enabled=True).dry_run_render(
        kubernetes_plan
    )
    argo_rendered = ArgoWorkflowExecutorAdapter(enabled=True).dry_run_render(argo_plan)
    kubernetes_result = KubernetesJobExecutorAdapter(enabled=True).import_status(
        kubernetes_plan,
        {"condition": "complete", "pod_name": "raw-pod"},
    )
    argo_result = ArgoWorkflowExecutorAdapter(enabled=True).import_status(
        argo_plan,
        {"phase": "Succeeded", "workflow_name": "raw-workflow"},
    )

    assert docker_rendered["mode"] == "dry_run"
    assert kubernetes_rendered["kind"] == "Job"
    assert argo_rendered["kind"] == "Workflow"
    assert kubernetes_result.status == "succeeded"
    assert argo_result.status == "succeeded"
    combined = (
        f"{docker_rendered}\n{kubernetes_rendered}\n{argo_rendered}\n"
        f"{kubernetes_result}\n{argo_result}"
    )
    for forbidden in (
        "pytest",
        "task-alpha",
        "node-validate",
        "workspace-alpha",
        "raw-pod",
        "raw-workflow",
        "stdout",
        "stderr",
        "secret",
        "argocd",
    ):
        assert forbidden not in combined.casefold()


def _authorized_local_plan(workspace: Path) -> ExecutorPlan:
    template = load_bee_workspace_template(workspace, "template-alpha")
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
        task_id="task-alpha",
        workspace_ref="workspace-alpha",
        approved_workspace_root=workspace,
        requested_at=_dt(1),
    )


def _write_template(workspace: Path) -> None:
    template_dir = workspace / ".bee" / "templates" / "template-alpha"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True)
    (template_dir / "metadata.yaml").write_text(
        "\n".join([
            "version: 1",
            "template_id: template-alpha",
            "kind: maintenance",
            "profile: local",
            "title: Local template",
            "topic:",
            "  session_id: session-alpha",
            "nodes:",
            "  - node_id: node-validate",
            "    kind: validation",
            "    profile: default",
            "    title: Validate local task",
            "    command_ref: smoke",
        ]),
        encoding="utf-8",
    )
    (template_dir / "SKILL.md").write_text("# Template Skill\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: safe external executor smoke\n", encoding="utf-8"
    )
    (template_dir / "commands.yaml").write_text(
        "\n".join([
            "commands:",
            "  - name: smoke",
            "    profile: validation",
            "    policy: existing_command_policy",
            "    category: validation",
            "    validation_label: pytest_smoke",
        ]),
        encoding="utf-8",
    )


def _dt(second: int) -> datetime:
    return datetime(2026, 5, 20, 1, 0, second, tzinfo=UTC)


def _clock(*seconds: int) -> Callable[[], datetime]:
    values = iter(seconds)

    def now() -> datetime:
        return _dt(next(values))

    return now
