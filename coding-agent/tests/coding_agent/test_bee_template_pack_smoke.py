from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from coding_agent.bee_template_pack import (
    BeePackRegistry,
    BeeTemplatePackSource,
    build_bee_pack_dry_run_plan,
    load_bee_template_pack,
    validate_bee_pack_compatibility,
)
from coding_agent.bee_workspace import (
    BeeWorkspaceRunArtifacts,
    BeeWorkspaceRunNode,
)
from coding_agent.observability import PrometheusMetricsRecorder
from coding_agent.recall_context import TopicRecallPlanner, TopicRecallPlannerInput
from coding_agent.topic_memory import (
    MEMORY_REFERENCE_MODE,
    MemoryReviewStore,
    propose_memory_candidates_from_bee_artifacts,
)
from coding_agent.topic_range_index import TopicRangeIndex
from coding_agent.topic_store import TopicRecord
from coding_agent.ui.developer_console import (
    ConsoleBeePackCompatibilitySummary,
    ConsoleBeePackDryRunSummary,
    ConsoleBeePackSummary,
    ConsoleBeePackTemplateSummary,
    ConsoleBeePage,
    render_console_bee_page,
)

FORBIDDEN_RENDERED_TEXT = (
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "raw prompt",
    "raw message",
    "command_output",
    "stdout",
    "stderr",
    "env",
)


def test_bee_template_pack_smoke_discovers_manifest_and_templates(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path)

    pack = load_bee_template_pack(tmp_path)
    registry = BeePackRegistry.discover(
        (tmp_path,),
        source=BeeTemplatePackSource.FIXTURE,
    )

    assert pack.manifest.pack_id == "pack-alpha"
    assert pack.manifest.domain_profile == "maintenance"
    assert pack.manifest.tags == ("local", "backup")
    assert [summary.pack_id for summary in registry.list_packs()] == ["pack-alpha"]
    assert [
        summary.template_id for summary in registry.list_templates("pack-alpha")
    ] == ["backup-check"]


def test_bee_template_pack_smoke_reports_compatible_and_incompatible_packs(
    tmp_path: Path,
) -> None:
    compatible_root = tmp_path / "compatible"
    incompatible_root = tmp_path / "incompatible"
    _write_pack(compatible_root)
    _write_pack(incompatible_root, include_skill=False)
    marker = compatible_root / "executed"

    compatible = validate_bee_pack_compatibility(
        compatible_root,
        source=BeeTemplatePackSource.FIXTURE,
    )
    incompatible = validate_bee_pack_compatibility(
        incompatible_root,
        source=BeeTemplatePackSource.FIXTURE,
    )

    assert compatible.status == "compatible"
    assert compatible.templates[0].status == "compatible"
    assert incompatible.status == "incompatible"
    assert incompatible.findings[0].recommended_fix
    assert not marker.exists()


def test_bee_template_pack_smoke_builds_non_durable_dry_run_plan(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path)
    registry = BeePackRegistry.discover((tmp_path,))

    plan = build_bee_pack_dry_run_plan(
        registry,
        pack_id="pack-alpha",
        template_id="backup-check",
        inputs={"target": "repo"},
    )

    assert plan.status == "ready"
    assert (
        plan.task_json_path
        == ".bee/runs/dry-run-task-pack-alpha-backup-check/task.json"
    )
    assert plan.report_path.endswith("/report.md")
    assert plan.evidence_dir.endswith("/evidence")
    assert plan.memory_candidates_path.endswith("/memory_candidates.yaml")
    assert plan.command_intents == ("smoke",)
    assert [node["command_ref"] for node in plan.nodes] == ["smoke"]
    assert not (tmp_path / ".bee" / "runs").exists()
    assert not (tmp_path / "executed").exists()


