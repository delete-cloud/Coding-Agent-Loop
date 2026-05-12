from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from coding_agent.environment import (
    DockerCloudWorkspaceClient,
    cleanup_cloud_binding_from_config,
    cleanup_stale_cloud_workspaces_from_config,
    cloud_client_factory_from_config,
    cloud_workspace_ready_from_config,
    provision_cloud_binding_from_config,
)
from coding_agent.ui.execution_binding import CloudWorkspaceBinding
from coding_agent.workspace_archive import create_workspace_archive_base64


TIMEOUT_SENTINEL_PREFIX = "__CODING_AGENT_DOCKER_TIMEOUT__:"


def _docker_config(overrides: dict[str, object] | None = None) -> dict[str, object]:
    config: dict[str, object] = {
        "provider": "docker",
        "default_runtime_profile": "python-basic",
        "image_allowlist": ["python:3.11-slim"],
        "runtime_profiles": {
            "python-basic": {
                "provider": "docker",
                "image": "python:3.11-slim",
            }
        },
    }
    if overrides is not None:
        config.update(overrides)
    return config


def _extract_timeout_sentinel(command: list[str]) -> str:
    for arg in command:
        match = re.search(r"__CODING_AGENT_DOCKER_TIMEOUT__:[0-9a-f]+", arg)
        if match is not None:
            return match.group(0)
    raise AssertionError(f"timeout sentinel not found in command: {command}")


def test_docker_workspace_provider_builds_client_from_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    (workspace_root / "ws-123").mkdir()

    factory = cloud_client_factory_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
                "env_allowlist": ["SAFE_VAR"],
            }
        )
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


def test_docker_cloud_client_maps_file_tools_to_remote_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    nested = target_root / "pkg"
    nested.mkdir(parents=True)
    note = nested / "note.txt"
    _ = note.write_text("hello docker\n", encoding="utf-8")
    other = target_root / "other.txt"
    _ = other.write_text("docker hello\n", encoding="utf-8")

    client = cloud_client_factory_from_config(
        _docker_config({"workspace_root": str(workspace_root)})
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
        _docker_config({"workspace_root": str(workspace_root)})
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
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
                "env_allowlist": ["SAFE_VAR"],
                "exec_user": "1000:1000",
            }
        )
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured_command[:] = command
        captured_kwargs.clear()
        captured_kwargs.update(kwargs)
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
    assert captured_command[:17] == [
        "/usr/bin/docker",
        "exec",
        "--workdir",
        "/workspace/pkg",
        "--user",
        "1000:1000",
        "-e",
        "SAFE_VAR=1",
        "agent-ws-123",
        "timeout",
        "-s",
        "TERM",
        "-k",
        "2s",
        "9s",
        "/bin/sh",
        "-c",
    ]
    timeout_wrapper = captured_command[17]
    child_wrapper = captured_command[19]
    assert TIMEOUT_SENTINEL_PREFIX in timeout_wrapper
    assert "trap _coding_agent_timeout TERM" in timeout_wrapper
    assert 'setsid /bin/sh -c "$1" sh "$pidfile" "$2" &' in timeout_wrapper
    assert 'printf "%s\\n" "$$" > "$1"' in child_wrapper
    assert 'exec /bin/sh -c "$2"' in child_wrapper
    assert captured_command[18:] == ["sh", child_wrapper, "python -V"]
    assert captured_kwargs == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "timeout": 12,
        "env": None,
    }


