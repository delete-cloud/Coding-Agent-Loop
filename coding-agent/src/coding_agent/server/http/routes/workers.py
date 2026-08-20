"""Retired worker/executor 410 routes and worker status views."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, HTTPException, Request

from coding_agent.stores.runtime_store import (
    AgentRunRecord,
)
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
    verify_api_key,
)
from coding_agent.runs import (
    ExternalWorkerExecutorRef,
    LocalAttachedExecutorRef,
    REMOTE_LOOP_OWNERSHIP_RETIRED,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    RuntimeEventsResponse,
    RuntimeRunResponse,
    ExecutorListResponse,
    WorkerClaimRequest,
    WorkerClaimResponse,
    WorkerApprovalRequest,
    WorkerApprovalResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerListResponse,
    WorkerRunCompleteRequest,
    WorkerRuntimeEventsRequest,
    WorkerStatusResponse,
)

from coding_agent.server.http.constants import (
    WORKER_OFFLINE_AFTER_SECONDS,
    WORKER_STALE_AFTER_SECONDS,
)
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.routes.runtime import (
    _metadata_datetime,
    _visible_runtime_runs,
)
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


def _remote_loop_gone() -> HTTPException:
    return HTTPException(status_code=410, detail=REMOTE_LOOP_OWNERSHIP_RETIRED)


def _session_uses_retired_remote_loop(session: object) -> bool:
    target = getattr(session, "default_run_target", None)
    executor = getattr(target, "executor", None)
    return isinstance(executor, (ExternalWorkerExecutorRef, LocalAttachedExecutorRef))


@router.post("/worker/runs/claim", response_model=WorkerClaimResponse)
@router.post("/executor/runs/claim", response_model=WorkerClaimResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def claim_worker_run(
    request: Request,
    body: WorkerClaimRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerClaimResponse:
    del request, body, api_key
    raise _remote_loop_gone()


@router.post("/worker/runs/{run_id}/heartbeat", response_model=WorkerHeartbeatResponse)
@router.post(
    "/executor/runs/{run_id}/heartbeat", response_model=WorkerHeartbeatResponse
)
@limiter.limit(RateLimits.SEND_PROMPT)
async def heartbeat_worker_run(
    request: Request,
    run_id: str,
    body: WorkerHeartbeatRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerHeartbeatResponse:
    del request, run_id, body, api_key
    raise _remote_loop_gone()


@router.post("/worker/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@router.post("/executor/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def append_worker_run_events(
    request: Request,
    run_id: str,
    body: WorkerRuntimeEventsRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeEventsResponse:
    del request, run_id, body, api_key
    raise _remote_loop_gone()


@router.post("/worker/runs/{run_id}/approval", response_model=WorkerApprovalResponse)
@router.post("/executor/runs/{run_id}/approval", response_model=WorkerApprovalResponse)
@limiter.limit(RateLimits.APPROVE)
async def request_worker_approval(
    request: Request,
    run_id: str,
    body: WorkerApprovalRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerApprovalResponse:
    del request, run_id, body, api_key
    raise _remote_loop_gone()


@router.post("/worker/runs/{run_id}/complete", response_model=RuntimeRunResponse)
@router.post("/executor/runs/{run_id}/complete", response_model=RuntimeRunResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def complete_worker_run(
    request: Request,
    run_id: str,
    body: WorkerRunCompleteRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeRunResponse:
    del request, run_id, body, api_key
    raise _remote_loop_gone()


def _worker_status_from_runs(
    worker_id: str,
    runs: Iterable[AgentRunRecord],
) -> WorkerStatusResponse:
    worker_runs = [run for run in runs if run.metadata.get("worker_id") == worker_id]
    if not worker_runs:
        raise KeyError(f"worker not found: {worker_id}")
    worker_runs.sort(key=lambda run: (run.started_at, run.run_id))
    latest = worker_runs[-1]
    now = datetime.now(UTC)
    active_runs = [
        run for run in worker_runs if run.status in {"claimed", "running", "cancelling"}
    ]
    active_runs.sort(key=lambda run: (run.started_at, run.run_id))
    current = active_runs[-1] if active_runs else None
    source = current or latest
    metadata = source.metadata
    lease_expires_at = _metadata_datetime(metadata, "lease_expires_at")
    last_seen_at = (
        _metadata_datetime(metadata, "last_heartbeat_at")
        or _metadata_datetime(metadata, "claimed_at")
        or _metadata_datetime(metadata, "finalized_at")
        or latest.ended_at
        or latest.started_at
    )
    status: Literal["idle", "running", "stale", "offline"]
    last_seen_age_seconds = (now - last_seen_at).total_seconds()
    if current is None and last_seen_age_seconds > float(WORKER_OFFLINE_AFTER_SECONDS):
        status = "offline"
    elif current is None:
        status = "idle"
    elif lease_expires_at is not None and lease_expires_at <= now:
        status = "stale"
    elif lease_expires_at is None and last_seen_age_seconds > float(
        WORKER_OFFLINE_AFTER_SECONDS
    ):
        status = "offline"
    elif lease_expires_at is None and last_seen_age_seconds > float(
        WORKER_STALE_AFTER_SECONDS
    ):
        status = "stale"
    else:
        status = "running"
    workspace_ref = metadata.get("workspace_ref")
    capabilities = metadata.get("capabilities")
    workspace_sync = metadata.get("workspace_sync")
    process_id = metadata.get("process_id")
    return WorkerStatusResponse(
        worker_id=worker_id,
        executor_id=worker_id,
        status=status,
        executor_kind=(
            metadata.get("executor_kind")
            if isinstance(metadata.get("executor_kind"), str)
            else None
        ),
        worker_pool=(
            metadata.get("worker_pool")
            if isinstance(metadata.get("worker_pool"), str)
            else None
        ),
        worker_instance_id=(
            metadata.get("worker_instance_id")
            if isinstance(metadata.get("worker_instance_id"), str)
            else None
        ),
        process_id=process_id if isinstance(process_id, int) else None,
        capabilities=capabilities if isinstance(capabilities, dict) else None,
        workspace_ref=workspace_ref if isinstance(workspace_ref, dict) else None,
        workspace_sync=workspace_sync if isinstance(workspace_sync, dict) else None,
        current_run_id=current.run_id if current is not None else None,
        current_session_id=current.session_id if current is not None else None,
        last_run_id=latest.run_id,
        last_session_id=latest.session_id,
        last_seen_at=last_seen_at,
        lease_expires_at=lease_expires_at,
    )


def _worker_statuses_from_runs(
    runs: Iterable[AgentRunRecord],
) -> list[WorkerStatusResponse]:
    run_list = list(runs)
    worker_ids = {
        worker_id
        for run in run_list
        if isinstance(worker_id := run.metadata.get("worker_id"), str) and worker_id
    }
    return sorted(
        (_worker_status_from_runs(worker_id, run_list) for worker_id in worker_ids),
        key=lambda worker: worker.worker_id,
    )


@router.get("/workers", response_model=WorkerListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_workers(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerListResponse:
    del request
    return WorkerListResponse(
        workers=_worker_statuses_from_runs(await _visible_runtime_runs(auth_context))
    )


@router.get("/executors", response_model=ExecutorListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_executors(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> ExecutorListResponse:
    del request
    return ExecutorListResponse(
        executors=_worker_statuses_from_runs(await _visible_runtime_runs(auth_context))
    )


@router.get("/workers/{worker_id}", response_model=WorkerStatusResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_worker_status(
    request: Request,
    worker_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerStatusResponse:
    del request
    try:
        return _worker_status_from_runs(
            worker_id,
            await _visible_runtime_runs(auth_context),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Worker not found") from exc


@router.get("/executors/{executor_id}", response_model=WorkerStatusResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_executor_status(
    request: Request,
    executor_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerStatusResponse:
    del request
    try:
        return _worker_status_from_runs(
            executor_id,
            await _visible_runtime_runs(auth_context),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Executor not found") from exc


__all__ = [
    "_remote_loop_gone",
    "_session_uses_retired_remote_loop",
    "_worker_status_from_runs",
    "_worker_statuses_from_runs",
    "append_worker_run_events",
    "claim_worker_run",
    "complete_worker_run",
    "get_executor_status",
    "get_worker_status",
    "heartbeat_worker_run",
    "list_executors",
    "list_workers",
    "request_worker_approval",
]
