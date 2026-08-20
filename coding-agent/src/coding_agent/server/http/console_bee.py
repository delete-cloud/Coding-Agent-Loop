"""Console bee page plus executor and launch summaries."""

from __future__ import annotations

import logging
from collections.abc import Iterable


from coding_agent.bee.launch import BeeLaunchRecord
from coding_agent.executors.external import ExecutorRunRecord
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.server.developer_console import (
    ConsoleBeeLaunchSummary,
    ConsoleBeePage,
    ConsoleBeeTaskSummary,
    ConsoleExecutorRunSummary,
    safe_error_summary,
    safe_id_value,
    safe_label_value,
)

from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.console_bee_packs import (
    _bee_node_summaries_from_run,
    _can_view_global_console_artifacts,
    _combined_bee_status,
    _console_bee_pack_compatibility_summaries,
    _console_bee_pack_dry_run_summaries,
    _console_bee_pack_summaries,
    _console_bee_pack_template_summaries,
    _console_bee_workspace_command_summaries,
    _console_bee_workspace_run_artifact_summaries,
    _console_bee_workspace_template_summaries,
)
from coding_agent.server.http.console_stores import (
    _console_bee_launch_store,
    _console_executor_run_store,
    _visible_console_runs,
    _visible_console_session_ids,
)

logger = logging.getLogger(LOGGER_NAME)