def test_bee_template_pack_smoke_binds_memory_and_recall_by_pack_metadata(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path)
    artifacts = BeeWorkspaceRunArtifacts(
        task_id="bee-task-alpha",
        template_id="backup-check",
        topic_id="topic-backup",
        status="completed",
        nodes=(
            BeeWorkspaceRunNode(
                node_id="node-validate",
                status="completed",
                run_id="run-alpha",
                validation_ids=("validation-alpha",),
                attempts=1,
            ),
        ),
        run_ids=("run-alpha",),
        validation_ids=("validation-alpha",),
        report_title="Backup check completed",
        report_summary="Validation passed with sanitized evidence.",
        memory_candidates=(
            {
                "kind": "project_convention",
                "title": "Backup restore convention",
                "summary": "Backups should be checked before restore.",
                "tags": ["restore"],
                "confidence": 0.8,
            },
        ),
    )
    candidates = propose_memory_candidates_from_bee_artifacts(
        topic=_topic("topic-backup", summary="Backup validation finished"),
        artifacts=artifacts,
        pack_id="pack-alpha",
        domain_profile="maintenance",
        pack_tags=("local", "backup"),
    )
    review_store = MemoryReviewStore()
    review_store.add_candidate(candidates[0])
    accepted = review_store.accept_candidate(candidates[0].candidate_id or "")
    index = TopicRangeIndex()
    index.index_topic(
        _topic("topic-backup", summary="Backup validation finished"),
        tags=("backup", "local"),
        bee_pack_id="pack-alpha",
        bee_template_id="backup-check",
        domain_profile="maintenance",
    )

    plan = TopicRecallPlanner(
        topic_index=index,
        accepted_memories=review_store.accepted_memories(),
    ).plan(
        TopicRecallPlannerInput(
            source_topic=_topic("topic-new", status="open", summary=None, end=None),
            text="backup validation restore",
            bee_pack_id="pack-alpha",
            domain_profile="maintenance",
            tags=("backup",),
        )
    )

    payload = candidates[0].to_dict()
    assert payload["reference_mode"] == MEMORY_REFERENCE_MODE
    assert payload["provenance"]["pack_id"] == "pack-alpha"
    assert payload["provenance"]["template_id"] == "backup-check"
    assert payload["provenance"]["domain_profile"] == "maintenance"
    assert [result.topic_id for result in plan.topic_results] == ["topic-backup"]
    assert plan.accepted_memories == (accepted,)
    assert plan.accepted_memories[0].candidate.to_dict()["reference_mode"] == (
        "reference_only"
    )


