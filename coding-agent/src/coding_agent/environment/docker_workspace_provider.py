from __future__ import annotations

import json
import logging
import posixpath
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from .cloud import CloudCommandResult
from .workspace_provider import (
    CloudWorkspaceClientFactory,
    CloudWorkspaceSource,
    WorkspaceProvider,
    register_workspace_provider,
)
from ..workspace_archive import (
    create_workspace_archive_base64,
    extract_workspace_archive_base64,
)

if TYPE_CHECKING:
    from .cloud import CloudWorkspaceClient
    from ..ui.execution_binding import CloudWorkspaceBinding


logger = logging.getLogger(__name__)
_WORKSPACE_QUOTA_LOCKS: dict[Path, threading.Lock] = {}
_WORKSPACE_QUOTA_LOCKS_GUARD = threading.Lock()
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOCKER_TIMEOUT_KILL_AFTER_SECONDS = 2
_DOCKER_TIMEOUT_MARGIN_SECONDS = 1
_DOCKER_TIMEOUT_CLEANUP_SECONDS = 3
_DOCKER_TIMEOUT_SENTINEL_PREFIX = "__CODING_AGENT_DOCKER_TIMEOUT__:"
_DOCKER_CONTAINER_REMOVAL_WAIT_SECONDS = 5.0
_DOCKER_CONTAINER_REMOVAL_POLL_INTERVAL_SECONDS = 0.1
_DEFAULT_DOCKER_IMAGE = "python:3.11-slim"
_DEFAULT_DOCKER_CPUS = "1"
_DEFAULT_DOCKER_MEMORY = "512m"
_DEFAULT_DOCKER_PIDS_LIMIT = 256
_DEFAULT_DOCKER_NETWORK = "none"