def test_docker_cloud_client_raises_timeout_when_container_command_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        _docker_config({"workspace_root": str(workspace_root)})
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        timeout_sentinel = _extract_timeout_sentinel(command)
        return subprocess.CompletedProcess(
            command,
            124,
            stdout="",
            stderr=f"{timeout_sentinel}\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TimeoutError, match="docker exec command timed out after 1s"):
        _ = client.run_command("sleep 10", cwd="/workspace", env=None, timeout=1)


def test_docker_cloud_client_preserves_stderr_with_nonmatching_timeout_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        _docker_config({"workspace_root": str(workspace_root)})
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    stderr = f"{TIMEOUT_SENTINEL_PREFIX}not-current-run\n"

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _ = command
        _ = kwargs
        return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = client.run_command("exit 1", cwd="/workspace", env=None, timeout=1)

    assert result.exit_code == 1
    assert result.stderr == stderr


@pytest.mark.parametrize("exit_code", [124, 137])
def test_docker_cloud_client_preserves_legitimate_exit_codes(
    exit_code: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        _docker_config({"workspace_root": str(workspace_root)})
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _ = kwargs
        return subprocess.CompletedProcess(command, exit_code, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = client.run_command("exit 0", cwd="/workspace", env=None, timeout=1)

    assert result.exit_code == exit_code


def test_docker_cloud_client_timeout_contract_blocks_post_timeout_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)
    marker = target_root / "late-write.txt"

    client = cloud_client_factory_from_config(
        _docker_config({"workspace_root": str(workspace_root)})
    )(
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    cleanup_called = threading.Event()
    late_write_checked = threading.Event()
    observed_timeouts: list[object] = []

    def maybe_late_write() -> None:
        cleanup_happened = cleanup_called.wait(timeout=0.2)
        if not cleanup_happened:
            _ = marker.write_text("late write\n", encoding="utf-8")
        late_write_checked.set()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        local_timeout = kwargs.get("timeout")
        observed_timeouts.append(local_timeout)
        if local_timeout == 4:
            threading.Thread(target=maybe_late_write, daemon=True).start()
            raise subprocess.TimeoutExpired(command, timeout=4)
        assert local_timeout == 3
        cleanup_called.set()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TimeoutError, match="docker exec command timed out after 1s"):
        _ = client.run_command("sleep 10", cwd="/workspace", env=None, timeout=1)

    assert cleanup_called.wait(timeout=0.2)
    assert late_write_checked.wait(timeout=0.2)
    assert observed_timeouts == [4, 3]
    assert not marker.exists()


def test_docker_cloud_client_rejects_disallowed_env_names(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    target_root = workspace_root / "ws-123"
    target_root.mkdir(parents=True)

    client = cloud_client_factory_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "env_allowlist": ["SAFE_VAR"],
            }
        )
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
        _docker_config({"workspace_root": str(workspace_root)})
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
        _docker_config({"workspace_root": str(workspace_root)})
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
        _ = client.run_command(
            "python -V", cwd="/workspace/escape", env=None, timeout=5
        )


def test_docker_workspace_provider_requires_workspace_root() -> None:
    with pytest.raises(
        ValueError,
        match="cloud_workspace.workspace_root is required for provider=docker",
    ):
        _ = cloud_client_factory_from_config(_docker_config())


def test_docker_workspace_provider_readiness_checks_docker_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured_command[:] = command
        captured_kwargs.clear()
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            command, 0, stdout="Docker version 1.0\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        cloud_workspace_ready_from_config(
            _docker_config(
                {
                    "workspace_root": "/srv/workspaces",
                    "docker_binary": "/usr/bin/docker",
                }
            )
        )
        is True
    )
    assert captured_command == ["/usr/bin/docker", "info", "--format", "{{json .}}"]
    assert captured_kwargs == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "env": None,
        "check": False,
    }


def test_docker_workspace_provider_readiness_returns_false_when_docker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="daemon unavailable"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        cloud_workspace_ready_from_config(
            _docker_config({"workspace_root": "/srv/workspaces"})
        )
        is False
    )


def test_docker_workspace_provider_readiness_returns_false_when_docker_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise OSError("docker missing")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        cloud_workspace_ready_from_config(
            _docker_config({"workspace_root": "/srv/workspaces"})
        )
        is False
    )


@pytest.mark.parametrize("root", ["/", "//", "/./", "/workspace/.."])
def test_docker_workspace_provider_rejects_root_container_workspace_root(
    root: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"cloud_workspace\.container_workspace_root must not resolve to /",
    ):
        _ = cloud_client_factory_from_config(
            _docker_config(
                {
                    "workspace_root": "/srv/workspaces",
                    "container_workspace_root": root,
                }
            )
        )


