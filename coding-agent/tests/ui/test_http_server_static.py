from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import coding_agent.server.http_server as http_server


@pytest.mark.asyncio
async def test_webui_static_mount_serves_index_after_api_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>webui</title>", "utf-8")

    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    http_server.mount_webui_static_files(app, str(dist))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        root = await client.get("/")
        health = await client.get("/healthz")

    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert "<title>webui</title>" in root.text
    assert health.status_code == 200
    assert health.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_webui_static_mount_is_disabled_without_dist_dir() -> None:
    app = FastAPI()
    http_server.mount_webui_static_files(app, None)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 404


def test_default_app_root_is_404_without_webui_dist_dir() -> None:
    env = os.environ.copy()
    env.pop("WEBUI_DIST_DIR", None)
    script = """
import asyncio

from httpx import ASGITransport, AsyncClient

from coding_agent.server import http_server


async def main() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_server.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")
    assert response.status_code == 404, response.status_code


asyncio.run(main())
"""

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


def test_cors_allowed_origins_defaults_to_development_wildcard() -> None:
    assert http_server._cors_allowed_origins({}) == ["*"]


def test_cors_allowed_origins_reads_comma_separated_whitelist() -> None:
    assert http_server._cors_allowed_origins(
        {
            "CODING_AGENT_CORS_ORIGINS": "https://agent.example.com, http://localhost:5173 "
        }
    ) == ["https://agent.example.com", "http://localhost:5173"]


def test_cors_allowed_origins_rejects_empty_explicit_whitelist() -> None:
    with pytest.raises(ValueError, match="CODING_AGENT_CORS_ORIGINS"):
        http_server._cors_allowed_origins({"CODING_AGENT_CORS_ORIGINS": " , "})
