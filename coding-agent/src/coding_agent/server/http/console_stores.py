"""Console store accessors and visibility helpers."""

from __future__ import annotations

import logging
from typing import Any


from coding_agent.bee.launch import PGBeeLaunchStore
from coding_agent.executors.external import PGExecutorRunStore
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.runs.scheduled import (
    PGScheduledRunStore,
    ProactiveSignalRecord,
    ScheduleRecord,
    ScheduleTriggerRecord,
)
from coding_agent.topics.store import (
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.server.developer_console import (
    ConsoleProactiveSignalSummary,
    ConsoleRunSummary,
    ConsoleScheduleSummary,
    ConsoleScheduleTriggerSummary,
    ConsoleTopicAnchorSummary,
    ConsoleTopicCostSummary,
    ConsoleTopicRecallSummary,
    ConsoleTopicSummary,
    safe_error_summary,
    safe_id_value,
    safe_label_value,
    safe_text_value,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import _auth_context_can_access_session
from coding_agent.server.http.config import _storage_uses_pg_http_sessions

logger = logging.getLogger(LOGGER_NAME)


def _console_scheduled_run_store() -> PGScheduledRunStore | None:
    try:
        storage_config = _bindings.module()._load_storage_config()
    except Exception:
        logger.exception(
            "Unable to load storage config for console scheduled run store"
        )
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGScheduledRunStore(pool=_bindings.module().session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console scheduled run store")
        return None


def _console_topic_store() -> Any | None:
    try:
        return _bindings.module().session_manager.selected_topic_store()
    except Exception:
        logger.exception("Unable to initialize console topic store")
        return None


def _console_bee_launch_store() -> PGBeeLaunchStore | None:
    try:
        storage_config = _bindings.module()._load_storage_config()
    except Exception:
        logger.exception("Unable to load storage config for console Bee launch store")
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGBeeLaunchStore(pool=_bindings.module().session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console Bee launch store")
        return None


def _console_executor_run_store() -> PGExecutorRunStore | None:
    try:
        storage_config = _bindings.module()._load_storage_config()
    except Exception:
        logger.exception("Unable to load storage config for console executor store")
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGExecutorRunStore(pool=_bindings.module().session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console executor store")
        return None


async def _auth_context_can_access_topic(
    auth_context: AuthContext | None,
    topic: TopicRecord,
) -> bool:
    if auth_context is None or auth_context.scope == "admin":
        return True
    return topic.session_id in await _visible_console_session_ids(auth_context)


async def _visible_console_session_ids(auth_context: AuthContext | None) -> list[str]:
    session_ids: list[str] = []
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        if _auth_context_can_access_session(auth_context, session):
            session_ids.append(session_id)
    return session_ids


def _topic_summary_from_record(
    topic: TopicRecord,
    cost: TopicCostRecord | None,
) -> ConsoleTopicSummary:
    return ConsoleTopicSummary(
        topic_id=safe_id_value(topic.topic_id) or "redacted",
        tape_id=safe_id_value(topic.tape_id),
        session_id=safe_id_value(topic.session_id),
        kind=safe_label_value(topic.kind) or "unknown",
        status=safe_label_value(topic.status) or "unknown",
        title=safe_text_value(topic.title),
        summary=safe_text_value(topic.summary),
        topic_initial_seq=topic.topic_initial_seq,
        topic_finalized_seq=topic.topic_finalized_seq,
        run_count=cost.run_count if cost is not None else 0,
        cost_total_tokens=cost.total_tokens if cost is not None else None,
    )


def _schedule_summary_from_record(schedule: ScheduleRecord) -> ConsoleScheduleSummary:
    return ConsoleScheduleSummary(
        schedule_id=safe_id_value(schedule.schedule_id) or "redacted",
        session_id=safe_id_value(schedule.session_id) or "redacted",
        topic_id=safe_id_value(schedule.topic_id),
        kind=safe_label_value(schedule.kind) or "unknown",
        status=safe_label_value(schedule.status) or "unknown",
        cadence=safe_label_value(schedule.cadence) or "unknown",
        title=safe_text_value(schedule.title),
        next_due_at=schedule.next_due_at,
        last_triggered_at=schedule.last_triggered_at,
    )


def _schedule_trigger_summary_from_record(
    trigger: ScheduleTriggerRecord,
) -> ConsoleScheduleTriggerSummary:
    return ConsoleScheduleTriggerSummary(
        trigger_id=safe_id_value(trigger.trigger_id) or "redacted",
        schedule_id=safe_id_value(trigger.schedule_id) or "redacted",
        signal_id=safe_id_value(trigger.signal_id),
        topic_id=safe_id_value(trigger.topic_id),
        run_id=safe_id_value(trigger.run_id),
        status=safe_label_value(trigger.status) or "unknown",
        due_at=trigger.due_at,
        planned_at=trigger.planned_at,
        reason=safe_label_value(trigger.reason),
    )


def _proactive_signal_summary_from_record(
    signal: ProactiveSignalRecord,
) -> ConsoleProactiveSignalSummary:
    return ConsoleProactiveSignalSummary(
        signal_id=safe_id_value(signal.signal_id) or "redacted",
        session_id=safe_id_value(signal.session_id),
        topic_id=safe_id_value(signal.topic_id),
        kind=safe_label_value(signal.kind) or "unknown",
        status=safe_label_value(signal.status) or "unknown",
        observed_at=signal.observed_at,
        cooldown_until=signal.cooldown_until,
        summary=safe_text_value(signal.summary),
    )


def _topic_anchor_summary_from_record(
    anchor: TopicAnchorRecord,
) -> ConsoleTopicAnchorSummary:
    return ConsoleTopicAnchorSummary(
        seq=anchor.seq,
        anchor_type=safe_label_value(anchor.anchor_type) or "unknown",
        entry_id=safe_id_value(anchor.entry_id),
    )


def _topic_recall_summary_from_record(
    recall: TopicRecallLinkRecord,
) -> ConsoleTopicRecallSummary:
    return ConsoleTopicRecallSummary(
        recalled_topic_id=safe_id_value(recall.recalled_topic_id) or "redacted",
        relation=safe_label_value(recall.relation) or "unknown",
        anchor_seq=recall.anchor_seq,
    )


def _topic_cost_summary_from_record(
    cost: TopicCostRecord | None,
) -> ConsoleTopicCostSummary | None:
    if cost is None:
        return None
    return ConsoleTopicCostSummary(
        prompt_tokens=cost.prompt_tokens,
        completion_tokens=cost.completion_tokens,
        total_tokens=cost.total_tokens,
        run_count=cost.run_count,
        action_count=cost.action_count,
        validation_count=cost.validation_count,
        tool_call_count=cost.tool_call_count,
    )


async def _visible_console_runs(
    auth_context: AuthContext | None,
) -> list[AgentRunRecord]:
    runs: list[AgentRunRecord] = []
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        try:
            runs.extend(
                await _bindings.module().session_manager.list_runtime_runs(session_id)
            )
        except RuntimeError:
            continue
    return runs


def _console_run_summary_from_run(run: AgentRunRecord) -> ConsoleRunSummary:
    return ConsoleRunSummary(
        run_id=run.run_id,
        session_id=run.session_id,
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        error_summary=safe_error_summary(run.error),
    )


__all__ = [
    "_auth_context_can_access_topic",
    "_console_bee_launch_store",
    "_console_executor_run_store",
    "_console_run_summary_from_run",
    "_console_scheduled_run_store",
    "_console_topic_store",
    "_proactive_signal_summary_from_record",
    "_schedule_summary_from_record",
    "_schedule_trigger_summary_from_record",
    "_topic_anchor_summary_from_record",
    "_topic_cost_summary_from_record",
    "_topic_recall_summary_from_record",
    "_topic_summary_from_record",
    "_visible_console_runs",
    "_visible_console_session_ids",
]