def test_docker_workspace_provider_provisions_runnable_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    captured_command: list[str] = []
    captured_kwargs: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured_command[:] = command
        captured_kwargs.clear()
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Runtime profiles only override image, cpus, and memory; sandbox policy
    # fields such as network, pids_limit, and exec_user come from base config.
    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )

    assert binding.workspace_id.startswith("ws-")
    assert (workspace_root / binding.workspace_id).is_dir()
    assert captured_command[:8] == [
        "/usr/bin/docker",
        "run",
        "-d",
        "--name",
        f"agent-{binding.workspace_id}",
        "--network",
        "none",
        "--cap-drop",
    ]
    assert captured_command[8:20] == [
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--cpus",
        "1",
        "--memory",
        "512m",
        "-v",
        f"{workspace_root / binding.workspace_id}:/workspace",
        "-w",
    ]
    assert captured_command[20:] == [
        "/workspace",
        "python:3.11-slim",
        "sleep",
        "infinity",
    ]
    assert captured_kwargs == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "env": None,
        "check": True,
    }


def test_docker_workspace_provider_applies_configured_container_hardening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    captured_command: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured_command[:] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "docker_binary": "/usr/bin/docker",
                "image_allowlist": ["registry.example/agent:2026-05-10"],
                "network": "none",
                "cpus": "2",
                "memory": "1g",
                "pids_limit": 128,
                "exec_user": "1000:1000",
                "default_runtime_profile": "agent",
                "runtime_profiles": {
                    "agent": {
                        "provider": "docker",
                        "image": "registry.example/agent:2026-05-10",
                    }
                },
            }
        ),
        {"kind": "docker"},
    )

    assert binding.workspace_id.startswith("ws-")
    assert captured_command == [
        "/usr/bin/docker",
        "run",
        "-d",
        "--name",
        binding.workspace_id,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--cpus",
        "2",
        "--memory",
        "1g",
        "-v",
        f"{workspace_root / binding.workspace_id}:/workspace",
        "-w",
        "/workspace",
        "--user",
        "1000:1000",
        "registry.example/agent:2026-05-10",
        "sleep",
        "infinity",
    ]


def test_docker_workspace_provider_runs_configured_setup_before_agent_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "docker_binary": "/usr/bin/docker",
                "network": "none",
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["uv sync"],
                        "secret_env_allowlist": [],
                    },
                    "agent": {"network": "none"},
                },
            }
        ),
        {"kind": "docker"},
    )

    assert commands[0] == [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--name",
        f"{binding.workspace_id}-setup",
        "--network",
        "bridge",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--cpus",
        "1",
        "--memory",
        "512m",
        "-v",
        f"{workspace_root / binding.workspace_id}:/workspace",
        "-w",
        "/workspace",
        "python:3.11-slim",
        "/bin/sh",
        "-c",
        "uv sync",
    ]
    assert commands[1][:6] == [
        "/usr/bin/docker",
        "run",
        "-d",
        "--name",
        binding.workspace_id,
        "--network",
    ]
    assert commands[1][6] == "none"


def test_docker_workspace_provider_imports_snapshot_before_setup_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    _ = (source / "README.md").write_text("uploaded\n", encoding="utf-8")
    events: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["run", "--rm"]:
            workspace_mount = command[command.index("-v") + 1]
            host_workspace = Path(workspace_mount.split(":", 1)[0])
            assert (host_workspace / "README.md").read_text(
                encoding="utf-8"
            ) == "uploaded\n"
            events.append("setup")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["run", "-d"]:
            events.append("agent")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "docker_binary": "/usr/bin/docker",
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["test -f README.md"],
                        "secret_env_allowlist": [],
                    },
                    "agent": {"network": "none"},
                },
            }
        ),
        {
            "kind": "docker",
            "snapshot_archive_base64": create_workspace_archive_base64(source),
        },
    )

    assert events == ["setup", "agent"]
    assert (workspace_root / binding.workspace_id / "README.md").read_text(
        encoding="utf-8"
    ) == "uploaded\n"


def test_docker_workspace_provider_removes_workspace_when_snapshot_import_fails_before_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise RuntimeError(
            f"docker cleanup should not run before containers: {command}"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(Exception):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "docker_binary": "/usr/bin/docker",
                }
            ),
            {
                "kind": "docker",
                "snapshot_archive_base64": "not a valid workspace archive",
            },
        )

    assert list(workspace_root.iterdir()) == []