@dataclass(frozen=True)
class _DockerWorkspaceProviderConfig:
    workspace_root: Path
    container_workspace_root: str
    container_name_prefix: str
    docker_binary: str
    env_allowlist: tuple[str, ...]
    exec_user: str | None
    image: str
    image_allowlist: tuple[str, ...]
    network: str
    cpus: str
    memory: str
    pids_limit: int
    max_active_workspaces: int | None
    max_workspace_age_seconds: int | None


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
                resolved_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines(),
                start=1,
            ):
                if matcher.search(line):
                    matches.append(f"{remote_path}:{line_number}:{line}")
        return matches

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        from ..tools.file_patch_tool import build_file_patch_tool

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
        timeout_pidfile = f"/tmp/coding-agent-docker-exec-{secrets.token_hex(16)}.pid"
        child_wrapper = "\n".join(
            [
                'printf "%s\\n" "$$" > "$1" || exit 125',
                'exec /bin/sh -c "$2"',
            ]
        )
        timeout_wrapper = "\n".join(
            [
                f"pidfile='{timeout_pidfile}'",
                "child=''",
                "_coding_agent_cleanup() {",
                '  rm -f "$pidfile"',
                "}",
                "_coding_agent_timeout() {",
                '  if [ -n "$child" ]; then',
                '    kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || true',
                "  fi",
                f"  printf '%s\\n' '{timeout_sentinel}' >&2",
                '  wait "$child" 2>/dev/null || true',
                "  exit 124",
                "}",
                "trap _coding_agent_cleanup EXIT",
                "trap _coding_agent_timeout TERM",
                "if command -v setsid >/dev/null 2>&1; then",
                '  setsid /bin/sh -c "$1" sh "$pidfile" "$2" &',
                "else",
                '  /bin/sh -c "$1" sh "$pidfile" "$2" &',
                "fi",
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
                child_wrapper,
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
            self._cleanup_timed_out_command(timeout_pidfile)
            raise TimeoutError(
                f"docker exec command timed out after {timeout}s"
            ) from exc

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

    def _filtered_env_items(self, env: dict[str, str] | None) -> list[tuple[str, str]]:
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

    def _cleanup_timed_out_command(self, timeout_pidfile: str) -> None:
        cleanup_command = [self._docker_binary, "exec"]
        if self._exec_user is not None:
            cleanup_command.extend(["--user", self._exec_user])
        cleanup_script = "\n".join(
            [
                'pid=$(cat "$1" 2>/dev/null || true)',
                'if [ -z "$pid" ]; then',
                '  rm -f "$1"',
                "  exit 0",
                "fi",
                'kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true',
                f"sleep {_DOCKER_TIMEOUT_KILL_AFTER_SECONDS}",
                'kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true',
                'rm -f "$1"',
            ]
        )
        cleanup_command.extend(
            [
                self._container_name,
                "/bin/sh",
                "-c",
                cleanup_script,
                "sh",
                timeout_pidfile,
            ]
        )
        try:
            _ = subprocess.run(
                cleanup_command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=_DOCKER_TIMEOUT_CLEANUP_SECONDS,
                env=None,
            )
        except (OSError, subprocess.SubprocessError):
            return

    def _validated_workspace_path(self, host_path: Path, remote_path: str) -> Path:
        resolved_path = host_path.resolve(strict=False)
        try:
            _ = resolved_path.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ValueError(
                f"Path is outside docker workspace: {remote_path}"
            ) from exc
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

    @override
    def check_readiness(self, config: dict[str, object]) -> bool:
        provider_config = _docker_workspace_provider_config(config)
        return _docker_provider_ready(provider_config)

    @override
    def provision_cloud_workspace_binding(
        self,
        config: dict[str, object],
        source: CloudWorkspaceSource,
    ) -> CloudWorkspaceBinding:
        from ..ui.execution_binding import CloudWorkspaceBinding

        kind = source.get("kind")
        if kind != "docker":
            raise ValueError(
                "cloud workspace source kind is not supported for provider=docker"
            )

        provider_config = _docker_workspace_provider_config(config)
        with _quota_lock_for_workspace_root(provider_config.workspace_root):
            _enforce_active_workspace_quota(provider_config)
            workspace_id = f"ws-{uuid.uuid4().hex}"
            workspace_root = _workspace_root_for_id(
                provider_config.workspace_root, workspace_id
            )
            workspace_root.mkdir(parents=True, exist_ok=False)
        binding = CloudWorkspaceBinding(
            workspace_url=(
                f"docker://{_container_name(provider_config, workspace_id)}{provider_config.container_workspace_root}"
            ),
            workspace_id=workspace_id,
        )
        try:
            _start_docker_workspace_container(provider_config, binding)
        except Exception as exc:
            logger.exception(
                "Docker workspace creation failed for workspace_id=%s",
                workspace_id,
            )
            cleanup_failed = False
            try:
                _remove_docker_workspace_container(provider_config, workspace_id)
            except Exception as cleanup_exc:
                cleanup_failed = True
                note = (
                    "failed to clean up docker workspace container after start failure: "
                    + str(cleanup_exc)
                )
                exc.add_note(note)
            if not cleanup_failed and workspace_root.exists():
                shutil.rmtree(workspace_root)
            raise
        logger.info(
            "Docker workspace created workspace_id=%s container=%s",
            workspace_id,
            _container_name(provider_config, workspace_id),
        )
        return binding

    @override
    def cleanup_cloud_workspace_binding(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
    ) -> None:
        provider_config = _docker_workspace_provider_config(config)
        logger.info(
            "Cleaning Docker workspace workspace_id=%s container=%s",
            binding.workspace_id,
            _container_name(provider_config, binding.workspace_id),
        )
        _remove_docker_workspace_container(provider_config, binding.workspace_id)
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root, binding.workspace_id
        )
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        logger.info("Docker workspace cleaned workspace_id=%s", binding.workspace_id)

    @override
    def import_workspace_archive(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
        archive_base64: str,
    ) -> None:
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root, binding.workspace_id
        )
        try:
            extract_workspace_archive_base64(workspace_root, archive_base64)
        except Exception:
            logger.exception(
                "Docker workspace archive import failed workspace_id=%s",
                binding.workspace_id,
            )
            raise

    @override
    def export_workspace_archive(
        self,
        config: dict[str, object],
        binding: CloudWorkspaceBinding,
    ) -> str:
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root, binding.workspace_id
        )
        try:
            return create_workspace_archive_base64(workspace_root)
        except Exception:
            logger.exception(
                "Docker workspace archive export failed workspace_id=%s",
                binding.workspace_id,
            )
            raise

    @override
    def cleanup_stale_cloud_workspaces(self, config: dict[str, object]) -> int:
        provider_config = _docker_workspace_provider_config(config)
        if provider_config.max_workspace_age_seconds is None:
            return 0
        return _cleanup_stale_docker_workspaces(
            provider_config,
            active_workspace_ids=_active_workspace_ids_from_config(config),
        )


