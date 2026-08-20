"""Runtime run, event, interaction, and display-event routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query, Request

from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.events import DisplayEvent
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    DisplayEventResponse,
    DisplayEventsResponse,
    ResolveInteractionRequest,
    RuntimeEventResponse,
    RuntimeEventsResponse,
    RuntimeInteractionListResponse,
    RuntimeInteractionResponse,
    RuntimeMessageSnapshotResponse,
    RuntimeRunResponse,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.deps import (
    _auth_context_can_access_session,
    _get_visible_session,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


def _runtime_run_response(record: AgentRunRecord) -> RuntimeRunResponse:
    metadata = dict(record.metadata)
    metadata.pop("claim_token_hash", None)
    return RuntimeRunResponse(
        run_id=record.run_id,
        session_id=record.session_id,
        tape_id=record.tape_id,
        parent_run_id=record.parent_run_id,
        agent_id=record.agent_id,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        metadata=metadata,
        result=record.result,
        error=record.error,
    )


def _runtime_message_snapshot_response(
    record: RunMessageSnapshotRecord,
) -> RuntimeMessageSnapshotResponse:
    return RuntimeMessageSnapshotResponse(
        snapshot_id=record.snapshot_id,
        run_id=record.run_id,
        messages=record.messages,
        metadata=record.metadata,
        created_at=record.created_at,
    )


def _runtime_event_response(record: RuntimeEventRecord) -> RuntimeEventResponse:
    return RuntimeEventResponse(
        sequence=record.sequence,
        event_id=record.event_id,
        run_id=record.run_id,
        event_kind=record.event_kind,
        payload=record.payload,
        created_at=record.created_at,
    )


def _display_event_response(record: DisplayEvent) -> DisplayEventResponse:
    return DisplayEventResponse(
        source_event_id=record.source_event_id,
        run_id=record.run_id,
        sequence=record.sequence,
        display_kind=record.display_kind,
        payload=record.payload,
        created_at=record.created_at,
    )


def _runtime_interaction_response(
    record: AgentInteractionRecord,
) -> RuntimeInteractionResponse:
    return RuntimeInteractionResponse(
        interaction_id=record.interaction_id,
        run_id=record.run_id,
        interaction_kind=record.interaction_kind,
        status=record.status,
        request_payload=record.request_payload,
        response_payload=record.response_payload,
        metadata=record.metadata,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
    )


def _metadata_datetime(
    metadata: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _visible_runtime_runs(
    auth_context: AuthContext | None,
) -> list[AgentRunRecord]:
    records: list[AgentRunRecord] = []
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
            records.extend(
                await _bindings.module().session_manager.list_runtime_runs(session_id)
            )
        except RuntimeError:
            continue
    return records


async def _get_visible_runtime_run(
    run_id: str,
    auth_context: AuthContext | None,
) -> AgentRunRecord:
    try:
        record = await _bindings.module().session_manager.load_runtime_run(run_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc

    try:
        await _get_visible_session(record.session_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Runtime run not found",
            ) from exc
        raise
    return record


@router.get("/runs/{run_id}", response_model=RuntimeRunResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_run(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeRunResponse:
    del request
    record = await _get_visible_runtime_run(run_id, auth_context)
    return _runtime_run_response(record)


@router.get(
    "/runs/{run_id}/interactions",
    response_model=RuntimeInteractionListResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_run_interactions(
    request: Request,
    run_id: str,
    status: str | None = Query(None, min_length=1, max_length=100),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionListResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        interactions = (
            await _bindings.module().session_manager.list_runtime_interactions(run_id)
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interactions not found",
        ) from exc
    if status is not None:
        interactions = [
            interaction for interaction in interactions if interaction.status == status
        ]
    return RuntimeInteractionListResponse(
        interactions=[
            _runtime_interaction_response(interaction) for interaction in interactions
        ]
    )


@router.get(
    "/runs/{run_id}/message-snapshot",
    response_model=RuntimeMessageSnapshotResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_message_snapshot(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeMessageSnapshotResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        record = await _bindings.module().session_manager.load_runtime_message_snapshot(
            run_id
        )
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime message snapshot not found",
        ) from exc
    return _runtime_message_snapshot_response(record)


@router.get("/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(1000, ge=1, le=1000),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeEventsResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        events = await _bindings.module().session_manager.replay_runtime_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime event not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc
    return RuntimeEventsResponse(
        run_id=run_id,
        events=[_runtime_event_response(event) for event in events],
    )


@router.get("/runs/{run_id}/display-events", response_model=DisplayEventsResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_display_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(1000, ge=1, le=1000),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> DisplayEventsResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        events = await _bindings.module().session_manager.replay_display_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime event not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc
    return DisplayEventsResponse(
        run_id=run_id,
        events=[_display_event_response(event) for event in events],
    )


@router.get("/interactions", response_model=RuntimeInteractionListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_runtime_interactions(
    request: Request,
    session_id: str | None = Query(None, min_length=1, max_length=100),
    run_id: str | None = Query(None, min_length=1, max_length=100),
    status: str | None = Query(None, min_length=1, max_length=100),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionListResponse:
    del request
    if run_id is not None:
        runs = [await _get_visible_runtime_run(run_id, auth_context)]
    elif session_id is not None:
        _ = await _get_visible_session(session_id, auth_context)
        runs = await _bindings.module().session_manager.list_runtime_runs(session_id)
    else:
        runs = await _visible_runtime_runs(auth_context)
    interactions: list[AgentInteractionRecord] = []
    for run in runs:
        if session_id is not None and run.session_id != session_id:
            continue
        try:
            interactions.extend(
                await _bindings.module().session_manager.list_runtime_interactions(
                    run.run_id
                )
            )
        except RuntimeError:
            continue
    if status is not None:
        interactions = [
            interaction for interaction in interactions if interaction.status == status
        ]
    interactions.sort(key=lambda interaction: interaction.created_at, reverse=True)
    return RuntimeInteractionListResponse(
        interactions=[
            _runtime_interaction_response(interaction) for interaction in interactions
        ]
    )


@router.get(
    "/interactions/{interaction_id}",
    response_model=RuntimeInteractionResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_interaction(
    request: Request,
    interaction_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionResponse:
    del request
    try:
        interaction = await _bindings.module().session_manager.load_runtime_interaction(
            interaction_id
        )
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interaction not found",
        ) from exc
    _ = await _get_visible_runtime_run(interaction.run_id, auth_context)
    return _runtime_interaction_response(interaction)


@router.post(
    "/interactions/{interaction_id}/resolve",
    response_model=RuntimeInteractionResponse,
)
@limiter.limit(RateLimits.APPROVE)
async def resolve_runtime_interaction(
    request: Request,
    interaction_id: str,
    body: ResolveInteractionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionResponse:
    del request
    try:
        interaction = await _bindings.module().session_manager.load_runtime_interaction(
            interaction_id
        )
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interaction not found",
        ) from exc
    _ = await _get_visible_runtime_run(interaction.run_id, auth_context)
    if interaction.interaction_kind != "approval":
        raise HTTPException(status_code=400, detail="Interaction is not an approval")
    if interaction.status != "pending":
        raise HTTPException(status_code=409, detail="Interaction is not pending")
    session_id = interaction.metadata.get("session_id")
    request_id = interaction.metadata.get("request_id")
    if not isinstance(session_id, str) or not isinstance(request_id, str):
        raise HTTPException(
            status_code=400,
            detail="Approval interaction metadata is incomplete",
        )
    try:
        approval = await _bindings.module().session_manager.submit_approval_response(
            session_id=session_id,
            request_id=request_id,
            approved=body.approved,
            feedback=body.feedback,
            scope=body.scope,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if approval is None:
        raise HTTPException(
            status_code=409,
            detail="Approval request is no longer pending",
        )
    resolved = await _bindings.module().session_manager.load_runtime_interaction(
        interaction_id
    )
    return _runtime_interaction_response(resolved)


__all__ = [
    "_display_event_response",
    "_get_visible_runtime_run",
    "_metadata_datetime",
    "_runtime_event_response",
    "_runtime_interaction_response",
    "_runtime_message_snapshot_response",
    "_runtime_run_response",
    "_visible_runtime_runs",
    "get_display_events",
    "get_runtime_events",
    "get_runtime_interaction",
    "get_runtime_message_snapshot",
    "get_runtime_run",
    "get_runtime_run_interactions",
    "list_runtime_interactions",
    "resolve_runtime_interaction",
]
