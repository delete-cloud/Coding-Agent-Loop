from __future__ import annotations

import base64
import io
import tarfile
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from coding_agent.ui.execution_binding import CloudWorkspaceBinding
from coding_agent.ui.http_server import app, session_manager
from coding_agent.ui.rate_limit import limiter
from coding_agent.ui.session_manager import Session
from httpx import ASGITransport, AsyncClient
import pytest


def _build_workspace_archive(files: dict[str, str]) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, content in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _read_workspace_archive(archive_base64: str) -> dict[str, str]:
    decoded = base64.b64decode(archive_base64)
    with tarfile.open(fileobj=io.BytesIO(decoded), mode="r:gz") as archive:
        extracted: dict[str, str] = {}
        for member in archive.getmembers():
            if not member.isfile():
                continue
            file_obj = archive.extractfile(member)
            assert file_obj is not None
            extracted[member.name] = file_obj.read().decode("utf-8")
    return extracted


@pytest.fixture(autouse=True)
async def clear_sessions() -> AsyncIterator[None]:
    session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    session_manager.clear_sessions()
    limiter.reset()
    yield
    session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    session_manager.clear_sessions()
    limiter.reset()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _register_cloud_session(session_id: str, binding: CloudWorkspaceBinding) -> None:
    session_manager.register_session(
        Session(
            id=session_id,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            execution_binding=binding,
            origin={
                "channel": "http",
                "binding_kind": "cloud",
                "workspace_source_kind": "docker",
            },
        )
    )


async def test_create_session_provisions_docker_workspace_from_snapshot(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.ui.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
        },
    )

    response = await client.post(
        "/sessions",
        json={
            "workspace_source": {
                "kind": "docker",
                "snapshot_archive_base64": _build_workspace_archive(
                    {"README.md": "uploaded", "src/app.py": "print('hi')\n"}
                ),
            }
        },
    )

    assert response.status_code == 200
    session = session_manager.get_session(response.json()["session_id"])
    assert isinstance(session.execution_binding, CloudWorkspaceBinding)
    workspace_root = tmp_path / session.execution_binding.workspace_id
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "uploaded"
    assert (workspace_root / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"


async def test_get_workspace_archive_returns_cloud_workspace_snapshot(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.ui.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
        },
    )

    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-transfer/workspace",
        workspace_id="ws-transfer",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    (workspace_root / "nested").mkdir()
    (workspace_root / "nested" / "data.json").write_text('{"ok": true}\n', encoding="utf-8")
    _register_cloud_session("sess-transfer", binding)

    response = await client.get("/sessions/sess-transfer/workspace")

    assert response.status_code == 200
    assert response.json()["format"] == "tar.gz"
    assert _read_workspace_archive(response.json()["archive_base64"]) == {
        "nested/data.json": '{"ok": true}\n',
        "result.txt": "remote result",
    }
