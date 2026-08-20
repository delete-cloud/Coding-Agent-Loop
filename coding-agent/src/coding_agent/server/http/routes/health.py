"""Liveness, readiness, and Prometheus metrics routes."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from coding_agent.observability import (
    prometheus_metrics_text,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    HealthResponse,
    ReadinessResponse,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.config import _prometheus_metrics_enabled
from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
@limiter.limit(RateLimits.HEALTH)
async def liveness_check(request: Request) -> HealthResponse:
    return HealthResponse(
        status="healthy",
        sessions=await _bindings.module().session_manager.count_sessions_async(),
        version="2.0.0",
    )


@router.get("/metrics", response_class=PlainTextResponse)
@limiter.limit(RateLimits.HEALTH)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    if not _prometheus_metrics_enabled():
        raise HTTPException(status_code=404, detail="Metrics endpoint disabled")
    return PlainTextResponse(
        prometheus_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/readyz", response_model=ReadinessResponse)
@limiter.limit(RateLimits.HEALTH)
async def readiness_check(request: Request, response: Response) -> ReadinessResponse:
    try:
        session_store_ok = bool(
            await _bindings.module().session_manager.check_health_async()
        )
    except Exception:
        logger.exception("Session store readiness check failed")
        session_store_ok = False

    try:
        rate_limiter_ok = bool(limiter._storage.check())
    except Exception:
        logger.exception("Rate limiter readiness check failed")
        rate_limiter_ok = False

    checks = {
        "session_store": "ok" if session_store_ok else "error",
        "rate_limiter": "ok" if rate_limiter_ok else "error",
    }
    ready = session_store_ok and rate_limiter_ok

    try:
        cloud_workspace_config = _bindings.module()._load_cloud_workspace_config()
        if cloud_workspace_config.get("enabled") is True:
            cloud_workspace_ok = bool(
                await _bindings.module().asyncio.to_thread(
                    _bindings.module().cloud_workspace_ready_from_config,
                    cloud_workspace_config,
                )
            )
            checks["cloud_workspace"] = "ok" if cloud_workspace_ok else "error"
            ready = ready and cloud_workspace_ok
    except Exception:
        logger.exception("Cloud workspace readiness check failed")
        checks["cloud_workspace"] = "error"
        ready = False

    if not ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        checks=checks,
    )


__all__ = [
    "liveness_check",
    "metrics_endpoint",
    "readiness_check",
]