def test_docker_workspace_provider_setup_failure_does_not_start_agent_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[1:3] == ["run", "--rm"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                output="",
                stderr="setup failed",
            )
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "container_name_prefix": "agent-",
                    "docker_binary": "/usr/bin/docker",
                    "remote_phases": {
                        "setup": {
                            "enabled": True,
                            "network": "bridge",
                            "timeout_seconds": 600,
                            "commands": ["uv sync"],
                            "secret_env_allowlist": [],
                        },
                        "agent": {"network": "none"},
                    },
                }
            ),
            {"kind": "docker"},
        )

    assert [command[1:3] for command in commands].count(["run", "-d"]) == 0
    setup_container_name = commands[0][4]
    agent_container_name = setup_container_name.removesuffix("-setup")
    removed_containers = [
        command[3] for command in commands if command[1:3] == ["rm", "-f"]
    ]
    assert removed_containers == [setup_container_name, agent_container_name]
    assert not any(workspace_root.iterdir())


def test_docker_workspace_provider_injects_setup_secrets_only_into_setup_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    commands: list[list[str]] = []
    monkeypatch.setenv("PIP_INDEX_URL", "https://token@example.test/simple")

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _ = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "docker_binary": "/usr/bin/docker",
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["uv sync"],
                        "secret_env_allowlist": ["PIP_INDEX_URL"],
                    },
                    "agent": {"network": "none"},
                },
            }
        ),
        {"kind": "docker"},
    )

    assert "-e" in commands[0]
    assert "PIP_INDEX_URL" in commands[0]
    assert "PIP_INDEX_URL=https://token@example.test/simple" not in commands[0]
    assert "PIP_INDEX_URL=https://token@example.test/simple" not in commands[1]


def test_docker_workspace_provider_rejects_invalid_setup_secret_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r"remote_phases\.setup\.secret_env_allowlist must be a list of strings",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "remote_phases": {
                        "setup": {
                            "enabled": True,
                            "network": "bridge",
                            "timeout_seconds": 600,
                            "commands": ["uv sync"],
                            "secret_env_allowlist": "PIP_INDEX_URL",
                        },
                        "agent": {"network": "none"},
                    },
                }
            ),
            {"kind": "docker"},
        )


def test_docker_workspace_provider_rejects_invalid_setup_secret_env_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r"remote_phases\.setup\.secret_env_allowlist entries must be valid environment variable names",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "remote_phases": {
                        "setup": {
                            "enabled": True,
                            "network": "bridge",
                            "timeout_seconds": 600,
                            "commands": ["uv sync"],
                            "secret_env_allowlist": ["PIP-INDEX-URL"],
                        },
                        "agent": {"network": "none"},
                    },
                }
            ),
            {"kind": "docker"},
        )


def test_docker_workspace_provider_setup_failure_surfaces_redacted_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monkeypatch.setenv("PIP_INDEX_URL", "https://token@example.test/simple")

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["run", "--rm"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                output="using https://token@example.test/simple",
                stderr="setup failed",
            )
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "remote_phases": {
                        "setup": {
                            "enabled": True,
                            "network": "bridge",
                            "timeout_seconds": 600,
                            "commands": ["uv sync"],
                            "secret_env_allowlist": ["PIP_INDEX_URL"],
                        },
                        "agent": {"network": "none"},
                    },
                }
            ),
            {"kind": "docker"},
        )

    notes = "\n".join(exc_info.value.__notes__)
    assert "setup phase stdout:" in notes
    assert "using [REDACTED]" in notes
    assert "https://token@example.test/simple" not in notes
    assert "setup phase stderr:\nsetup failed" in notes


