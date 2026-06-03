from __future__ import annotations
import base64
import io
import os
import tarfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from coding_agent.runs import (
    CloudWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    RunTarget,
)
from coding_agent.server.http_server import app, session_manager
from coding_agent.server.rate_limit import limiter
from coding_agent.server.session_manager import Session
from coding_agent.server.stores.session_owner_store import SessionOwnerRecord
from coding_agent.core.config import settings
from httpx import ASGITransport, AsyncClient
import pytest


class FakeOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, SessionOwnerRecord] = {}

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            fencing_token=fencing_token,
        )
        return True

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        return self._owners.get(session_id)

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != current_fencing_token:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            fencing_token=new_fencing_token,
        )
        return True

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != fencing_token:
            return False
        del self._owners[session_id]
        return True


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


def _test_runtime_profile_config() -> dict[str, object]:
    return {
        "default_runtime_profile": "python-basic",
        "image_allowlist": ["python:3.11-slim"],
        "runtime_profiles": {
            "python-basic": {
                "provider": "docker",
                "image": "python:3.11-slim",
            }
        },
    }


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


@pytest.fixture
async def client_500() -> AsyncIterator[AsyncClient]:
    transport = cast(Any, ASGITransport)(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def owner_store() -> FakeOwnerStore:
    fake_owner_store = FakeOwnerStore()
    session_manager.configure_owner_leases(
        owner_store=fake_owner_store,
        owner_id="owner-a",
        fencing_token=1,
    )
    return fake_owner_store


def _register_cloud_session(session_id: str, binding: CloudWorkspaceRef) -> None:
    session_manager.register_session(
        Session(
            id=session_id,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            default_run_target=RunTarget(
                workspace=binding,
                executor=LocalDaemonExecutorRef(),
                isolation=IsolationPolicy(
                    kind="provider_sandbox",
                    network="provider_managed",
                    filesystem="provider_managed",
                    secrets="provider_managed",
                ),
            ),
            origin={
                "channel": "http",
                "placement_kind": "cloud_workspace",
                "workspace_source_kind": "docker",
            },
        )
    )


def _write_auth_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        "\n".join(
            [
                "[agent]",
                'name = "test-agent"',
                'model = "test-model"',
                'provider = "openai"',
                "",
                "[server]",
                'bearer_token = "user-token"',
                'admin_bearer_token = "admin-token"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _configure_workspace_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    max_workspace_age_seconds: int | None = None,
) -> None:
    config: dict[str, object] = {
        "enabled": True,
        "provider": "docker",
        "workspace_root": str(tmp_path),
        "container_name_prefix": "agent-",
        **_test_runtime_profile_config(),
    }
    if max_workspace_age_seconds is not None:
        config["max_workspace_age_seconds"] = max_workspace_age_seconds
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: config,
    )


def _configure_admin_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path)))
    monkeypatch.setattr(settings, "http_api_key", None)