def test_bee_template_pack_smoke_renders_console_and_low_cardinality_metrics(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path)
    registry = BeePackRegistry.discover((tmp_path,))
    pack_summary = registry.list_packs()[0]
    template_summary = registry.list_templates("pack-alpha")[0]
    report = validate_bee_pack_compatibility(tmp_path)
    dry_run = build_bee_pack_dry_run_plan(
        registry,
        pack_id="pack-alpha",
        template_id="backup-check",
        inputs={"target": "repo"},
    )
    html = render_console_bee_page(
        ConsoleBeePage(
            tasks=(),
            nodes=(),
            packs=(
                ConsoleBeePackSummary(
                    pack_id=pack_summary.pack_id,
                    name=pack_summary.name,
                    version=pack_summary.version,
                    source_type=pack_summary.source.value,
                    domain_profile=pack_summary.domain_profile,
                    tags=pack_summary.tags,
                    template_count=pack_summary.template_count,
                ),
            ),
            pack_templates=(
                ConsoleBeePackTemplateSummary(
                    pack_id=template_summary.pack_id,
                    template_id=template_summary.template_id,
                    source_type=template_summary.source.value,
                    kind=template_summary.template_kind,
                    profile=template_summary.template_profile,
                    title=template_summary.title,
                ),
            ),
            pack_compatibility=(
                ConsoleBeePackCompatibilitySummary(
                    pack_id=report.pack_id,
                    source_type=report.source.value,
                    status=report.status,
                    check_count=len(report.checks),
                    finding_count=len(report.findings),
                    template_count=len(report.templates),
                    recommended_fixes=(),
                ),
            ),
            pack_dry_runs=(
                ConsoleBeePackDryRunSummary(
                    pack_id=dry_run.pack_id,
                    template_id=dry_run.template_id,
                    source_type=dry_run.source.value,
                    status=dry_run.status,
                    task_json_path=dry_run.task_json_path,
                    report_path=dry_run.report_path,
                    evidence_dir=dry_run.evidence_dir,
                    memory_candidates_path=dry_run.memory_candidates_path,
                    node_count=len(dry_run.nodes),
                    command_count=len(dry_run.command_intents),
                    warning_count=len(dry_run.warnings),
                ),
            ),
        )
    )
    recorder = PrometheusMetricsRecorder()
    recorder.record_bee_pack_validation(
        status=report.status,
        source_type=report.source.value,
    )
    recorder.record_bee_pack_template(
        status=report.templates[0].status,
        source_type=report.source.value,
    )
    recorder.record_bee_pack_dry_run(status=dry_run.status)
    metrics = recorder.exposition_text()

    assert "Bee Template Packs" in html
    assert "pack-alpha" in html
    assert "backup-check" in html
    assert "Bee Pack Compatibility" in html
    assert "Bee Pack Dry-Run Plans" in html
    assert (
        'bee_pack_validations_total{source_type="local_workspace",status="compatible"} 1'
        in metrics
    )
    assert (
        'bee_pack_templates_total{source_type="local_workspace",status="compatible"} 1'
        in metrics
    )
    assert 'bee_pack_dry_runs_total{status="ready"} 1' in metrics
    assert "pack-alpha" not in metrics
    assert "backup-check" not in metrics
    assert "pack_id" not in metrics
    assert "template_id" not in metrics
    for forbidden in FORBIDDEN_RENDERED_TEXT:
        assert forbidden not in html
        assert forbidden not in metrics


def _write_pack(root: Path, *, include_skill: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bee-pack.yaml").write_text(
        """pack_id: pack-alpha
name: Alpha Pack
version: 1.0.0
description: Safe fixture pack
domain_profile: maintenance
templates:
  - backup-check
tags:
  - local
  - backup
metadata:
  owner: platform
""",
        encoding="utf-8",
    )
    template_dir = root / ".bee" / "templates" / "backup-check"
    feature_dir = template_dir / "features"
    feature_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "metadata.yaml").write_text(
        """version: 1
template_id: backup-check
kind: maintenance
profile: local
title: Backup check
topic:
  session_id: session-alpha
inputs:
  required:
    - target
  defaults: {}
metadata:
  risk_profile: low
  report_output_contract: sanitized_markdown
  memory_candidates:
    review_required: true
nodes:
  - node_id: node-validate
    kind: validation
    profile: default
    title: Validate backup
    command_ref: smoke
""",
        encoding="utf-8",
    )
    if include_skill:
        (template_dir / "SKILL.md").write_text("# Backup Check\n", encoding="utf-8")
    (feature_dir / "acceptance.feature").write_text(
        "Feature: backup check emits sanitized evidence\n",
        encoding="utf-8",
    )
    (template_dir / "commands.yaml").write_text(
        """commands:
  - name: smoke
    profile: validation
    policy: existing_command_policy
    category: validation
    validation_label: pytest_smoke
    metadata:
      owner: platform
      purpose: static validation fixture
""",
        encoding="utf-8",
    )


def _topic(
    topic_id: str,
    *,
    status: str = "finalized",
    summary: str | None = "Backup validation finished",
    end: int | None = 9,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id="tape-alpha",
        session_id="session-alpha",
        kind="coding",
        status=status,
        title="Backup topic",
        summary=summary,
        owner="local",
        topic_initial_seq=2,
        topic_finalized_seq=end,
        created_at=datetime(2026, 5, 20, 1, 0, 0, tzinfo=UTC),
        finalized_at=(
            None if end is None else datetime(2026, 5, 20, 1, 5, 0, tzinfo=UTC)
        ),
        metadata={"profile": "local"},
    )
