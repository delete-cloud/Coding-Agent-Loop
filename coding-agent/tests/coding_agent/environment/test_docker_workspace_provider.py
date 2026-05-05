from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent.environment import DockerCloudWorkspaceClient, cloud_client_factory_from_config
from coding_agent.ui.execution_binding import CloudWorkspaceBinding


def test_docker_workspace_provider_builds_client_from_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    (workspace_root / "ws-123").mkdir()

    factory = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
            "container_name_prefix": "agent-",
            "docker_binary": "/usr/bin/docker",
            "env_allowlist": ["SAFE_VAR"],
        }
    )

    client = factory(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    assert isinstance(client, DockerCloudWorkspaceClient)
    assert client.workspace_id == "ws-123"
    assert client.workspace_url == "https://workspace.example.com"
    assert client.default_cwd == "/workspace"


def test_docker_cloud_client_maps_file_tools_to_remote_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    nested = target_root / "pkg"
    nested.mkdir(parents=True)
    note = nested / "note.txt"
    _ = note.write_text("hello docker\n", encoding="utf-8")
    other = target_root / "other.txt"
    _ = other.write_text("docker hello\n", encoding="utf-8")

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    assert client.read_file("pkg/note.txt") == "hello docker\n"
    _ = client.write_file("pkg/new.txt", "new file")
    assert (nested / "new.txt").read_text(encoding="utf-8") == "new file"
    _ = client.replace_file("pkg/new.txt", "new", "updated")
    assert (nested / "new.txt").read_text(encoding="utf-8") == "updated file"
    assert client.glob_files("*.txt", "/workspace/pkg") == [
        "/workspace/pkg/new.txt",
        "/workspace/pkg/note.txt",
    ]
    assert client.grep_search("hello", "/workspace", "*.txt") == [
        "/workspace/other.txt:1:docker hello",
        "/workspace/pkg/note.txt:1:hello docker",
    ]


def test_docker_cloud_client_applies_patch_with_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)
    target = target_root / "hello.py"
    _ = target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    payload = client.apply_patch(
        "hello.py",
        "@@ -1,2 +1,2 @@\n def greet():\n-    return 'hello'\n+    return 'hello docker'\n",
    )

    assert payload["success"] is True
    assert payload["changed"] is True
    assert "hello docker" in target.read_text(encoding="utf-8")


def test_docker_cloud_client_runs_shell_in_workspace_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    (target_root / "pkg").mkdir(parents=True)

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
            "container_name_prefix": "agent-",
            "docker_binary": "/usr/bin/docker",
            "env_allowlist": ["SAFE_VAR"],
            "exec_user": "1000:1000",
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = client.run_command(
        "python -V",
        cwd="/workspace/pkg",
        env={"SAFE_VAR": "1"},
        timeout=9,
    )

    assert result.stdout == "ok"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert captured["command"] == [
        "/usr/bin/docker",
        "exec",
        "--workdir",
        "/workspace/pkg",
        "--user",
        "1000:1000",
        "-e",
        "SAFE_VAR=1",
        "agent-ws-123",
        "/bin/sh",
        "-c",
        "python -V",
    ]
    assert captured["kwargs"] == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "timeout": 9,
        "env": None,
    }


def test_docker_cloud_client_rejects_disallowed_env_names(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
            "env_allowlist": ["SAFE_VAR"],
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    with pytest.raises(
        ValueError,
        match="environment variable is not allowed for docker workspace: OTHER_VAR",
    ):
        _ = client.run_command(
            "python -V",
            cwd="/workspace",
            env={"OTHER_VAR": "1"},
            timeout=5,
        )


def test_docker_cloud_client_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    with pytest.raises(ValueError, match="Path is outside docker workspace: /etc"):
        _ = client.read_file("/etc/passwd")


def test_docker_cloud_client_rejects_symlink_escape_paths(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    secret = outside_root / "secret.txt"
    _ = secret.write_text("top secret\n", encoding="utf-8")
    escape_dir = target_root / "escape"
    escape_dir.symlink_to(outside_root, target_is_directory=True)

    client = cloud_client_factory_from_config(
        {
            "provider": "docker",
            "workspace_root": str(workspace_root),
        }
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape/secret\.txt",
    ):
        _ = client.read_file("escape/secret.txt")

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape/new\.txt",
    ):
        _ = client.write_file("escape/new.txt", "blocked")

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape/secret\.txt",
    ):
        _ = client.replace_file("escape/secret.txt", "top", "public")

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape/secret\.txt",
    ):
        _ = client.apply_patch(
            "escape/secret.txt",
            "@@ -1 +1 @@\n-top secret\n+public\n",
        )

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape",
    ):
        _ = client.glob_files("*.txt", "/workspace/escape")

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape",
    ):
        _ = client.grep_search("secret", "/workspace/escape", "*.txt")

    with pytest.raises(
        ValueError,
        match=r"Path is outside docker workspace: /workspace/escape",
    ):
        _ = client.run_command("python -V", cwd="/workspace/escape", env=None, timeout=5)


def test_docker_workspace_provider_requires_workspace_root() -> None:
    with pytest.raises(
        ValueError,
        match="cloud_workspace.workspace_root is required for provider=docker",
    ):
        _ = cloud_client_factory_from_config({"provider": "docker"})