async def test_create_session_provisions_docker_workspace_from_snapshot(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
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
    assert isinstance(session.default_run_target.workspace, CloudWorkspaceRef)
    workspace_root = tmp_path / session.default_run_target.workspace.workspace_id
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "uploaded"
    assert (workspace_root / "src" / "app.py").read_text(
        encoding="utf-8"
    ) == "print('hi')\n"


async def test_workspaces_list_requires_admin_scope(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_admin_auth(monkeypatch, tmp_path)
    _configure_workspace_server(monkeypatch, tmp_path)

    response = await client.get(
        "/workspaces",
        headers={"Authorization": "Bearer user-token"},
    )

    assert response.status_code == 403


async def test_workspaces_list_ignores_unrelated_directories(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_admin_auth(monkeypatch, tmp_path)
    _configure_workspace_server(monkeypatch, tmp_path)
    (tmp_path / "ws-visible").mkdir()
    (tmp_path / "not-a-workspace").mkdir()

    response = await client.get(
        "/workspaces",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert [item["workspace_id"] for item in response.json()["workspaces"]] == [
        "ws-visible"
    ]


async def test_workspace_cleanup_requires_admin_scope(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_admin_auth(monkeypatch, tmp_path)
    _configure_workspace_server(monkeypatch, tmp_path)
    (tmp_path / "ws-cleanup").mkdir()

    response = await client.delete(
        "/workspaces/ws-cleanup",
        headers={"Authorization": "Bearer user-token"},
    )

    assert response.status_code == 403


async def test_workspace_cleanup_skips_active_cloud_sessions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_admin_auth(monkeypatch, tmp_path)
    _configure_workspace_server(monkeypatch, tmp_path, max_workspace_age_seconds=1)
    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-active/workspace",
        workspace_id="ws-active",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir()
    os.utime(workspace_root, (1, 1))
    _register_cloud_session("sess-active", binding)

    response = await client.post(
        "/workspaces/gc",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json()["cleaned_count"] == 0
    assert workspace_root.exists()


async def test_workspace_archive_manifest_reports_counts_bytes_and_changes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_workspace_server(monkeypatch, tmp_path)
    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-manifest/workspace",
        workspace_id="ws-manifest",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir()
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    (workspace_root / "nested").mkdir()
    (workspace_root / "nested" / "data.json").write_text("{}\n", encoding="utf-8")
    (workspace_root / ".git").mkdir()
    (workspace_root / ".git" / "config").write_text("ignored", encoding="utf-8")
    (workspace_root / "__pycache__").mkdir()
    (workspace_root / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"cache")
    _register_cloud_session("sess-manifest", binding)

    response = await client.get("/sessions/sess-manifest/workspace/archive/manifest")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace_id"] == "ws-manifest"
    assert data["session_id"] == "sess-manifest"
    assert data["format"] == "tar.gz"
    assert data["file_count"] == 2
    assert data["total_bytes"] == len("remote result") + len("{}\n")
    assert data["changed_files"] == ["nested/data.json", "result.txt"]
    assert data["deleted_files"] == []
    assert data["excluded_files"] == [".git", "__pycache__"]
    assert len(data["archive_sha256"]) == 64


async def test_session_workspace_archive_endpoint_keeps_compatibility_alias(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_workspace_server(monkeypatch, tmp_path)
    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-alias/workspace",
        workspace_id="ws-alias",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir()
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    _register_cloud_session("sess-alias", binding)

    canonical = await client.get("/sessions/sess-alias/workspace/archive")
    alias = await client.get("/sessions/sess-alias/workspace")

    assert canonical.status_code == 200
    assert alias.status_code == 200
    assert canonical.json() == alias.json()


async def test_get_workspace_archive_returns_cloud_workspace_snapshot(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-transfer/workspace",
        workspace_id="ws-transfer",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    (workspace_root / "nested").mkdir()
    (workspace_root / "nested" / "data.json").write_text(
        '{"ok": true}\n', encoding="utf-8"
    )
    _register_cloud_session("sess-transfer", binding)

    response = await client.get("/sessions/sess-transfer/workspace")

    assert response.status_code == 200
    assert response.json()["format"] == "tar.gz"
    assert _read_workspace_archive(response.json()["archive_base64"]) == {
        "nested/data.json": '{"ok": true}\n',
        "result.txt": "remote result",
    }


async def test_get_workspace_archive_returns_409_for_active_turn(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-busy/workspace",
        workspace_id="ws-busy",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    _register_cloud_session("sess-busy", binding)
    session = session_manager.get_session("sess-busy")
    session.turn_in_progress = True

    response = await client.get("/sessions/sess-busy/workspace")

    assert response.status_code == 409
    assert response.json()["detail"] == "turn already in progress"


async def test_get_workspace_archive_returns_409_for_stale_owner(
    client: AsyncClient, owner_store: FakeOwnerStore, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-stale/workspace",
        workspace_id="ws-stale",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    _register_cloud_session("sess-stale", binding)
    owner_store._owners["sess-stale"] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=2,
    )

    response = await client.get("/sessions/sess-stale/workspace")

    assert response.status_code == 409
    assert response.json()["detail"] == "stale owner or fencing token rejected"


async def test_get_workspace_archive_rejects_owner_change_during_export(
    client: AsyncClient, owner_store: FakeOwnerStore, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-race/workspace",
        workspace_id="ws-race",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "result.txt").write_text("remote result", encoding="utf-8")
    _register_cloud_session("sess-race", binding)
    owner_store._owners["sess-race"] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    monkeypatch.setattr(
        "coding_agent.server.http_server.export_workspace_archive_from_config",
        lambda config, binding: (
            owner_store._owners.__setitem__(
                "sess-race",
                SessionOwnerRecord(
                    owner_id="owner-b",
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fencing_token=2,
                ),
            )
            or "archive-base64"
        ),
    )

    response = await client.get("/sessions/sess-race/workspace")

    assert response.status_code == 409
    assert response.json()["detail"] == "stale owner or fencing token rejected"


async def test_get_workspace_archive_returns_500_for_unexpected_runtime_error(
    client_500: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-fail/workspace",
        workspace_id="ws-fail",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    _register_cloud_session("sess-fail", binding)

    monkeypatch.setattr(
        "coding_agent.server.http_server.export_workspace_archive_from_config",
        lambda config, binding: (_ for _ in ()).throw(
            RuntimeError("docker export failed")
        ),
    )

    response = await client_500.get("/sessions/sess-fail/workspace")

    assert response.status_code == 500


async def test_create_session_rejects_snapshot_archive_larger_than_limit(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    response = await client.post(
        "/sessions",
        json={
            "workspace_source": {
                "kind": "docker",
                "snapshot_archive_base64": base64.b64encode(
                    b"a" * (8 * 1024 * 1024 + 1)
                ).decode("ascii"),
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "workspace archive exceeds 8 MiB limit"


async def test_get_workspace_archive_returns_400_for_oversized_workspace_export(
    client: AsyncClient, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
        lambda provider_config, binding: None,
    )
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: {
            "enabled": True,
            "provider": "docker",
            "workspace_root": str(tmp_path),
            "container_name_prefix": "agent-",
            **_test_runtime_profile_config(),
        },
    )

    binding = CloudWorkspaceRef(
        workspace_url="docker://agent-ws-large/workspace",
        workspace_id="ws-large",
    )
    workspace_root = tmp_path / binding.workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "large.bin").write_bytes(b"a" * (8 * 1024 * 1024 + 1))
    _register_cloud_session("sess-large", binding)

    response = await client.get("/sessions/sess-large/workspace")

    assert response.status_code == 400
    assert response.json()["detail"] == "workspace archive exceeds 8 MiB limit"