async def _console_bee_page(auth_context: AuthContext | None) -> ConsoleBeePage:
    runs = await _visible_console_runs(auth_context)
    nodes = tuple(node for run in runs for node in _bee_node_summaries_from_run(run))
    launch_summaries = await _bee_launch_summaries(auth_context, runs)
    tasks_by_id: dict[str, ConsoleBeeTaskSummary] = {}
    for node in nodes:
        current = tasks_by_id.get(node.task_id)
        if current is None:
            tasks_by_id[node.task_id] = ConsoleBeeTaskSummary(
                task_id=node.task_id,
                topic_id=node.topic_id,
                session_id=node.session_id,
                kind=node.task_kind,
                profile=node.task_profile,
                status=node.status,
                node_count=1,
                run_count=1 if node.run_id else 0,
            )
            continue
        tasks_by_id[node.task_id] = ConsoleBeeTaskSummary(
            task_id=current.task_id,
            topic_id=current.topic_id,
            session_id=current.session_id,
            kind=current.kind,
            profile=current.profile,
            status=_combined_bee_status(current.status, node.status),
            node_count=current.node_count + 1,
            run_count=current.run_count + (1 if node.run_id else 0),
        )
    can_view_workspace_artifacts = _can_view_global_console_artifacts(auth_context)
    return ConsoleBeePage(
        tasks=tuple(sorted(tasks_by_id.values(), key=lambda item: item.task_id)),
        nodes=tuple(sorted(nodes, key=lambda item: (item.task_id, item.node_id))),
        launches=launch_summaries,
        executor_runs=await _executor_run_summaries(auth_context, runs),
        packs=(_console_bee_pack_summaries() if can_view_workspace_artifacts else ()),
        pack_templates=(
            _console_bee_pack_template_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        pack_compatibility=(
            _console_bee_pack_compatibility_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        pack_dry_runs=(
            _console_bee_pack_dry_run_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        templates=(
            _console_bee_workspace_template_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        run_artifacts=(
            _console_bee_workspace_run_artifact_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        commands=(
            _console_bee_workspace_command_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
    )


async def _executor_run_summaries(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    summaries = {
        summary.executor_run_id: summary
        for summary in await _executor_run_summaries_from_store(auth_context, runs)
    }
    for summary in _executor_run_summaries_from_runs(runs):
        summaries.setdefault(summary.executor_run_id, summary)
    return tuple(sorted(summaries.values(), key=lambda item: item.executor_run_id))


async def _executor_run_summaries_from_store(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    store = _console_executor_run_store()
    if store is None:
        return ()
    visible_task_ids = {
        task_id
        for run in runs
        if (task_id := safe_id_value(run.metadata.get("task_id"))) is not None
    }
    try:
        records = await store.list_executor_runs(limit=100)
    except Exception:
        logger.exception("Console executor run store list failed")
        return ()
    if auth_context is not None and auth_context.scope != "admin":
        records = [record for record in records if record.task_id in visible_task_ids]
    return tuple(_executor_run_summary_from_record(record) for record in records)


def _executor_run_summary_from_record(
    record: ExecutorRunRecord,
) -> ConsoleExecutorRunSummary:
    return ConsoleExecutorRunSummary(
        executor_run_id=safe_id_value(record.executor_run_id) or "unknown",
        executor_kind=safe_label_value(record.executor_kind) or "unknown",
        status=safe_label_value(record.status) or "unknown",
        task_id=safe_id_value(record.task_id),
        node_id=safe_id_value(record.node_id),
        launch_id=safe_id_value(record.launch_id),
        topic_id=safe_id_value(record.topic_id),
        capability_status=safe_label_value(record.metadata.get("capability_status")),
        sanitized_summary=safe_error_summary(record.sanitized_summary),
    )


def _executor_run_summaries_from_runs(
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    summaries: dict[str, ConsoleExecutorRunSummary] = {}
    for run in runs:
        metadata = run.metadata
        executor_run_id = safe_id_value(metadata.get("executor_run_id"))
        executor_kind = safe_label_value(metadata.get("executor_kind"))
        if executor_run_id is None or executor_kind is None:
            continue
        summaries[executor_run_id] = ConsoleExecutorRunSummary(
            executor_run_id=executor_run_id,
            executor_kind=executor_kind,
            status=safe_label_value(metadata.get("executor_status")) or run.status,
            task_id=safe_id_value(metadata.get("task_id")),
            node_id=safe_id_value(metadata.get("node_id")),
            launch_id=safe_id_value(metadata.get("launch_id")),
            topic_id=safe_id_value(metadata.get("topic_id")),
            capability_status=safe_label_value(metadata.get("executor_capability")),
            sanitized_summary=safe_error_summary(metadata.get("executor_summary")),
        )
    return tuple(sorted(summaries.values(), key=lambda item: item.executor_run_id))


async def _bee_launch_summaries(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    launch_summaries = {
        launch.launch_id: launch
        for launch in await _bee_launch_summaries_from_store(auth_context)
    }
    for launch in _bee_launch_summaries_from_runs(runs):
        launch_summaries.setdefault(launch.launch_id, launch)
    return tuple(sorted(launch_summaries.values(), key=lambda item: item.launch_id))


async def _bee_launch_summaries_from_store(
    auth_context: AuthContext | None,
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    store = _console_bee_launch_store()
    if store is None:
        return ()
    launches: list[BeeLaunchRecord] = []
    try:
        if auth_context is None or auth_context.scope == "admin":
            launches = await store.list_launches(limit=100)
        else:
            for session_id in await _visible_console_session_ids(auth_context):
                launches.extend(
                    await store.list_launches(session_id=session_id, limit=100)
                )
    except Exception:
        logger.exception(
            "Console Bee launch store list failed; falling back to run metadata"
        )
        return ()
    return tuple(_bee_launch_summary_from_record(launch) for launch in launches)


def _bee_launch_summary_from_record(
    launch: BeeLaunchRecord,
) -> ConsoleBeeLaunchSummary:
    return ConsoleBeeLaunchSummary(
        launch_id=safe_id_value(launch.launch_id),
        source=safe_label_value(launch.source),
        status=safe_label_value(launch.status),
        template_id=safe_id_value(launch.template_id),
        task_id=safe_id_value(launch.task_id),
        topic_id=safe_id_value(launch.topic_id),
        schedule_id=safe_id_value(launch.schedule_id),
        signal_id=safe_id_value(launch.signal_id),
        error_summary=safe_error_summary(launch.error_message or launch.error_type),
    )


def _bee_launch_summaries_from_runs(
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    launches: dict[str, ConsoleBeeLaunchSummary] = {}
    for run in runs:
        metadata = run.metadata
        launch_id = safe_id_value(metadata.get("launch_id"))
        if not launch_id:
            continue
        launch_source = safe_label_value(metadata.get("launch_source"))
        if launch_source not in {"manual", "schedule", "proactive_signal"}:
            continue
        launches[launch_id] = ConsoleBeeLaunchSummary(
            launch_id=launch_id,
            source=launch_source,
            status=safe_label_value(metadata.get("launch_status")) or run.status,
            template_id=safe_id_value(metadata.get("template_id")),
            task_id=safe_id_value(metadata.get("task_id")),
            topic_id=safe_id_value(metadata.get("topic_id")),
            schedule_id=safe_id_value(metadata.get("schedule_id")),
            signal_id=safe_id_value(metadata.get("signal_id")),
            error_summary=safe_error_summary(metadata.get("launch_error")),
        )
    return tuple(sorted(launches.values(), key=lambda item: item.launch_id))


__all__ = [
    "_bee_launch_summaries",
    "_bee_launch_summaries_from_runs",
    "_bee_launch_summaries_from_store",
    "_bee_launch_summary_from_record",
    "_console_bee_page",
    "_executor_run_summaries",
    "_executor_run_summaries_from_runs",
    "_executor_run_summaries_from_store",
    "_executor_run_summary_from_record",
]
