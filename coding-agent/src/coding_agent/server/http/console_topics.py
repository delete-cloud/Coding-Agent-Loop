"""Console topic and schedule page adapters."""

from __future__ import annotations

import logging


from coding_agent.runs.scheduled import (
    ScheduleTriggerRecord,
)
from coding_agent.topics.store import (
    TopicRecord,
)
from coding_agent.server.auth import (
    AuthContext,
)
from coding_agent.server.developer_console import (
    ConsoleActionSummary,
    ConsoleRunSummary,
    ConsoleSchedulesPage,
    ConsoleTopicDetail,
    ConsoleTopicSummary,
    ConsoleValidationOutcomeSummary,
    safe_id_value,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.console_run_meta import (
    _topic_anchor_summaries_from_run,
    _topic_cost_summary_from_run,
    _topic_id_from_run,
    _topic_recall_summaries_from_run,
    _topic_summary_from_run,
)
from coding_agent.server.http.console_stores import (
    _auth_context_can_access_topic,
    _console_run_summary_from_run,
    _proactive_signal_summary_from_record,
    _schedule_summary_from_record,
    _schedule_trigger_summary_from_record,
    _topic_anchor_summary_from_record,
    _topic_cost_summary_from_record,
    _topic_recall_summary_from_record,
    _topic_summary_from_record,
    _visible_console_runs,
    _visible_console_session_ids,
)
from coding_agent.server.http.console_summaries import (
    _action_validation_summary_from_run,
)

logger = logging.getLogger(LOGGER_NAME)


async def _console_topic_summaries(
    auth_context: AuthContext | None,
) -> list[ConsoleTopicSummary]:
    store_summaries = await _console_topic_summaries_from_store(auth_context)
    if store_summaries:
        return store_summaries
    runs = await _visible_console_runs(auth_context)
    summaries_by_topic: dict[str, ConsoleTopicSummary] = {}
    run_counts: dict[str, int] = {}
    for run in runs:
        summary = _topic_summary_from_run(run)
        if summary is None:
            continue
        run_counts[summary.topic_id] = run_counts.get(summary.topic_id, 0) + 1
        if summary.topic_id not in summaries_by_topic:
            summaries_by_topic[summary.topic_id] = summary
    summaries = [
        ConsoleTopicSummary(
            topic_id=summary.topic_id,
            tape_id=summary.tape_id,
            session_id=summary.session_id,
            kind=summary.kind,
            status=summary.status,
            title=summary.title,
            summary=summary.summary,
            topic_initial_seq=summary.topic_initial_seq,
            topic_finalized_seq=summary.topic_finalized_seq,
            run_count=run_counts[summary.topic_id],
            cost_total_tokens=summary.cost_total_tokens,
        )
        for summary in summaries_by_topic.values()
    ]
    summaries.sort(key=lambda item: (item.session_id or "", item.topic_id))
    return summaries


async def _console_topic_detail(
    topic_id: str,
    auth_context: AuthContext | None,
) -> ConsoleTopicDetail | None:
    safe_topic_id = safe_id_value(topic_id)
    if safe_topic_id is None:
        return None
    store_detail = await _console_topic_detail_from_store(safe_topic_id, auth_context)
    if store_detail is not None:
        return store_detail
    runs = [
        run
        for run in await _visible_console_runs(auth_context)
        if _topic_id_from_run(run) == safe_topic_id
    ]
    if not runs:
        return None
    base_summary = _topic_summary_from_run(runs[0])
    if base_summary is None:
        return None
    summary = ConsoleTopicSummary(
        topic_id=base_summary.topic_id,
        tape_id=base_summary.tape_id,
        session_id=base_summary.session_id,
        kind=base_summary.kind,
        status=base_summary.status,
        title=base_summary.title,
        summary=base_summary.summary,
        topic_initial_seq=base_summary.topic_initial_seq,
        topic_finalized_seq=base_summary.topic_finalized_seq,
        run_count=len(runs),
        cost_total_tokens=base_summary.cost_total_tokens,
    )
    actions: list[ConsoleActionSummary] = []
    validations: list[ConsoleValidationOutcomeSummary] = []
    run_summaries: list[ConsoleRunSummary] = []
    for run in runs:
        run_summaries.append(_console_run_summary_from_run(run))
        action_summary = _action_validation_summary_from_run(run)
        actions.extend(action_summary.actions)
        validations.extend(action_summary.validations)
    return ConsoleTopicDetail(
        summary=summary,
        anchors=tuple(
            anchor for run in runs for anchor in _topic_anchor_summaries_from_run(run)
        ),
        recalls=tuple(
            recall for run in runs for recall in _topic_recall_summaries_from_run(run)
        ),
        cost=_topic_cost_summary_from_run(runs[0]),
        runs=tuple(run_summaries),
        actions=tuple(actions),
        validations=tuple(validations),
    )


async def _console_topic_summaries_from_store(
    auth_context: AuthContext | None,
) -> list[ConsoleTopicSummary]:
    store = _bindings.module()._console_topic_store()
    if store is None:
        return []
    topics: list[TopicRecord] = []
    try:
        if auth_context is None or auth_context.scope == "admin":
            topics = await store.list_topics(limit=100)
        else:
            for session_id in await _visible_console_session_ids(auth_context):
                topics.extend(await store.list_topics(session_id=session_id, limit=100))
    except Exception:
        logger.exception(
            "Console topic store list failed; falling back to run metadata"
        )
        return []
    summaries = [
        _topic_summary_from_record(topic, await store.load_topic_cost(topic.topic_id))
        for topic in topics
    ]
    summaries.sort(key=lambda item: (item.session_id or "", item.topic_id))
    return summaries


async def _console_topic_detail_from_store(
    topic_id: str,
    auth_context: AuthContext | None,
) -> ConsoleTopicDetail | None:
    store = _bindings.module()._console_topic_store()
    if store is None:
        return None
    try:
        topic = await store.load_topic(topic_id)
    except Exception:
        logger.exception(
            "Console topic store load failed; falling back to run metadata"
        )
        return None
    if topic is None:
        return None
    if not await _auth_context_can_access_topic(auth_context, topic):
        return None
    try:
        anchors = tuple(
            _topic_anchor_summary_from_record(anchor)
            for anchor in await store.list_topic_anchors(topic.topic_id)
        )
        recalls = tuple(
            _topic_recall_summary_from_record(recall)
            for recall in await store.list_recall_links(topic.topic_id)
        )
        cost = await store.load_topic_cost(topic.topic_id)
    except Exception:
        logger.exception(
            "Console topic store detail failed; falling back to run metadata"
        )
        return None
    runs = [
        run
        for run in await _visible_console_runs(auth_context)
        if _topic_id_from_run(run) == topic.topic_id
    ]
    run_summaries = tuple(_console_run_summary_from_run(run) for run in runs)
    actions: list[ConsoleActionSummary] = []
    validations: list[ConsoleValidationOutcomeSummary] = []
    for run in runs:
        action_summary = _action_validation_summary_from_run(run)
        actions.extend(action_summary.actions)
        validations.extend(action_summary.validations)
    return ConsoleTopicDetail(
        summary=_topic_summary_from_record(topic, cost),
        anchors=anchors,
        recalls=recalls,
        cost=_topic_cost_summary_from_record(cost),
        runs=run_summaries,
        actions=tuple(actions),
        validations=tuple(validations),
    )


async def _console_schedules_page(
    auth_context: AuthContext | None,
) -> ConsoleSchedulesPage:
    store = _bindings.module()._console_scheduled_run_store()
    if store is None:
        return ConsoleSchedulesPage(schedules=(), triggers=(), signals=())
    try:
        visible_session_ids = await _visible_console_session_ids(auth_context)
        if auth_context is None or auth_context.scope == "admin":
            schedules = await store.list_schedules(limit=100)
            signals = await store.list_signals(limit=100)
        else:
            schedules = []
            signals = []
            for session_id in visible_session_ids:
                schedules.extend(await store.list_schedules(session_id=session_id))
                signals.extend(await store.list_signals(session_id=session_id))
        triggers: list[ScheduleTriggerRecord] = []
        for schedule in schedules[:100]:
            triggers.extend(await store.list_triggers(schedule.schedule_id, limit=25))
        for signal in signals[:100]:
            triggers.extend(
                await store.list_triggers(f"signal:{signal.signal_id}", limit=25)
            )
    except Exception:
        logger.exception(
            "Console scheduled run store failed; rendering empty schedule page"
        )
        return ConsoleSchedulesPage(schedules=(), triggers=(), signals=())
    return ConsoleSchedulesPage(
        schedules=tuple(
            _schedule_summary_from_record(schedule) for schedule in schedules
        ),
        triggers=tuple(
            _schedule_trigger_summary_from_record(trigger) for trigger in triggers
        ),
        signals=tuple(
            _proactive_signal_summary_from_record(signal) for signal in signals
        ),
    )


__all__ = [
    "_console_schedules_page",
    "_console_topic_detail",
    "_console_topic_detail_from_store",
    "_console_topic_summaries",
    "_console_topic_summaries_from_store",
]
