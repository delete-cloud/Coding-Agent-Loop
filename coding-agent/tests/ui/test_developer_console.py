from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from coding_agent.ui.http_server import app


CONSOLE_ROUTES = (
    "/console",
    "/console/sessions",
    "/console/runs",
    "/console/interactions",
    "/console/tape",
    "/console/context",
    "/console/memory",
    "/console/actions",
    "/console/observability",
    "/console/release",
)

NAV_LINKS = {
    "Sessions": "/console/sessions",
    "Runs": "/console/runs",
    "HITL / Interactions": "/console/interactions",
    "Tape": "/console/tape",
    "Context": "/console/context",
    "Memory": "/console/memory",
    "Actions / Validation": "/console/actions",
    "Observability": "/console/observability",
    "Release / Health": "/console/release",
}

FORBIDDEN_RENDERED_TEXT = (
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "raw prompt",
    "raw message",
    "command_output",
    "stdout",
    "stderr",
    "env",
)


@pytest.mark.asyncio
async def test_console_shell_routes_render_navigation_without_secrets() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route in CONSOLE_ROUTES:
            response = await client.get(
                route,
                params={"secret": "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"},
            )

            assert response.status_code == 200, route
            assert response.headers["content-type"].startswith("text/html")
            assert "<!doctype html>" in response.text.casefold()
            assert "Developer Console" in response.text
            for label, href in NAV_LINKS.items():
                assert label in response.text
                assert f'href="{href}"' in response.text
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text


@pytest.mark.asyncio
async def test_console_placeholder_pages_render_empty_states() -> None:
    expected = {
        "/console": "Console Overview",
        "/console/sessions": "Sessions",
        "/console/runs": "Runs",
        "/console/interactions": "HITL / Interactions",
        "/console/tape": "Tape",
        "/console/context": "Context",
        "/console/memory": "Memory",
        "/console/actions": "Actions / Validation",
        "/console/observability": "Observability",
        "/console/release": "Release / Health",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for route, title in expected.items():
            response = await client.get(route)

            assert response.status_code == 200, route
            assert f"<h1>{title}</h1>" in response.text
            assert "No data loaded yet." in response.text
