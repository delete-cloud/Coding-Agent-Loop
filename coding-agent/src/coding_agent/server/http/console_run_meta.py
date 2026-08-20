"""Console topic-from-run, observability, workspace, and release summaries."""

from __future__ import annotations

import logging
from pathlib import Path


from coding_agent.environment import (
    WorkspaceProviderCapabilities,
)
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.developer_console import (
    ConsoleCorrelationSummary,
    ConsoleObservabilitySummary,
    ConsoleReleaseGateSummary,
    ConsoleReleaseSummary,
    ConsoleTopicAnchorSummary,
    ConsoleTopicCostSummary,
    ConsoleTopicRecallSummary,
    ConsoleTopicSummary,
    ConsoleWorkspaceCapabilitySummary,
    ConsoleWorkspaceSummary,
    safe_error_summary,
    safe_id_value,
    safe_key_tuple,
    safe_label_value,
    safe_text_value,
)
from coding_agent.server.rate_limit import limiter
from coding_agent.server.schemas import (
    WorkspaceSummarySchema,
)
from coding_agent.verification.release_manifest import (
    load_release_verification_manifest,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.config import _prometheus_metrics_enabled
from coding_agent.server.http.console_actions import (
    _metadata_lists,
    _optional_int,
    _safe_observability_config,
    _safe_observability_link,
)
from coding_agent.server.http.lifecycle import _active_cloud_workspace_ids
from coding_agent.server.http.deps import _safe_dict
from coding_agent.server.http.workspace_retention import (
    _remote_retention_enabled,
    _workspace_record_summary_response,
    _workspace_summary_response,
)

logger = logging.getLogger(LOGGER_NAME)


def _topic_summary_from_run(run: AgentRunRecord) -> ConsoleTopicSummary | None:
    topic = _topic_metadata(run)
    topic_id = _topic_id_from_run(run)
    if topic_id is None:
        return None
    cost = _safe_dict(topic.get("cost") or run.metadata.get("topic_cost"))
    return ConsoleTopicSummary(
        topic_id=topic_id,
        tape_id=safe_id_value(topic.get("tape_id") or run.tape_id),
        session_id=safe_id_value(topic.get("session_id") or run.session_id),
        kind=safe_label_value(topic.get("kind") or run.metadata.get("topic_kind"))
        or "unknown",
        status=safe_label_value(topic.get("status") or run.metadata.get("topic_status"))
        or "unknown",
        title=safe_text_value(topic.get("title")),
        summary=safe_text_value(topic.get("summary")),
        topic_initial_seq=_optional_int(
            _first_present(topic, run.metadata, "topic_initial_seq")
        ),
        topic_finalized_seq=_optional_int(
            _first_present(topic, run.metadata, "topic_finalized_seq")
        ),
        run_count=1,
        cost_total_tokens=_optional_int(cost.get("total_tokens")),
    )


def _topic_metadata(run: AgentRunRecord) -> dict[str, object]:
    return _safe_dict(run.metadata.get("topic"))


def _topic_id_from_run(run: AgentRunRecord) -> str | None:
    topic = _topic_metadata(run)
    return safe_id_value(topic.get("topic_id") or run.metadata.get("topic_id"))


def _first_present(
    primary: dict[str, object],
    secondary: dict[str, object],
    key: str,
) -> object:
    if key in primary:
        return primary[key]
    return secondary.get(key)


def _topic_anchor_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleTopicAnchorSummary, ...]:
    topic = _topic_metadata(run)
    anchors = _metadata_lists(
        {"items": topic.get("anchors") or run.metadata.get("topic_anchors")},
        ("items",),
    )
    summaries = []
    for anchor in anchors:
        anchor_type = safe_label_value(
            anchor.get("anchor_type") or anchor.get("product_anchor_type")
        )
        if anchor_type is None:
            continue
        summaries.append(
            ConsoleTopicAnchorSummary(
                seq=_optional_int(anchor.get("seq")),
                anchor_type=anchor_type,
                entry_id=safe_id_value(anchor.get("entry_id")),
            )
        )
    return tuple(summaries)


def _topic_recall_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleTopicRecallSummary, ...]:
    topic = _topic_metadata(run)
    recalls = _metadata_lists(
        {"items": topic.get("recall_links") or run.metadata.get("topic_recall_links")},
        ("items",),
    )
    summaries = []
    for recall in recalls:
        recalled_topic_id = safe_id_value(
            recall.get("recalled_topic_id") or recall.get("target_topic_id")
        )
        relation = safe_label_value(recall.get("relation")) or "unknown"
        if recalled_topic_id is None:
            continue
        summaries.append(
            ConsoleTopicRecallSummary(
                recalled_topic_id=recalled_topic_id,
                relation=relation,
                anchor_seq=_optional_int(recall.get("anchor_seq")),
            )
        )
    return tuple(summaries)


def _topic_cost_summary_from_run(run: AgentRunRecord) -> ConsoleTopicCostSummary | None:
    topic = _topic_metadata(run)
    cost = _safe_dict(topic.get("cost") or run.metadata.get("topic_cost"))
    if not cost:
        return None
    return ConsoleTopicCostSummary(
        prompt_tokens=_optional_int(cost.get("prompt_tokens")) or 0,
        completion_tokens=_optional_int(cost.get("completion_tokens")) or 0,
        total_tokens=_optional_int(cost.get("total_tokens")) or 0,
        run_count=_optional_int(cost.get("run_count")) or 0,
        action_count=_optional_int(cost.get("action_count")) or 0,
        validation_count=_optional_int(cost.get("validation_count")) or 0,
        tool_call_count=_optional_int(cost.get("tool_call_count")) or 0,
    )


