"""Console bee pack, template, and workspace-command adapters."""

from __future__ import annotations

import json
import logging
from pathlib import Path


from coding_agent.bee.template_pack import (
    BeePackRegistry,
    BeeTemplatePackSource,
    build_bee_pack_dry_run_plan,
    validate_bee_pack_compatibility,
)
from coding_agent.bee.workspace import (
    BeeWorkspaceCommandIntent,
    BeeWorkspaceRunArtifactRecord,
    BeeWorkspaceTemplate,
    discover_bee_workspace_run_artifacts,
    discover_bee_workspace_templates,
    load_bee_workspace_command_intents,
)
from coding_agent.observability import (
    record_bee_pack_dry_run_metric,
    record_bee_pack_template_metric,
    record_bee_pack_validation_metric,
)
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.server.developer_console import (
    ConsoleBeeCommandIntentSummary,
    ConsoleBeeNodeSummary,
    ConsoleBeePackCompatibilitySummary,
    ConsoleBeePackDryRunSummary,
    ConsoleBeePackSummary,
    ConsoleBeePackTemplateSummary,
    ConsoleBeeRunArtifactSummary,
    ConsoleBeeTemplateSummary,
    safe_id_value,
    safe_label_value,
    safe_text_value,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _can_view_global_console_artifacts(auth_context: AuthContext | None) -> bool:
    return auth_context is None or auth_context.scope == "admin"


def _console_bee_workspace_root() -> Path | None:
    config = _bindings.module()._load_bee_workspace_config()
    root = config.get("workspace_root")
    if not isinstance(root, str) or not root.strip():
        return None
    return Path(root).expanduser().resolve()


def _console_bee_workspace_templates() -> tuple[BeeWorkspaceTemplate, ...]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        return tuple(discover_bee_workspace_templates(workspace_root))
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee workspace template discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()


def _console_bee_pack_registry() -> BeePackRegistry | None:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return None
    try:
        return BeePackRegistry.discover(
            (workspace_root,),
            source=BeeTemplatePackSource.LOCAL_WORKSPACE,
        )
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee template pack discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return None


def _console_bee_pack_summaries() -> tuple[ConsoleBeePackSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    return tuple(
        ConsoleBeePackSummary(
            pack_id=summary.pack_id,
            name=safe_text_value(summary.name) or "untitled",
            version=safe_label_value(summary.version) or "unknown",
            source_type=summary.source.value,
            domain_profile=safe_label_value(summary.domain_profile),
            tags=tuple(
                tag
                for tag in (safe_label_value(tag) for tag in summary.tags)
                if tag is not None
            ),
            template_count=summary.template_count,
        )
        for summary in registry.list_packs()
    )


def _console_bee_pack_template_summaries() -> tuple[ConsoleBeePackTemplateSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    summaries: list[ConsoleBeePackTemplateSummary] = []
    for pack in registry.list_packs():
        for template in registry.list_templates(pack.pack_id):
            summaries.append(
                ConsoleBeePackTemplateSummary(
                    pack_id=template.pack_id,
                    template_id=template.template_id,
                    source_type=template.source.value,
                    kind=safe_label_value(template.template_kind) or "unknown",
                    profile=safe_label_value(template.template_profile) or "unknown",
                    title=safe_text_value(template.title) or "untitled",
                )
            )
    return tuple(sorted(summaries, key=lambda item: (item.pack_id, item.template_id)))


def _console_bee_pack_compatibility_summaries() -> tuple[
    ConsoleBeePackCompatibilitySummary, ...
]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        report = validate_bee_pack_compatibility(
            workspace_root,
            source=BeeTemplatePackSource.LOCAL_WORKSPACE,
        )
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee template pack compatibility failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()
    record_bee_pack_validation_metric(
        status=report.status,
        source_type=report.source.value,
    )
    for template in report.templates:
        record_bee_pack_template_metric(
            status=template.status,
            source_type=report.source.value,
        )
    return (
        ConsoleBeePackCompatibilitySummary(
            pack_id=safe_id_value(report.pack_id),
            source_type=report.source.value,
            status=safe_label_value(report.status) or "unknown",
            check_count=len(report.checks),
            finding_count=len(report.findings),
            template_count=len(report.templates),
            recommended_fixes=tuple(
                fix
                for fix in (
                    safe_text_value(finding.recommended_fix)
                    for finding in report.findings[:5]
                )
                if fix is not None
            ),
        ),
    )


def _console_bee_pack_dry_run_summaries() -> tuple[ConsoleBeePackDryRunSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    summaries: list[ConsoleBeePackDryRunSummary] = []
    for pack in registry.list_packs():
        for template in registry.list_templates(pack.pack_id):
            try:
                plan = build_bee_pack_dry_run_plan(
                    registry,
                    pack_id=pack.pack_id,
                    template_id=template.template_id,
                    inputs={},
                )
            except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
                logger.warning(
                    "Console Bee template pack dry-run failed for %s/%s",
                    pack.pack_id,
                    template.template_id,
                    exc_info=exc,
                )
                record_bee_pack_dry_run_metric(status="rejected")
                continue
            record_bee_pack_dry_run_metric(status=plan.status)
            summaries.append(
                ConsoleBeePackDryRunSummary(
                    pack_id=plan.pack_id,
                    template_id=plan.template_id,
                    source_type=plan.source.value,
                    status=safe_label_value(plan.status) or "unknown",
                    task_json_path=safe_text_value(plan.task_json_path) or "-",
                    report_path=safe_text_value(plan.report_path) or "-",
                    evidence_dir=safe_text_value(plan.evidence_dir) or "-",
                    memory_candidates_path=(
                        safe_text_value(plan.memory_candidates_path) or "-"
                    ),
                    node_count=len(plan.nodes),
                    command_count=len(plan.command_intents),
                    warning_count=len(plan.warnings),
                )
            )
    return tuple(sorted(summaries, key=lambda item: (item.pack_id, item.template_id)))


def _console_bee_workspace_template_summaries() -> tuple[
    ConsoleBeeTemplateSummary, ...
]:
    summaries = []
    for template in _console_bee_workspace_templates():
        intents = _safe_bee_workspace_command_intents(template)
        summaries.append(
            ConsoleBeeTemplateSummary(
                template_id=template.template_id,
                kind=safe_label_value(template.metadata.get("kind")) or "unknown",
                profile=safe_label_value(template.metadata.get("profile")) or "unknown",
                title=safe_label_value(template.metadata.get("title")) or "untitled",
                feature_count=len(template.feature_paths),
                has_commands=template.commands_path is not None,
                command_count=len(intents),
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.template_id))


def _console_bee_workspace_run_artifact_summaries() -> tuple[
    ConsoleBeeRunArtifactSummary, ...
]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        records = discover_bee_workspace_run_artifacts(workspace_root)
    except (
        OSError,
        ValueError,
        TypeError,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning(
            "Console Bee workspace run artifact discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()
    return tuple(_console_bee_run_artifact_summary(record) for record in records)


def _console_bee_run_artifact_summary(
    record: BeeWorkspaceRunArtifactRecord,
) -> ConsoleBeeRunArtifactSummary:
    return ConsoleBeeRunArtifactSummary(
        task_id=record.task_id,
        template_id=record.template_id,
        topic_id=record.topic_id,
        status=record.status,
        node_count=record.node_count,
        run_count=record.run_count,
        action_count=record.action_count,
        validation_count=record.validation_count,
        executor_count=record.executor_count,
        has_report=record.has_report,
        has_memory_candidates=record.has_memory_candidates,
    )


def _console_bee_workspace_command_summaries() -> tuple[
    ConsoleBeeCommandIntentSummary, ...
]:
    summaries = []
    for template in _console_bee_workspace_templates():
        for intent in _safe_bee_workspace_command_intents(template):
            summaries.append(_console_bee_command_summary(template.template_id, intent))
    return tuple(sorted(summaries, key=lambda item: (item.template_id, item.name)))


def _safe_bee_workspace_command_intents(
    template: BeeWorkspaceTemplate,
) -> tuple[BeeWorkspaceCommandIntent, ...]:
    try:
        return load_bee_workspace_command_intents(template)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(
            "Console Bee workspace command intent discovery failed for %s",
            template.template_id,
            exc_info=exc,
        )
        return ()


def _console_bee_command_summary(
    template_id: str,
    intent: BeeWorkspaceCommandIntent,
) -> ConsoleBeeCommandIntentSummary:
    return ConsoleBeeCommandIntentSummary(
        template_id=template_id,
        name=intent.name,
        profile=intent.profile,
        policy=intent.policy,
        category=intent.category,
        validation_label=intent.validation_label,
        status=intent.status,
    )


def _bee_node_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleBeeNodeSummary, ...]:
    metadata = run.metadata
    if metadata.get("bee_runtime") != "task_launch":
        return ()
    task_id = safe_id_value(metadata.get("task_id"))
    node_id = safe_id_value(metadata.get("node_id"))
    if task_id is None or node_id is None:
        return ()
    return (
        ConsoleBeeNodeSummary(
            task_id=task_id,
            node_id=node_id,
            run_id=safe_id_value(run.run_id),
            topic_id=safe_id_value(metadata.get("topic_id")),
            session_id=safe_id_value(metadata.get("session_id")),
            task_kind=safe_label_value(metadata.get("task_kind")) or "unknown",
            task_profile=safe_label_value(metadata.get("task_profile")) or "unknown",
            kind=safe_label_value(metadata.get("node_kind")) or "unknown",
            profile=safe_label_value(metadata.get("node_profile")) or "unknown",
            status=safe_label_value(run.status) or "unknown",
            context_profile=safe_label_value(metadata.get("context_profile")),
            validation_profile=safe_label_value(metadata.get("validation_profile")),
            workspace_policy=safe_label_value(metadata.get("workspace_policy")),
            approval_policy=safe_label_value(metadata.get("approval_policy")),
            action_policy=safe_label_value(metadata.get("action_policy")),
            workspace_binding=safe_label_value(metadata.get("workspace_binding")),
        ),
    )


def _combined_bee_status(current: str, next_status: str) -> str:
    if current == next_status:
        return current
    if "failed" in {current, next_status}:
        return "failed"
    if "running" in {current, next_status}:
        return "running"
    if "completed" in {current, next_status}:
        return "completed"
    return current


__all__ = [
    "_bee_node_summaries_from_run",
    "_can_view_global_console_artifacts",
    "_combined_bee_status",
    "_console_bee_command_summary",
    "_console_bee_pack_compatibility_summaries",
    "_console_bee_pack_dry_run_summaries",
    "_console_bee_pack_registry",
    "_console_bee_pack_summaries",
    "_console_bee_pack_template_summaries",
    "_console_bee_run_artifact_summary",
    "_console_bee_workspace_command_summaries",
    "_console_bee_workspace_root",
    "_console_bee_workspace_run_artifact_summaries",
    "_console_bee_workspace_template_summaries",
    "_console_bee_workspace_templates",
    "_safe_bee_workspace_command_intents",
]
