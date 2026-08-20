"""Developer console HTML routes. Rendering lives in developer_console."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.developer_console import (
    ConsoleDisplayEventSummary,
    ConsoleInteractionSummary,
    ConsoleRunDetail,
    ConsoleRunSummary,
    ConsoleSessionSummary,
    ConsoleSnapshotSummary,
    ConsoleTapeInfo,
    message_label,
    render_console_actions_page,
    render_console_context_page,
    render_console_interactions_page,
    render_console_memory_page,
    render_console_observability_page,
    render_console_page,
    render_console_run_detail_page,
    render_console_runs_page,
    render_console_sessions_page,
    render_console_tape_page,
    safe_error_summary,
    safe_id_value,
    safe_key_tuple,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.console_actions import (
    _can_search_tape,
    _visible_console_tape_ids,
)
from coding_agent.server.http.console_run_meta import (
    _observability_summary,
)
from coding_agent.server.http.console_stores import _visible_console_runs
from coding_agent.server.http.console_summaries import (
    _action_validation_summary_from_run,
    _context_summary_from_run,
    _correlation_summary_from_run,
    _memory_summary_from_run,
    _memory_summary_from_runs,
    _tape_entry_summary,
)
from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
)
from coding_agent.server.http.routes.runtime import _get_visible_runtime_run
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get("/console", response_class=HTMLResponse)
async def console_overview(request: Request) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_page("/console"))


@router.get("/console/sessions", response_class=HTMLResponse)
async def console_sessions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    sessions: list[ConsoleSessionSummary] = []
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        summary = session.as_dict()
        sessions.append(
            ConsoleSessionSummary(
                session_id=session.id,
                status=str(summary["status"]),
                turn_status=str(summary["turn_status"]),
                created_at=session.created_at,
                updated_at=session.last_activity,
                current_turn_id=session.current_turn_id,
            )
        )
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return HTMLResponse(render_console_sessions_page(sessions))


@router.get("/console/runs", response_class=HTMLResponse)
async def console_runs(
    request: Request,
    status: str | None = Query(None, min_length=1, max_length=80),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    runs: list[ConsoleRunSummary] = []
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
            session_runs = await _bindings.module().session_manager.list_runtime_runs(
                session_id
            )
        except RuntimeError:
            session_runs = []
        for run in session_runs:
            if status is not None and run.status != status:
                continue
            runs.append(
                ConsoleRunSummary(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    status=run.status,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    error_summary=safe_error_summary(run.error),
                )
            )
    runs.sort(key=lambda item: item.started_at, reverse=True)
    return HTMLResponse(render_console_runs_page(runs, status_filter=status))


@router.get("/console/runs/{run_id}", response_class=HTMLResponse)
async def console_run_detail(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    run = await _get_visible_runtime_run(run_id, auth_context)
    try:
        snapshot_record = (
            await _bindings.module().session_manager.load_runtime_message_snapshot(
                run_id
            )
        )
    except (KeyError, RuntimeError):
        snapshot = None
    else:
        snapshot = ConsoleSnapshotSummary(
            snapshot_id=snapshot_record.snapshot_id,
            message_count=len(snapshot_record.messages),
            created_at=snapshot_record.created_at,
            message_labels=tuple(
                message_label(message) for message in snapshot_record.messages
            ),
            metadata_keys=safe_key_tuple(snapshot_record.metadata),
        )
    try:
        events = await _bindings.module().session_manager.replay_display_events(
            run_id, limit=1000
        )
    except (KeyError, RuntimeError):
        events = []
    event_summaries = tuple(
        ConsoleDisplayEventSummary(
            sequence=event.sequence,
            source_event_id=event.source_event_id,
            display_kind=event.display_kind,
            created_at=event.created_at,
            payload_keys=safe_key_tuple(event.payload),
        )
        for event in sorted(
            events, key=lambda item: (item.sequence or 0, item.created_at)
        )
    )
    detail = ConsoleRunDetail(
        run_id=run.run_id,
        session_id=run.session_id,
        tape_id=run.tape_id,
        parent_run_id=run.parent_run_id,
        agent_id=run.agent_id,
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        error_summary=safe_error_summary(run.error),
        metadata_keys=safe_key_tuple(run.metadata),
        result_keys=safe_key_tuple(run.result),
        snapshot=snapshot,
        events=event_summaries,
    )
    return HTMLResponse(render_console_run_detail_page(detail))


@router.get("/console/interactions", response_class=HTMLResponse)
async def console_interactions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    interactions: list[ConsoleInteractionSummary] = []
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
            runs = await _bindings.module().session_manager.list_runtime_runs(
                session_id
            )
        except RuntimeError:
            runs = []
        for run in runs:
            try:
                run_interactions = (
                    await _bindings.module().session_manager.list_runtime_interactions(
                        run.run_id
                    )
                )
            except RuntimeError:
                run_interactions = []
            for interaction in run_interactions:
                interactions.append(
                    ConsoleInteractionSummary(
                        interaction_id=interaction.interaction_id,
                        run_id=interaction.run_id,
                        session_id=run.session_id,
                        tool_call_id=safe_id_value(
                            interaction.metadata.get("tool_call_id")
                        ),
                        interaction_kind=interaction.interaction_kind,
                        status=interaction.status,
                        created_at=interaction.created_at,
                        resolved_at=interaction.resolved_at,
                    )
                )
    interactions.sort(key=lambda item: item.created_at, reverse=True)
    return HTMLResponse(render_console_interactions_page(interactions))


@router.get("/console/tape", response_class=HTMLResponse)
async def console_tape(
    request: Request,
    tape_id: str | None = Query(None, min_length=1, max_length=200),
    kind: str | None = Query(None, min_length=1, max_length=80),
    run_id: str | None = Query(None, min_length=1, max_length=200),
    tool_call_id: str | None = Query(None, min_length=1, max_length=200),
    anchor_type: str | None = Query(None, min_length=1, max_length=80),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if (
        run_id is not None
        and auth_context is not None
        and auth_context.scope != "admin"
    ):
        try:
            visible_run = await _get_visible_runtime_run(run_id, auth_context)
        except HTTPException as exc:
            if exc.status_code == 404:
                return HTMLResponse(render_console_tape_page(None, []))
            raise
        if visible_run.tape_id is not None:
            if tape_id is not None and tape_id != visible_run.tape_id:
                return HTMLResponse(render_console_tape_page(None, []))
            tape_id = visible_run.tape_id
    visible_tape_ids = await _visible_console_tape_ids(auth_context)
    if not _can_search_tape(
        auth_context=auth_context,
        tape_id=tape_id,
        run_id=run_id,
        visible_tape_ids=visible_tape_ids,
    ):
        return HTMLResponse(render_console_tape_page(None, []))
    if (
        auth_context is not None
        and auth_context.scope != "admin"
        and tape_id is None
        and run_id is None
    ):
        entries = []
        for visible_tape_id in sorted(visible_tape_ids):
            entries.extend(
                await _bindings.module().session_manager.search_tape_debug_entries(
                    tape_id=visible_tape_id,
                    kind=kind,
                    run_id=None,
                    tool_call_id=tool_call_id,
                    anchor_type=anchor_type,
                    limit=100,
                )
            )
        return HTMLResponse(
            render_console_tape_page(
                None,
                [_tape_entry_summary(entry) for entry in entries],
            )
        )
    info = None
    if tape_id is not None:
        tape_info = await _bindings.module().session_manager.load_tape_debug_info(
            tape_id
        )
        if tape_info is not None:
            info = ConsoleTapeInfo(
                tape_id=tape_info.tape_id,
                entry_count=tape_info.entry_count,
                first_seq=tape_info.first_seq,
                last_seq=tape_info.last_seq,
            )
    entries = await _bindings.module().session_manager.search_tape_debug_entries(
        tape_id=tape_id,
        kind=kind,
        run_id=run_id,
        tool_call_id=tool_call_id,
        anchor_type=anchor_type,
        limit=100,
    )
    summaries = [_tape_entry_summary(entry) for entry in entries]
    return HTMLResponse(render_console_tape_page(info, summaries))


@router.get("/console/context", response_class=HTMLResponse)
async def console_context(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        return HTMLResponse(render_console_context_page(None))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_context_page(None))
        raise
    return HTMLResponse(render_console_context_page(_context_summary_from_run(run)))


@router.get("/console/memory", response_class=HTMLResponse)
async def console_memory(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        runs = await _visible_console_runs(auth_context)
        return HTMLResponse(render_console_memory_page(_memory_summary_from_runs(runs)))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_memory_page(None))
        raise
    return HTMLResponse(render_console_memory_page(_memory_summary_from_run(run)))


@router.get("/console/actions", response_class=HTMLResponse)
async def console_actions(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        return HTMLResponse(render_console_actions_page(None))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_actions_page(None))
        raise
    return HTMLResponse(
        render_console_actions_page(_action_validation_summary_from_run(run))
    )


@router.get("/console/observability", response_class=HTMLResponse)
async def console_observability(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    correlation = None
    if run_id is not None:
        try:
            run = await _get_visible_runtime_run(run_id, auth_context)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            correlation = _correlation_summary_from_run(run)
    return HTMLResponse(
        render_console_observability_page(
            _observability_summary(correlation=correlation)
        )
    )


__all__ = [
    "console_actions",
    "console_context",
    "console_interactions",
    "console_memory",
    "console_observability",
    "console_overview",
    "console_run_detail",
    "console_runs",
    "console_sessions",
    "console_tape",
]