def _observability_summary(
    *,
    correlation: ConsoleCorrelationSummary | None,
) -> ConsoleObservabilitySummary:
    config = _safe_observability_config()
    tracing_config = _safe_dict(config.get("tracing"))
    metrics_config = _safe_dict(config.get("metrics"))
    tracing_backend = safe_label_value(
        tracing_config.get("backend")
    ) or safe_label_value(config.get("backend"))
    metrics_backend = safe_label_value(metrics_config.get("backend") or "prometheus")
    return ConsoleObservabilitySummary(
        correlation=correlation,
        metrics_enabled=_prometheus_metrics_enabled(),
        metrics_path="/metrics",
        tracing_backend=tracing_backend,
        metrics_backend=metrics_backend,
        langfuse_url=_safe_observability_link(
            tracing_config.get("public_url")
            or tracing_config.get("ui_url")
            or config.get("langfuse_url")
        ),
        grafana_url=_safe_observability_link(
            metrics_config.get("grafana_url")
            or config.get("grafana_url")
            or config.get("dashboard_url")
        ),
    )


async def _console_workspace_summaries() -> list[ConsoleWorkspaceSummary]:
    if _remote_retention_enabled():
        records = await _bindings.module().session_manager.list_workspace_records()
        summaries = [
            _console_workspace_summary_from_schema(
                _workspace_record_summary_response(record)
            )
            for record in records
        ]
    else:
        try:
            entries = await _bindings.module().asyncio.to_thread(
                _bindings.module().list_cloud_workspaces_from_config,
                _bindings.module()._load_cloud_workspace_config(),
                active_workspace_ids=await _active_cloud_workspace_ids(),
            )
        except ValueError:
            summaries = []
        else:
            summaries = [
                _console_workspace_summary_from_schema(
                    _workspace_summary_response(entry)
                )
                for entry in entries
            ]
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries


def _console_workspace_capability_summary() -> ConsoleWorkspaceCapabilitySummary | None:
    try:
        capabilities = _bindings.module().workspace_provider_capabilities_from_config(
            _bindings.module()._load_cloud_workspace_config()
        )
    except ValueError:
        return None
    return _console_workspace_capability_from_provider(capabilities)


def _console_workspace_capability_from_provider(
    capabilities: WorkspaceProviderCapabilities,
) -> ConsoleWorkspaceCapabilitySummary:
    return ConsoleWorkspaceCapabilitySummary(
        provider=safe_label_value(capabilities.provider) or "redacted",
        available=capabilities.available,
        reason=safe_label_value(capabilities.reason) or "redacted",
        supports_provision=capabilities.supports_provision,
        supports_archive=capabilities.supports_archive,
        supports_diff=capabilities.supports_diff,
        supports_patch=capabilities.supports_patch,
        supports_publish=capabilities.supports_publish,
    )


def _console_workspace_summary_from_schema(
    workspace: WorkspaceSummarySchema,
) -> ConsoleWorkspaceSummary:
    result_refs = workspace.result_refs or {}
    return ConsoleWorkspaceSummary(
        workspace_id=safe_id_value(workspace.workspace_id) or "redacted",
        status=safe_label_value(workspace.status) or "redacted",
        updated_at=workspace.updated_at,
        session_id=safe_id_value(workspace.session_id),
        provider=safe_label_value(workspace.provider),
        provider_instance_id=safe_label_value(workspace.provider_instance_id),
        workspace_host_label=safe_label_value(workspace.workspace_host_label),
        source_kind=safe_label_value(workspace.source_kind),
        retention_policy=safe_label_value(workspace.retention_policy),
        expires_at=workspace.expires_at,
        is_local=workspace.is_local,
        result_ref_keys=safe_key_tuple(result_refs),
        cleanup_error=safe_error_summary(workspace.cleanup_error),
    )


async def _release_summary() -> ConsoleReleaseSummary:
    readiness_checks: dict[str, str]
    try:
        session_store_ok = bool(
            await _bindings.module().session_manager.check_health_async()
        )
    except Exception:
        logger.exception("Console session store readiness check failed")
        session_store_ok = False
    try:
        rate_limiter_ok = bool(limiter._storage.check())
    except Exception:
        logger.exception("Console rate limiter readiness check failed")
        rate_limiter_ok = False
    readiness_checks = {
        "session_store": "ok" if session_store_ok else "error",
        "rate_limiter": "ok" if rate_limiter_ok else "error",
    }
    ready = session_store_ok and rate_limiter_ok
    manifest_name = None
    gates: tuple[ConsoleReleaseGateSummary, ...] = ()
    manifest_path = Path("docs/release_hardening/release-verification.yaml")
    try:
        manifest = load_release_verification_manifest(manifest_path)
    except Exception:
        logger.exception("Unable to load release verification manifest")
    else:
        manifest_name = manifest.name
        gates = tuple(
            ConsoleReleaseGateSummary(
                gate_id=gate.id,
                command=gate.command,
                required=gate.required,
                scope=gate.scope,
            )
            for gate in manifest.gates
        )
    return ConsoleReleaseSummary(
        health_status="healthy",
        session_count=await _bindings.module().session_manager.count_sessions_async(),
        version="2.0.0",
        readiness_status="ready" if ready else "not_ready",
        readiness_checks=tuple(sorted(readiness_checks.items())),
        release_manifest_name=manifest_name,
        release_gates=gates,
    )


__all__ = [
    "_console_workspace_capability_from_provider",
    "_console_workspace_capability_summary",
    "_console_workspace_summaries",
    "_console_workspace_summary_from_schema",
    "_first_present",
    "_observability_summary",
    "_release_summary",
    "_topic_anchor_summaries_from_run",
    "_topic_cost_summary_from_run",
    "_topic_id_from_run",
    "_topic_metadata",
    "_topic_recall_summaries_from_run",
    "_topic_summary_from_run",
]
