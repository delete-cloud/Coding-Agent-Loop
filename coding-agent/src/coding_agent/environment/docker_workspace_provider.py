from __future__ import annotations

import json
import posixpath
import re
import secrets
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from .cloud import CloudCommandResult
from .workspace_provider import CloudWorkspaceClientFactory, WorkspaceProvider, register_workspace_provider

if TYPE_CHECKING:
    from .cloud import CloudWorkspaceClient
    from ..ui.execution_binding import CloudWorkspaceBinding


_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOCKER_TIMEOUT_KILL_AFTER_SECONDS = 2
_DOCKER_TIMEOUT_MARGIN_SECONDS = 1
_DOCKER_TIMEOUT_SENTINEL_PREFIX = "__CODING_AGENT_DOCKER_TIMEOUT__:"


@dataclass(frozen=True)
class _DockerWorkspaceProviderConfig:
    workspace_root: Path
    container_workspace_root: str
    container_name_prefix: str
    docker_binary: str
    env_allowlist: tuple[str, ...]
    exec_user: str | None


class DockerCloudWorkspaceClient:
    def __init__(
        self,
        *,
        binding: CloudWorkspaceBinding,
        config: _DockerWorkspaceProviderConfig,
    ) -> None:
        _validate_workspace_id(binding.workspace_id)
        self.workspace_id: str = binding.workspace_id
        self.workspace_url: str = binding.workspace_url
        self.default_cwd: str = config.container_workspace_root
        self._workspace_root: Path = _workspace_root_for_id(
            config.workspace_root, binding.workspace_id
        )
        self._docker_binary: str = config.docker_binary
        self._container_name: str = (
            f"{config.container_name_prefix}{binding.workspace_id}"
        )
        self._env_allowlist: set[str] = set(config.env_allowlist)
        self._exec_user: str | None = config.exec_user

    def read_file(self, path: str) -> str:
        _, host_path = self._resolve_file_path(path)
        return host_path.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        _, host_path = self._resolve_file_path(path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        _ = host_path.write_text(content, encoding="utf-8")

    def replace_file(self, path: str, old: str, new: str) -> None:
        _, host_path = self._resolve_file_path(path)
        content = host_path.read_text(encoding="utf-8")
        if old not in content:
            raise ValueError(f"'{old}' not found in {path}")
        updated = content.replace(old, new, 1)
        _ = host_path.write_text(updated, encoding="utf-8")

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        _, host_directory = self._resolve_directory_path(directory)
        matches: list[str] = []
        for path in sorted(host_directory.glob(pattern)):
            remote_path = self._workspace_entry_remote_path(path)
            _ = self._validated_workspace_path(path, remote_path)
            matches.append(remote_path)
        return matches

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        _, host_directory = self._resolve_directory_path(directory)
        matcher = re.compile(pattern)
        include_pattern = include or "*"
        matches: list[str] = []
        for path in sorted(host_directory.rglob(include_pattern)):
            remote_path = self._workspace_entry_remote_path(path)
            resolved_path = self._validated_workspace_path(path, remote_path)
            if not resolved_path.is_file():
                continue
            for line_number, line in enumerate(
                resolved_path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if matcher.search(line):
                    matches.append(f"{remote_path}:{line_number}:{line}")
        return matches

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        from coding_agent.tools.file_patch_tool import build_file_patch_tool

        _, host_path = self._resolve_file_path(path)
        patch_tool = build_file_patch_tool(self._workspace_root)
        payload = patch_tool(str(host_path.relative_to(self._workspace_root)), patch)
        return _json_object(payload)

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        remote_cwd, host_cwd = self._resolve_directory_path(cwd or self.default_cwd)
        if not host_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {remote_cwd}")

        docker_command = [
            self._docker_binary,
            "exec",
            "--workdir",
            remote_cwd,
        ]
        if self._exec_user is not None:
            docker_command.extend(["--user", self._exec_user])
        for key, value in self._filtered_env_items(env):
            docker_command.extend(["-e", f"{key}={value}"])
        timeout_sentinel = _docker_timeout_sentinel()
        timeout_wrapper = "\n".join(
            [
                "child=''",
                "_coding_agent_timeout() {",
                '  if [ -n "$child" ]; then',
                '    kill -TERM "$child" 2>/dev/null || true',
                "  fi",
                f"  printf '%s\\n' '{timeout_sentinel}' >&2",
                '  wait "$child" 2>/dev/null || true',
                "  exit 124",
                "}",
                "trap _coding_agent_timeout TERM",
                '/bin/sh -c "$1" &',
                "child=$!",
                'wait "$child"',
                "exit $?",
            ]
        )
        docker_command.extend(
            [
                self._container_name,
                "timeout",
                "-s",
                "TERM",
                "-k",
                f"{_DOCKER_TIMEOUT_KILL_AFTER_SECONDS}s",
                f"{timeout}s",
                "/bin/sh",
                "-c",
                timeout_wrapper,
                "sh",
                command,
            ]
        )
        local_timeout = (
            timeout
            + _DOCKER_TIMEOUT_KILL_AFTER_SECONDS
            + _DOCKER_TIMEOUT_MARGIN_SECONDS
        )

        try:
            result = subprocess.run(
                docker_command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=local_timeout,
                env=None,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"docker exec command timed out after {timeout}s") from exc

        if _contains_timeout_sentinel(result.stderr, timeout_sentinel):
            raise TimeoutError(f"docker exec command timed out after {timeout}s")

        return CloudCommandResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )

    def _resolve_file_path(self, path: str) -> tuple[str, Path]:
        remote_path = _normalize_remote_path(path, self.default_cwd)
        host_path = _host_path_for_remote(
            self._workspace_root, self.default_cwd, remote_path
        )
        return remote_path, self._validated_workspace_path(host_path, remote_path)

    def _resolve_directory_path(self, directory: str) -> tuple[str, Path]:
        remote_path = _normalize_remote_path(directory, self.default_cwd)
        host_path = _host_path_for_remote(
            self._workspace_root, self.default_cwd, remote_path
        )
        return remote_path, self._validated_workspace_path(host_path, remote_path)

    def _filtered_env_items(
        self, env: dict[str, str] | None
    ) -> list[tuple[str, str]]:
        if env is None:
            return []
        items: list[tuple[str, str]] = []
        for key, value in env.items():
            if not _ENV_NAME_RE.fullmatch(key):
                raise ValueError(f"unsafe environment variable name: {key!r}")
            if key not in self._env_allowlist:
                raise ValueError(
                    f"environment variable is not allowed for docker workspace: {key}"
                )
            items.append((key, value))
        return items

    def _remote_path_for_host(self, host_path: Path) -> str:
        relative = host_path.resolve().relative_to(self._workspace_root)
        if not relative.parts:
            return self.default_cwd
        return posixpath.join(self.default_cwd, *relative.parts)

    def _validated_workspace_path(self, host_path: Path, remote_path: str) -> Path:
        resolved_path = host_path.resolve(strict=False)
        try:
            _ = resolved_path.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path is outside docker workspace: {remote_path}") from exc
        return resolved_path

    def _workspace_entry_remote_path(self, host_path: Path) -> str:
        relative = host_path.relative_to(self._workspace_root)
        if not relative.parts:
            return self.default_cwd
        return posixpath.join(self.default_cwd, *relative.parts)


class DockerWorkspaceProvider(WorkspaceProvider):
    @override
    def build_cloud_client_factory(
        self, config: dict[str, object]
    ) -> CloudWorkspaceClientFactory:
        provider_config = _docker_workspace_provider_config(config)

        def build_client(binding: CloudWorkspaceBinding) -> CloudWorkspaceClient:
            return DockerCloudWorkspaceClient(binding=binding, config=provider_config)

        return build_client


def _docker_workspace_provider_config(
    config: dict[str, object],
) -> _DockerWorkspaceProviderConfig:
    workspace_root_raw = config.get("workspace_root")
    if not isinstance(workspace_root_raw, str) or not workspace_root_raw.strip():
        raise ValueError("cloud_workspace.workspace_root is required for provider=docker")

    container_workspace_root = _container_workspace_root(config)
    container_name_prefix = _optional_string(config.get("container_name_prefix"), "")
    docker_binary = _optional_string(config.get("docker_binary"), "docker")
    exec_user = _optional_string(config.get("exec_user"), None)
    env_allowlist = tuple(_string_list(config.get("env_allowlist"), key="env_allowlist"))
    assert container_name_prefix is not None
    assert docker_binary is not None

    return _DockerWorkspaceProviderConfig(
        workspace_root=Path(workspace_root_raw).expanduser().resolve(),
        container_workspace_root=container_workspace_root,
        container_name_prefix=container_name_prefix,
        docker_binary=docker_binary,
        env_allowlist=env_allowlist,
        exec_user=exec_user,
    )


def _container_workspace_root(config: dict[str, object]) -> str:
    root = _optional_string(config.get("container_workspace_root"), "/workspace")
    assert root is not None
    normalized = posixpath.normpath(root)
    if not normalized.startswith("/"):
        raise ValueError("cloud_workspace.container_workspace_root must be absolute")
    if not normalized.lstrip("/"):
        raise ValueError("cloud_workspace.container_workspace_root must not resolve to /")
    return normalized


def _optional_string(value: object, default: str | None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"expected string config value, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        return default
    return stripped


def _string_list(value: object, *, key: str) -> Iterable[str]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"cloud_workspace.{key} must be a list of strings")
    items: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"cloud_workspace.{key} must be a list of strings")
        items.append(item.strip())
    return items