def test_docker_workspace_provider_applies_requested_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    captured_command: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured_command[:] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "image_allowlist": [
                    "python:3.11-slim",
                    "registry.example/universal:2026-05-11",
                ],
                "network": "none",
                "pids_limit": 256,
                "exec_user": "1000:1000",
                "default_runtime_profile": "universal",
                "runtime_profiles": {
                    "universal": {
                        "provider": "docker",
                        "image": "registry.example/universal:2026-05-11",
                        "cpus": "4",
                        "memory": "8g",
                        "network": "bridge",
                        "pids_limit": 4096,
                        "exec_user": "0:0",
                    }
                },
            }
        ),
        {"kind": "docker", "runtime_profile": "universal"},
    )

    assert binding.workspace_id.startswith("ws-")
    assert captured_command == [
        "docker",
        "run",
        "-d",
        "--name",
        binding.workspace_id,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--cpus",
        "4",
        "--memory",
        "8g",
        "-v",
        f"{workspace_root / binding.workspace_id}:/workspace",
        "-w",
        "/workspace",
        "--user",
        "1000:1000",
        "registry.example/universal:2026-05-11",
        "sleep",
        "infinity",
    ]


def test_docker_workspace_provider_uses_default_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    captured_command: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured_command[:] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "image_allowlist": [
                    "python:3.11-slim",
                    "registry.example/universal:2026-05-11",
                ],
                "default_runtime_profile": "universal",
                "runtime_profiles": {
                    "universal": {
                        "provider": "docker",
                        "image": "registry.example/universal:2026-05-11",
                    }
                },
            }
        ),
        {"kind": "docker"},
    )

    assert binding.runtime_profile == "universal"
    assert "registry.example/universal:2026-05-11" in captured_command


def test_docker_workspace_provider_requires_default_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r"cloud_workspace.default_runtime_profile is required",
    ):
        _ = provision_cloud_binding_from_config(
            {
                "provider": "docker",
                "workspace_root": str(workspace_root),
                "image_allowlist": ["python:3.11-slim"],
                "runtime_profiles": {
                    "python-basic": {
                        "provider": "docker",
                        "image": "python:3.11-slim",
                    }
                },
            },
            {"kind": "docker"},
        )


def test_docker_workspace_provider_rejects_unknown_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r"cloud_workspace.runtime_profile is not configured: missing",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "default_runtime_profile": "python-basic",
                    "runtime_profiles": {},
                }
            ),
            {"kind": "docker", "runtime_profile": "missing"},
        )


def test_docker_workspace_provider_rejects_non_docker_runtime_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r'cloud_workspace.runtime_profiles.universal.provider must be "docker"',
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "default_runtime_profile": "universal",
                    "runtime_profiles": {
                        "universal": {
                            "provider": "kubernetes",
                            "image": "registry.example/universal:2026-05-11",
                        }
                    },
                }
            ),
            {"kind": "docker", "runtime_profile": "universal"},
        )


def test_docker_workspace_provider_rejects_image_outside_allowlist(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"cloud_workspace\.image is not allowed by image_allowlist",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(tmp_path),
                    "image_allowlist": ["python:3.11-slim"],
                    "default_runtime_profile": "untrusted",
                    "runtime_profiles": {
                        "untrusted": {
                            "provider": "docker",
                            "image": "registry.example/untrusted:latest",
                        }
                    },
                }
            ),
            {"kind": "docker"},
        )


def test_docker_workspace_provider_rejects_provision_when_active_workspace_quota_is_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "ws-existing").mkdir(parents=True)

    def fail_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise AssertionError("docker run must not be called when quota is full")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(
        ValueError,
        match=r"cloud workspace quota exceeded: max_active_workspaces=1",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "default_runtime_profile": "python-basic",
                    "max_active_workspaces": 1,
                }
            ),
            {"kind": "docker"},
        )


def test_docker_workspace_provider_quota_ignores_unowned_workspace_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "not-a-workspace").mkdir(parents=True)
    (workspace_root / "ws-owned").mkdir()
    captured_command: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured_command[:] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "default_runtime_profile": "python-basic",
                "max_active_workspaces": 2,
            }
        ),
        {"kind": "docker"},
    )

    assert binding.workspace_id.startswith("ws-")
    assert captured_command[:3] == ["docker", "run", "-d"]


