"""Developer console HTML routes for topics, schedules, bee, workspaces, and release."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from coding_agent.server.auth import AuthContext, auth_context_from_headers
from coding_agent.server.developer_console import (
    render_console_bee_page,
    render_console_release_page,
    render_console_schedules_page,
    render_console_topic_detail_page,
    render_console_topics_page,
    render_console_workspaces_page,
)
from coding_agent.server.http.console_bee import _console_bee_page
from coding_agent.server.http.console_run_meta import (
    _console_workspace_capability_summary,
    _console_workspace_summaries,
    _release_summary,
)
from coding_agent.server.http.console_topics import (
    _console_schedules_page,
    _console_topic_detail,
    _console_topic_summaries,
)
from coding_agent.server.http.deps import _require_admin_context

router = APIRouter()


@router.get("/console/topics", response_class=HTMLResponse)
async def console_topics(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(
        render_console_topics_page(await _console_topic_summaries(auth_context))
    )


@router.get("/console/topics/{topic_id}", response_class=HTMLResponse)
async def console_topic_detail(
    request: Request,
    topic_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    detail = await _console_topic_detail(topic_id, auth_context)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return HTMLResponse(render_console_topic_detail_page(detail))


@router.get("/console/schedules", response_class=HTMLResponse)
async def console_schedules(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(
        render_console_schedules_page(await _console_schedules_page(auth_context))
    )


@router.get("/console/bee", response_class=HTMLResponse)
async def console_bee(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_bee_page(await _console_bee_page(auth_context)))


@router.get("/console/workspaces", response_class=HTMLResponse)
async def console_workspaces(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    _require_admin_context(auth_context)
    return HTMLResponse(
        render_console_workspaces_page(
            await _console_workspace_summaries(),
            _console_workspace_capability_summary(),
        )
    )


@router.get("/console/release", response_class=HTMLResponse)
async def console_release(request: Request) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_release_page(await _release_summary()))


__all__ = [
    "console_bee",
    "console_release",
    "console_schedules",
    "console_topic_detail",
    "console_topics",
    "console_workspaces",
]