def _json_object(payload: str) -> dict[str, object]:
    decoded = cast(object, json.loads(payload))
    if not isinstance(decoded, dict):
        raise ValueError("docker workspace patch payload must decode to an object")
    decoded_dict = cast(dict[object, object], decoded)
    return {str(key): value for key, value in decoded_dict.items()}


def _docker_timeout_sentinel() -> str:
    return f"{_DOCKER_TIMEOUT_SENTINEL_PREFIX}{secrets.token_hex(16)}"


def _contains_timeout_sentinel(stderr: str, sentinel: str) -> bool:
    return any(line == sentinel for line in stderr.splitlines())


def _validate_workspace_id(workspace_id: str) -> None:
    if not _WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise ValueError(f"unsupported workspace id for docker provider: {workspace_id}")


def _workspace_root_for_id(workspace_root: Path, workspace_id: str) -> Path:
    candidate = (workspace_root / workspace_id).resolve()
    try:
        _ = candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"workspace id escapes configured workspace root: {workspace_id}") from exc
    return candidate


def _normalize_remote_path(path: str, workspace_root: str) -> str:
    normalized_root = posixpath.normpath(workspace_root)
    if not normalized_root.lstrip("/"):
        raise ValueError("cloud_workspace.container_workspace_root must not resolve to /")
    resolved = path if path.startswith("/") else posixpath.join(workspace_root, path)
    normalized = posixpath.normpath(resolved)
    workspace_prefix = normalized_root.rstrip("/") + "/"
    if normalized != normalized_root and not normalized.startswith(workspace_prefix):
        raise ValueError(f"Path is outside docker workspace: {normalized}")
    return normalized


def _host_path_for_remote(
    workspace_root: Path,
    container_workspace_root: str,
    remote_path: str,
) -> Path:
    relative = remote_path.removeprefix(container_workspace_root).lstrip("/")
    return workspace_root if not relative else workspace_root / Path(relative)


register_workspace_provider("docker", DockerWorkspaceProvider())