def test_docker_workspace_provider_reserves_quota_slot_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    start_barrier = threading.Barrier(2)
    release_first_run = threading.Event()
    first_run_started = threading.Event()
    run_count = 0
    run_count_lock = threading.Lock()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal run_count
        del kwargs
        with run_count_lock:
            run_count += 1
            current_run = run_count
        if current_run == 1:
            first_run_started.set()
            _ = release_first_run.wait(timeout=5)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def provision() -> CloudWorkspaceBinding:
        start_barrier.wait(timeout=5)
        return provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "default_runtime_profile": "python-basic",
                    "max_active_workspaces": 1,
                }
            ),
            {"kind": "docker"},
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    results: list[CloudWorkspaceBinding] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(provision())
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    second.start()
    assert first_run_started.wait(timeout=5)
    release_first_run.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 1
    assert run_count == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "cloud workspace quota exceeded" in str(errors[0])


def test_docker_workspace_provider_gc_removes_only_stale_owned_workspaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    stale_owned = workspace_root / "ws-stale"
    fresh_owned = workspace_root / "ws-fresh"
    unrelated = workspace_root / "project-not-owned"
    stale_owned.mkdir(parents=True)
    fresh_owned.mkdir()
    unrelated.mkdir()
    old_timestamp = time.time() - 7200
    os.utime(stale_owned, (old_timestamp, old_timestamp))
    removed_containers: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[:3] == ["docker", "rm", "-f"]:
            removed_containers.append(command[3])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="No such container"
            )
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cleaned = cleanup_stale_cloud_workspaces_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "max_workspace_age_seconds": 3600,
            }
        ),
    )

    assert cleaned == 1
    assert removed_containers == ["ws-stale-setup", "ws-stale"]
    assert not stale_owned.exists()
    assert fresh_owned.exists()
    assert unrelated.exists()


def test_docker_workspace_provider_gc_skips_active_workspace_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    active_owned = workspace_root / "ws-active"
    stale_owned = workspace_root / "ws-stale"
    active_owned.mkdir(parents=True)
    stale_owned.mkdir()
    old_timestamp = time.time() - 7200
    os.utime(active_owned, (old_timestamp, old_timestamp))
    os.utime(stale_owned, (old_timestamp, old_timestamp))
    removed_containers: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[:3] == ["docker", "rm", "-f"]:
            removed_containers.append(command[3])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="No such container"
            )
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cleaned = cleanup_stale_cloud_workspaces_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "max_workspace_age_seconds": 3600,
                "_active_workspace_ids": ["ws-active"],
            }
        ),
    )

    assert cleaned == 1
    assert removed_containers == ["ws-stale-setup", "ws-stale"]
    assert active_owned.exists()
    assert not stale_owned.exists()


def test_docker_workspace_provider_rejects_boolean_integer_config(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"cloud_workspace.max_active_workspaces must be a positive integer",
    ):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(tmp_path),
                    "max_active_workspaces": True,
                }
            ),
            {"kind": "docker"},
        )


def test_docker_workspace_provider_provision_cleans_up_container_when_start_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    commands: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[1:3] == ["run", "-d"]:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, int)
            raise subprocess.TimeoutExpired(command, timeout=timeout)
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        _ = provision_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "container_name_prefix": "agent-",
                    "docker_binary": "/usr/bin/docker",
                }
            ),
            {"kind": "docker"},
        )

    container_name = commands[0][4]
    assert commands[1] == ["/usr/bin/docker", "rm", "-f", f"{container_name}-setup"]
    assert commands[2] == [
        "/usr/bin/docker",
        "container",
        "inspect",
        f"{container_name}-setup",
    ]
    assert commands[3] == ["/usr/bin/docker", "rm", "-f", container_name]
    assert commands[4] == ["/usr/bin/docker", "container", "inspect", container_name]
    assert not any(workspace_root.iterdir())


def test_docker_workspace_provider_cleanup_removes_nonempty_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    removed_commands: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        removed_commands.append(command)
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )
    proof_file = workspace_root / binding.workspace_id / "qa-proof.txt"
    _ = proof_file.write_text("qa-from-cloud\n", encoding="utf-8")

    cleanup_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        binding,
    )

    assert removed_commands[1] == [
        "/usr/bin/docker",
        "rm",
        "-f",
        f"agent-{binding.workspace_id}-setup",
    ]
    assert removed_commands[3] == [
        "/usr/bin/docker",
        "rm",
        "-f",
        f"agent-{binding.workspace_id}",
    ]
    assert not (workspace_root / binding.workspace_id).exists()