def _docker_workspace_provider_config(
    config: dict[str, object],
) -> _DockerWorkspaceProviderConfig:
    workspace_root_raw = config.get("workspace_root")
    if not isinstance(workspace_root_raw, str) or not workspace_root_raw.strip():
        raise ValueError(
            "cloud_workspace.workspace_root is required for provider=docker"
        )

    container_workspace_root = _container_workspace_root(config)
    container_name_prefix = _optional_string(config.get("container_name_prefix"), "")
    docker_binary = _optional_string(config.get("docker_binary"), "docker")
    exec_user = _optional_string(config.get("exec_user"), None)
    env_allowlist = tuple(
        _string_list(config.get("env_allowlist"), key="env_allowlist")
    )
    image = _optional_string(config.get("image"), _DEFAULT_DOCKER_IMAGE)
    image_allowlist = tuple(
        _string_list(
            config.get("image_allowlist"),
            key="image_allowlist",
            default=(_DEFAULT_DOCKER_IMAGE,),
        )
    )
    network = _optional_string(config.get("network"), _DEFAULT_DOCKER_NETWORK)
    cpus = _optional_string(config.get("cpus"), _DEFAULT_DOCKER_CPUS)
    memory = _optional_string(config.get("memory"), _DEFAULT_DOCKER_MEMORY)
    pids_limit = _positive_int(
        config.get("pids_limit"),
        key="pids_limit",
        default=_DEFAULT_DOCKER_PIDS_LIMIT,
    )
    max_active_workspaces = _optional_positive_int(
        config.get("max_active_workspaces"),
        key="max_active_workspaces",
    )
    max_workspace_age_seconds = _optional_positive_int(
        config.get("max_workspace_age_seconds"),
        key="max_workspace_age_seconds",
    )
    assert container_name_prefix is not None
    assert docker_binary is not None
    assert image is not None
    assert network is not None
    assert cpus is not None
    assert memory is not None
    if image not in image_allowlist:
        raise ValueError(
            f"cloud_workspace.image is not allowed by image_allowlist: {image}"
        )

    return _DockerWorkspaceProviderConfig(
        workspace_root=Path(workspace_root_raw).expanduser().resolve(),
        container_workspace_root=container_workspace_root,
        container_name_prefix=container_name_prefix,
        docker_binary=docker_binary,
        env_allowlist=env_allowlist,
        exec_user=exec_user,
        image=image,
        image_allowlist=image_allowlist,
        network=network,
        cpus=cpus,
        memory=memory,
        pids_limit=pids_limit,
        max_active_workspaces=max_active_workspaces,
        max_workspace_age_seconds=max_workspace_age_seconds,
    )


def _container_workspace_root(config: dict[str, object]) -> str:
    root = _optional_string(config.get("container_workspace_root"), "/workspace")
    assert root is not None
    normalized = posixpath.normpath(root)
    if not normalized.startswith("/"):
        raise ValueError("cloud_workspace.container_workspace_root must be absolute")
    if not normalized.lstrip("/"):
        raise ValueError(
            "cloud_workspace.container_workspace_root must not resolve to /"
        )
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


def _string_list(
    value: object, *, key: str, default: tuple[str, ...] = ()
) -> Iterable[str]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError(f"cloud_workspace.{key} must be a list of strings")
    items: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"cloud_workspace.{key} must be a list of strings")
        items.append(item.strip())
    return items


def _positive_int(value: object, *, key: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"cloud_workspace.{key} must be a positive integer")
    return value


def _optional_positive_int(value: object, *, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"cloud_workspace.{key} must be a positive integer")
    return value


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
        raise ValueError(
            f"unsupported workspace id for docker provider: {workspace_id}"
        )


def _workspace_root_for_id(workspace_root: Path, workspace_id: str) -> Path:
    candidate = (workspace_root / workspace_id).resolve()
    try:
        _ = candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(
            f"workspace id escapes configured workspace root: {workspace_id}"
        ) from exc
    return candidate


def _is_provider_workspace_id(name: str) -> bool:
    return name.startswith("ws-") and _WORKSPACE_ID_RE.fullmatch(name) is not None


def _quota_lock_for_workspace_root(workspace_root: Path) -> threading.Lock:
    with _WORKSPACE_QUOTA_LOCKS_GUARD:
        lock = _WORKSPACE_QUOTA_LOCKS.get(workspace_root)
        if lock is None:
            lock = threading.Lock()
            _WORKSPACE_QUOTA_LOCKS[workspace_root] = lock
        return lock


def _active_workspace_count(provider_config: _DockerWorkspaceProviderConfig) -> int:
    if not provider_config.workspace_root.exists():
        return 0
    count = 0
    for path in provider_config.workspace_root.iterdir():
        if path.is_dir() and _is_provider_workspace_id(path.name):
            count += 1
    return count


def _enforce_active_workspace_quota(
    provider_config: _DockerWorkspaceProviderConfig,
) -> None:
    if provider_config.max_active_workspaces is None:
        return
    active_count = _active_workspace_count(provider_config)
    if active_count >= provider_config.max_active_workspaces:
        logger.warning(
            "Docker workspace quota exceeded active=%s max_active_workspaces=%s",
            active_count,
            provider_config.max_active_workspaces,
        )
        raise ValueError(
            "cloud workspace quota exceeded: "
            f"max_active_workspaces={provider_config.max_active_workspaces}"
        )


