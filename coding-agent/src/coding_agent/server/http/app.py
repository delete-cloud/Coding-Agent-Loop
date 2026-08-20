"""FastAPI factory, lifespan wiring, CORS, rate-limit, and HTTP metrics."""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from coding_agent.observability import record_http_request_metric
from coding_agent.server.http.config import (
    _cors_allowed_origins,
    _prometheus_metrics_enabled,
    mount_webui_static_files,
)
from coding_agent.server.http.deps import _http_metrics_route_label
from coding_agent.server.http.lifecycle import lifespan
from coding_agent.server.http.routes import checkpoints as checkpoints_routes
from coding_agent.server.http.routes import console as console_routes
from coding_agent.server.http.routes import console_ops as console_ops_routes
from coding_agent.server.http.routes import health as health_routes
from coding_agent.server.http.routes import memory as memory_routes
from coding_agent.server.http.routes import oauth as oauth_routes
from coding_agent.server.http.routes import prompts as prompts_routes
from coding_agent.server.http.routes import providers as providers_routes
from coding_agent.server.http.routes import sse as sse_routes
from coding_agent.server.http.routes import publish as publish_routes
from coding_agent.server.http.routes import runtime as runtime_routes
from coding_agent.server.http.routes import session_result as session_result_routes
from coding_agent.server.http.routes import (
    session_workspace as session_workspace_routes,
)
from coding_agent.server.http.routes import sessions as sessions_routes
from coding_agent.server.http.routes import workers as workers_routes
from coding_agent.server.http.routes import workspaces as workspaces_routes
from coding_agent.server.rate_limit import limiter


class _HTTPMetricsASGIMiddleware:
    def __init__(self, wrapped_app: Any) -> None:
        self._wrapped_app = wrapped_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._wrapped_app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_with_metrics(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                message_status = message.get("status")
                if isinstance(message_status, int):
                    status_code = message_status
            await send(message)

        try:
            await self._wrapped_app(scope, receive, send_with_metrics)
        finally:
            if _prometheus_metrics_enabled():
                route = scope.get("route")
                route_label = getattr(route, "path", None)
                if not isinstance(route_label, str) or not route_label:
                    route_label = "unmatched"
                route_label = _http_metrics_route_label(route_label)
                record_http_request_metric(
                    method=cast(str, scope["method"]),
                    route=route_label,
                    status_code=status_code,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )


async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail=str(exc))


def create_app() -> FastAPI:
    application = FastAPI(title="Coding Agent HTTP API", lifespan=lifespan)
    application.state.limiter = limiter
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(_HTTPMetricsASGIMiddleware)
    application.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    for router in (
        console_routes.router,
        console_ops_routes.router,
        health_routes.router,
        providers_routes.router,
        sessions_routes.router,
        memory_routes.router,
        prompts_routes.router,
        sse_routes.router,
        workers_routes.router,
        workspaces_routes.router,
        runtime_routes.router,
        session_result_routes.router,
        session_workspace_routes.router,
        publish_routes.router,
        checkpoints_routes.router,
        oauth_routes.router,
    ):
        application.include_router(router)
    mount_webui_static_files(application)
    return application