def test_docker_workspace_provider_cleanup_removes_setup_and_agent_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    removed_containers: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["rm", "-f"]:
            removed_containers.append(command[3])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["uv sync"],
                        "secret_env_allowlist": [],
                    },
                    "agent": {"network": "none"},
                },
            }
        ),
        {"kind": "docker"},
    )

    cleanup_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        binding,
    )

    assert removed_containers == [
        f"agent-{binding.workspace_id}-setup",
        f"agent-{binding.workspace_id}",
    ]
    assert not (workspace_root / binding.workspace_id).exists()


def test_docker_workspace_provider_cleanup_waits_for_container_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    commands: list[list[str]] = []
    inspect_calls = 0
    sleep_calls: list[float] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal inspect_calls
        del kwargs
        commands.append(command)
        if command[1:3] == ["container", "inspect"]:
            inspect_calls += 1
            if inspect_calls == 1:
                return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider.time.sleep",
        sleep_calls.append,
    )

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )

    cleanup_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        binding,
    )

    assert [command[1:3] for command in commands].count(["container", "inspect"]) == 3
    assert sleep_calls == [0.1]


def test_docker_workspace_provider_cleanup_preserves_workspace_when_container_remove_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"Error: No such container: {command[-1]}\n",
            )
        if command[1:3] == ["run", "-d"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="permission denied",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )
    proof_file = workspace_root / binding.workspace_id / "qa-proof.txt"
    _ = proof_file.write_text("qa-from-cloud\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="failed to remove docker workspace container"
    ):
        cleanup_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "container_name_prefix": "agent-",
                    "docker_binary": "/usr/bin/docker",
                }
            ),
            binding,
        )

    assert proof_file.exists()


@pytest.mark.parametrize(
    "inspect_failure",
    [
        subprocess.CompletedProcess(
            ["/usr/bin/docker", "container", "inspect", "agent-ws-broken"],
            1,
            stdout="",
            stderr="Cannot connect to the Docker daemon\n",
        ),
        OSError("docker unavailable"),
    ],
)
def test_docker_workspace_provider_cleanup_preserves_workspace_when_container_inspect_fails(
    inspect_failure: subprocess.CompletedProcess[str] | OSError,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["run", "-d"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            if isinstance(inspect_failure, OSError):
                raise inspect_failure
            return inspect_failure
        raise AssertionError(f"unexpected docker command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )
    proof_file = workspace_root / binding.workspace_id / "qa-proof.txt"
    _ = proof_file.write_text("qa-from-cloud\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="failed to inspect docker workspace container"
    ):
        cleanup_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "container_name_prefix": "agent-",
                    "docker_binary": "/usr/bin/docker",
                }
            ),
            binding,
        )

    assert proof_file.exists()


def test_docker_workspace_provider_cleanup_preserves_workspace_when_container_removal_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monotonic_values = iter([0.0, 0.0, 5.1])

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider.time.monotonic",
        lambda: next(monotonic_values),
    )

    def ignore_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(
        "coding_agent.environment.docker_workspace_provider.time.sleep",
        ignore_sleep,
    )

    binding = provision_cloud_binding_from_config(
        _docker_config(
            {
                "workspace_root": str(workspace_root),
                "container_name_prefix": "agent-",
                "docker_binary": "/usr/bin/docker",
            }
        ),
        {"kind": "docker"},
    )
    proof_file = workspace_root / binding.workspace_id / "qa-proof.txt"
    _ = proof_file.write_text("qa-from-cloud\n", encoding="utf-8")

    with pytest.raises(TimeoutError, match="docker workspace container still exists"):
        cleanup_cloud_binding_from_config(
            _docker_config(
                {
                    "workspace_root": str(workspace_root),
                    "container_name_prefix": "agent-",
                    "docker_binary": "/usr/bin/docker",
                }
            ),
            binding,
        )

    assert proof_file.exists()