def _cleanup_stale_docker_workspaces(
    provider_config: _DockerWorkspaceProviderConfig,
    *,
    active_workspace_ids: set[str] | None = None,
) -> int:
    assert provider_config.max_workspace_age_seconds is not None
    if not provider_config.workspace_root.exists():
        return 0

    active_workspace_ids = active_workspace_ids or set()
    cutoff = time.time() - provider_config.max_workspace_age_seconds
    cleaned = 0
    for path in provider_config.workspace_root.iterdir():
        if not path.is_dir() or not _is_provider_workspace_id(path.name):
            continue
        if path.name in active_workspace_ids:
            logger.info("Docker workspace GC skipped active workspace_id=%s", path.name)
            continue
        if path.stat().st_mtime >= cutoff:
            continue
        logger.info("Docker workspace GC removing stale workspace_id=%s", path.name)
        _remove_docker_workspace_container(provider_config, path.name)
        if path.exists():
            shutil.rmtree(path)
        cleaned += 1
    return cleaned


def _active_workspace_ids_from_config(config: dict[str, object]) -> set[str]:
    raw_ids = config.get("_active_workspace_ids")
    if raw_ids is None:
        return set()
    if not isinstance(raw_ids, list):
        raise ValueError("cloud_workspace._active_workspace_ids must be a list")
    active_ids: set[str] = set()
    for raw_id in cast(list[object], raw_ids):
        if not isinstance(raw_id, str) or not _is_provider_workspace_id(raw_id):
            raise ValueError(
                "cloud_workspace._active_workspace_ids must contain workspace ids"
            )
        active_ids.add(raw_id)
    return active_ids


def _container_name(
    provider_config: _DockerWorkspaceProviderConfig, workspace_id: str
) -> str:
    return f"{provider_config.container_name_prefix}{workspace_id}"


def _docker_provider_ready(provider_config: _DockerWorkspaceProviderConfig) -> bool:
    command = [
        provider_config.docker_binary,
        "info",
        "--format",
        "{{json .}}",
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _start_docker_workspace_container(
    provider_config: _DockerWorkspaceProviderConfig,
    binding: CloudWorkspaceBinding,
) -> None:
    workspace_root = _workspace_root_for_id(
        provider_config.workspace_root, binding.workspace_id
    )
    command = [
        provider_config.docker_binary,
        "run",
        "-d",
        "--name",
        _container_name(provider_config, binding.workspace_id),
        "--network",
        provider_config.network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(provider_config.pids_limit),
        "--cpus",
        provider_config.cpus,
        "--memory",
        provider_config.memory,
        "-v",
        f"{workspace_root}:{provider_config.container_workspace_root}",
        "-w",
        provider_config.container_workspace_root,
    ]
    if provider_config.exec_user is not None:
        command.extend(["--user", provider_config.exec_user])
    command.extend([provider_config.image, "sleep", "infinity"])
    _ = subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=None,
        check=True,
    )


def _remove_docker_workspace_container(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_id: str,
) -> None:
    container_name = _container_name(provider_config, workspace_id)
    command = [
        provider_config.docker_binary,
        "rm",
        "-f",
        container_name,
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception(
            "Docker workspace container removal failed container=%s",
            container_name,
        )
        raise RuntimeError(
            f"failed to remove docker workspace container: {container_name}"
        ) from exc

    if result.returncode != 0 and "No such container" not in result.stderr:
        logger.error(
            "Docker workspace container removal failed container=%s returncode=%s stderr=%s",
            container_name,
            result.returncode,
            result.stderr,
        )
        raise RuntimeError(
            f"failed to remove docker workspace container: {container_name}"
        )

    deadline = time.monotonic() + _DOCKER_CONTAINER_REMOVAL_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _docker_container_exists(provider_config, container_name):
            return
        time.sleep(_DOCKER_CONTAINER_REMOVAL_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"docker workspace container still exists after cleanup: {container_name}"
    )


def _docker_container_exists(
    provider_config: _DockerWorkspaceProviderConfig,
    container_name: str,
) -> bool:
    command = [
        provider_config.docker_binary,
        "container",
        "inspect",
        container_name,
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception(
            "Docker workspace container inspect failed container=%s",
            container_name,
        )
        raise RuntimeError(
            f"failed to inspect docker workspace container: {container_name}"
        ) from exc
    if result.returncode == 0:
        return True
    if "No such container" in result.stderr:
        return False
    logger.error(
        "Docker workspace container inspect failed container=%s returncode=%s stderr=%s",
        container_name,
        result.returncode,
        result.stderr,
    )
    raise RuntimeError(
        f"failed to inspect docker workspace container: {container_name}"
    )


def _normalize_remote_path(path: str, workspace_root: str) -> str:
    normalized_root = posixpath.normpath(workspace_root)
    if not normalized_root.lstrip("/"):
        raise ValueError(
            "cloud_workspace.container_workspace_root must not resolve to /"
        )
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
