from __future__ import annotations

import base64
import hashlib
import json
import logging
import posixpath
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import os
from typing import TYPE_CHECKING, cast, override
from urllib.parse import urlsplit, urlunsplit

from .cloud import CloudCommandResult
from coding_agent.runs.target import CloudWorkspaceRef
from .workspace_provider import (
    CloudWorkspaceClientFactory,
    CloudWorkspaceSource,
    WorkspaceProvider,
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceDiff,
    WorkspaceDiffFile,
    WorkspaceDiffStatus,
    WorkspaceInventoryEntry,
    WorkspacePatch,
    WorkspaceProviderCapabilities,
    WorkspaceStatus,
    register_workspace_provider,
)
from .archive import (
    create_workspace_archive_base64,
    extract_workspace_archive_base64,
    should_exclude_workspace_archive_path,
)

if TYPE_CHECKING:
    from .cloud import CloudWorkspaceClient


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
_GIT_OPERATION_TIMEOUT_SECONDS = 300
_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_DEFAULT_DOCKER_IMAGE = "python:3.11-slim"
_DEFAULT_DOCKER_CPUS = "1"
_DEFAULT_DOCKER_MEMORY = "512m"
_DEFAULT_DOCKER_PIDS_LIMIT = 256
_DEFAULT_DOCKER_NETWORK = "none"
_REMOTE_PHASE_SETUP_KEY = "setup"


@dataclass(frozen=True)
class _DockerWorkspaceProviderConfig:
    workspace_root: Path
    container_workspace_root: str
    container_name_prefix: str
    docker_binary: str
    git_binary: str
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
    runtime_profile: str | None = None


class DockerCloudWorkspaceClient:
    def __init__(
        self,
        *,
        binding: CloudWorkspaceRef,
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
        _validate_docker_workspace_base_config(config)

        def build_client(binding: CloudWorkspaceRef) -> CloudWorkspaceClient:
            provider_config = _docker_workspace_provider_config(
                config,
                runtime_profile=binding.runtime_profile,
            )
            return DockerCloudWorkspaceClient(binding=binding, config=provider_config)

        return build_client

    @override
    def check_readiness(self, config: dict[str, object]) -> bool:
        provider_config = _docker_workspace_provider_config(config)
        return _docker_provider_ready(provider_config)

    def workspace_capabilities(
        self, config: dict[str, object]
    ) -> WorkspaceProviderCapabilities:
        provider_config = _docker_workspace_provider_config(config)
        available = _docker_provider_ready(provider_config)
        return WorkspaceProviderCapabilities(
            provider="docker",
            available=available,
            reason="docker_ready" if available else "docker_unavailable",
            supports_provision=available,
            supports_archive=available,
            supports_diff=available,
            supports_patch=available,
            supports_publish=available,
        )

    @override
    def provision_cloud_workspace_binding(
        self,
        config: dict[str, object],
        source: CloudWorkspaceSource,
    ) -> CloudWorkspaceRef:
        kind = source.get("kind")
        if kind not in {"docker", "git"}:
            raise ValueError(
                "cloud workspace source kind is not supported for provider=docker"
            )

        runtime_profile = _runtime_profile_from_source(
            source
        ) or _default_runtime_profile(config)
        provider_config = _docker_workspace_provider_config(
            config,
            runtime_profile=runtime_profile,
        )
        with _quota_lock_for_workspace_root(provider_config.workspace_root):
            _enforce_active_workspace_quota(provider_config)
            workspace_id = f"ws-{uuid.uuid4().hex}"
            workspace_root = _workspace_root_for_id(
                provider_config.workspace_root, workspace_id
            )
            workspace_root.mkdir(parents=True, exist_ok=False)
        binding = CloudWorkspaceRef(
            workspace_url=(
                f"docker://{_container_name(provider_config, workspace_id)}{provider_config.container_workspace_root}"
            ),
            workspace_id=workspace_id,
            runtime_profile=runtime_profile,
        )
        docker_cleanup_needed = False
        try:
            if kind == "git":
                _clone_git_workspace_source(
                    provider_config,
                    config,
                    source,
                    workspace_root,
                )
            else:
                snapshot_archive_base64 = source.get("snapshot_archive_base64")
                if snapshot_archive_base64 is not None:
                    if not isinstance(snapshot_archive_base64, str):
                        raise ValueError(
                            "workspace_source.snapshot_archive_base64 must be a string"
                        )
                    extract_workspace_archive_base64(
                        workspace_root,
                        snapshot_archive_base64,
                    )

            def mark_docker_cleanup_needed() -> None:
                nonlocal docker_cleanup_needed
                docker_cleanup_needed = True

            _run_docker_setup_phase_if_configured(
                provider_config,
                config,
                binding,
                on_docker_invoked=mark_docker_cleanup_needed,
            )
            docker_cleanup_needed = True
            _start_docker_workspace_container(provider_config, binding)
        except Exception as exc:
            logger.exception(
                "Docker workspace creation failed for workspace_id=%s",
                workspace_id,
            )
            cleanup_failed = False
            if docker_cleanup_needed:
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
                shutil.rmtree(workspace_root, ignore_errors=not docker_cleanup_needed)
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
        binding: CloudWorkspaceRef,
    ) -> None:
        provider_config = _docker_workspace_provider_config(
            config,
            runtime_profile=binding.runtime_profile,
        )
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
        binding: CloudWorkspaceRef,
        archive_base64: str,
    ) -> None:
        provider_config = _docker_workspace_provider_config(
            config,
            runtime_profile=binding.runtime_profile,
        )
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
        binding: CloudWorkspaceRef,
    ) -> str:
        provider_config = _docker_workspace_provider_config(
            config,
            runtime_profile=binding.runtime_profile,
        )
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

    @override
    def list_cloud_workspaces(
        self,
        config: dict[str, object],
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> list[WorkspaceInventoryEntry]:
        provider_config = _docker_workspace_provider_config(config)
        return _list_docker_workspaces(provider_config, active_workspace_ids or set())

    @override
    def get_cloud_workspace(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> WorkspaceInventoryEntry:
        provider_config = _docker_workspace_provider_config(config)
        _validate_workspace_id(workspace_id)
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root,
            workspace_id,
        )
        if not workspace_root.is_dir() or not _is_provider_workspace_id(workspace_id):
            raise KeyError(f"workspace not found: {workspace_id}")
        return _docker_workspace_entry(
            provider_config,
            workspace_root,
            active_workspace_ids or set(),
        )

    @override
    def cleanup_cloud_workspace(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        active_workspace_ids: set[str] | None = None,
    ) -> WorkspaceInventoryEntry:
        provider_config = _docker_workspace_provider_config(config)
        _validate_workspace_id(workspace_id)
        if not _is_provider_workspace_id(workspace_id):
            raise KeyError(f"workspace not found: {workspace_id}")
        if workspace_id in (active_workspace_ids or set()):
            raise RuntimeError(f"workspace is active: {workspace_id}")
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root,
            workspace_id,
        )
        if not workspace_root.exists():
            raise KeyError(f"workspace not found: {workspace_id}")
        logger.info("Docker workspace manual cleanup workspace_id=%s", workspace_id)
        _remove_docker_workspace_container(provider_config, workspace_id)
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        return WorkspaceInventoryEntry(
            workspace_id=workspace_id,
            status="cleaned",
            updated_at=datetime.now(UTC),
        )

    @override
    def export_workspace_archive_by_id(
        self,
        config: dict[str, object],
        workspace_id: str,
    ) -> str:
        provider_config = _docker_workspace_provider_config(config)
        return _export_workspace_archive_by_id(provider_config, workspace_id)

    @override
    def workspace_archive_manifest(
        self,
        config: dict[str, object],
        workspace_id: str,
        *,
        session_id: str | None = None,
    ) -> WorkspaceArchiveManifest:
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _workspace_root_for_id(
            provider_config.workspace_root,
            workspace_id,
        )
        return _docker_workspace_archive_manifest(
            workspace_id=workspace_id,
            session_id=session_id,
            workspace_root=workspace_root,
        )

    @override
    def workspace_diff(
        self,
        config: dict[str, object],
        workspace_id: str,
    ) -> WorkspaceDiff:
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _git_workspace_root_for_operation(
            provider_config,
            workspace_id,
            "diff",
        )
        return _git_workspace_diff(provider_config, workspace_id, workspace_root)

    @override
    def workspace_patch(
        self,
        config: dict[str, object],
        workspace_id: str,
    ) -> WorkspacePatch:
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _git_workspace_root_for_operation(
            provider_config,
            workspace_id,
            "patch",
        )
        patch = _git_workspace_patch(provider_config, workspace_root)
        return WorkspacePatch(
            workspace_id=workspace_id,
            format="unified_diff",
            patch=patch,
        )

    @override
    def publish_workspace_branch(
        self,
        config: dict[str, object],
        publication_config: dict[str, object],
        workspace_id: str,
        branch_name: str,
        commit_message: str,
    ) -> WorkspaceBranchPublication:
        if publication_config.get("enabled") is not True:
            raise ValueError("remote_publication.enabled must be true")
        provider_config = _docker_workspace_provider_config(config)
        workspace_root = _git_workspace_root_for_operation(
            provider_config,
            workspace_id,
            "publication",
        )
        return _publish_git_workspace_branch(
            provider_config,
            publication_config,
            workspace_id,
            workspace_root,
            branch_name,
            commit_message,
        )


def _docker_workspace_provider_config(
    config: dict[str, object],
    *,
    runtime_profile: str | None = None,
) -> _DockerWorkspaceProviderConfig:
    resolved_runtime_profile = runtime_profile or _default_runtime_profile(config)
    effective_config = _effective_docker_workspace_config(
        config,
        runtime_profile=resolved_runtime_profile,
    )
    workspace_root_raw = _validate_docker_workspace_base_config(effective_config)

    container_workspace_root = _container_workspace_root(effective_config)
    container_name_prefix = _optional_string(
        effective_config.get("container_name_prefix"), ""
    )
    docker_binary = _optional_string(effective_config.get("docker_binary"), "docker")
    git_binary = _optional_string(effective_config.get("git_binary"), "git")
    exec_user = _optional_string(effective_config.get("exec_user"), None)
    env_allowlist = tuple(
        _string_list(effective_config.get("env_allowlist"), key="env_allowlist")
    )
    image = _optional_string(effective_config.get("image"), _DEFAULT_DOCKER_IMAGE)
    image_allowlist = tuple(
        _string_list(
            config.get("image_allowlist"),
            key="image_allowlist",
            default=(_DEFAULT_DOCKER_IMAGE,),
        )
    )
    network = _optional_string(effective_config.get("network"), _DEFAULT_DOCKER_NETWORK)
    cpus = _optional_string(effective_config.get("cpus"), _DEFAULT_DOCKER_CPUS)
    memory = _optional_string(effective_config.get("memory"), _DEFAULT_DOCKER_MEMORY)
    pids_limit = _positive_int(
        effective_config.get("pids_limit"),
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
    assert git_binary is not None
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
        git_binary=git_binary,
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
        runtime_profile=resolved_runtime_profile,
    )


def _validate_docker_workspace_base_config(config: dict[str, object]) -> str:
    workspace_root_raw = config.get("workspace_root")
    if not isinstance(workspace_root_raw, str) or not workspace_root_raw.strip():
        raise ValueError(
            "cloud_workspace.workspace_root is required for provider=docker"
        )
    _ = _container_workspace_root(config)
    return workspace_root_raw


def _default_runtime_profile(config: dict[str, object]) -> str:
    default_runtime_profile = config.get("default_runtime_profile")
    if (
        not isinstance(default_runtime_profile, str)
        or not default_runtime_profile.strip()
    ):
        raise ValueError("cloud_workspace.default_runtime_profile is required")
    return default_runtime_profile.strip()


def _effective_docker_workspace_config(
    config: dict[str, object],
    *,
    runtime_profile: str | None,
) -> dict[str, object]:
    runtime_profiles = config.get("runtime_profiles")
    if not isinstance(runtime_profiles, dict):
        raise ValueError(
            f"cloud_workspace.runtime_profile is not configured: {runtime_profile}"
        )
    profiles = cast(dict[object, object], runtime_profiles)
    profile = profiles.get(runtime_profile)
    if not isinstance(profile, dict):
        raise ValueError(
            f"cloud_workspace.runtime_profile is not configured: {runtime_profile}"
        )
    profile_config = cast(dict[object, object], profile)
    provider = profile_config.get("provider")
    if provider != "docker":
        raise ValueError(
            f'cloud_workspace.runtime_profiles.{runtime_profile}.provider must be "docker"'
        )
    image = profile_config.get("image")
    if not isinstance(image, str) or not image.strip():
        raise ValueError(
            f"cloud_workspace.runtime_profiles.{runtime_profile}.image is required"
        )
    effective = dict(config)
    # Runtime profiles select toolchain/resource defaults. Sandbox policy fields
    # such as network, exec_user, and pids_limit remain controlled by base config.
    for key in ("image", "cpus", "memory"):
        if key in profile_config:
            effective[key] = profile_config[key]
    return effective


def _runtime_profile_from_source(source: CloudWorkspaceSource) -> str | None:
    runtime_profile = source.get("runtime_profile")
    if runtime_profile is None:
        return None
    if not isinstance(runtime_profile, str) or not runtime_profile.strip():
        raise ValueError("cloud workspace runtime_profile must be a non-empty string")
    return runtime_profile.strip()


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
            f"cloud workspace quota exceeded: max_active_workspaces={provider_config.max_active_workspaces}"
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


def _list_docker_workspaces(
    provider_config: _DockerWorkspaceProviderConfig,
    active_workspace_ids: set[str],
) -> list[WorkspaceInventoryEntry]:
    if not provider_config.workspace_root.exists():
        return []
    entries: list[WorkspaceInventoryEntry] = []
    for path in sorted(
        provider_config.workspace_root.iterdir(), key=lambda item: item.name
    ):
        if not path.is_dir() or not _is_provider_workspace_id(path.name):
            continue
        entries.append(
            _docker_workspace_entry(provider_config, path, active_workspace_ids)
        )
    return entries


def _docker_workspace_entry(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_root: Path,
    active_workspace_ids: set[str],
) -> WorkspaceInventoryEntry:
    workspace_id = workspace_root.name
    status: WorkspaceStatus = "active"
    if workspace_id not in active_workspace_ids:
        max_age = provider_config.max_workspace_age_seconds
        if (
            max_age is not None
            and workspace_root.stat().st_mtime < time.time() - max_age
        ):
            status = "stale"
    return WorkspaceInventoryEntry(
        workspace_id=workspace_id,
        status=status,
        updated_at=datetime.fromtimestamp(workspace_root.stat().st_mtime, UTC),
    )


def _export_workspace_archive_by_id(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_id: str,
) -> str:
    _validate_workspace_id(workspace_id)
    if not _is_provider_workspace_id(workspace_id):
        raise KeyError(f"workspace not found: {workspace_id}")
    workspace_root = _workspace_root_for_id(
        provider_config.workspace_root, workspace_id
    )
    if not workspace_root.is_dir():
        raise KeyError(f"workspace not found: {workspace_id}")
    return create_workspace_archive_base64(workspace_root)


def _docker_workspace_archive_manifest(
    *,
    workspace_id: str,
    session_id: str | None,
    workspace_root: Path,
) -> WorkspaceArchiveManifest:
    _validate_workspace_id(workspace_id)
    if not _is_provider_workspace_id(workspace_id) or not workspace_root.is_dir():
        raise KeyError(f"workspace not found: {workspace_id}")

    changed_files: list[str] = []
    excluded_files: list[str] = []
    total_bytes = 0
    if (workspace_root / ".git").exists():
        excluded_files.append(".git")

    for path in sorted(workspace_root.rglob("*")):
        relative = path.relative_to(workspace_root)
        if should_exclude_workspace_archive_path(relative):
            if relative.parts and relative.parts[0] != ".git":
                if "__pycache__" in relative.parts:
                    excluded_path = Path(
                        *relative.parts[: relative.parts.index("__pycache__") + 1]
                    )
                else:
                    excluded_path = relative
                excluded = excluded_path.as_posix()
                if excluded not in excluded_files:
                    excluded_files.append(excluded)
            continue
        if path.is_symlink():
            raise ValueError(f"workspace archive does not support symlinks: {relative}")
        if not path.is_file():
            continue
        changed_files.append(relative.as_posix())
        total_bytes += path.stat().st_size

    archive_base64 = create_workspace_archive_base64(workspace_root)
    archive_bytes = base64.b64decode(archive_base64.encode("ascii"), validate=True)
    return WorkspaceArchiveManifest(
        workspace_id=workspace_id,
        session_id=session_id,
        format="tar.gz",
        generated_at=datetime.now(UTC),
        file_count=len(changed_files),
        total_bytes=total_bytes,
        changed_files=changed_files,
        deleted_files=[],
        excluded_files=excluded_files,
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
    )


def _git_workspace_root_for_operation(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_id: str,
    operation: str,
) -> Path:
    _validate_workspace_id(workspace_id)
    if not _is_provider_workspace_id(workspace_id):
        raise KeyError(f"workspace not found: {workspace_id}")
    workspace_root = _workspace_root_for_id(
        provider_config.workspace_root,
        workspace_id,
    )
    if not workspace_root.is_dir():
        raise KeyError(f"workspace not found: {workspace_id}")
    if not (workspace_root / ".git").exists():
        raise ValueError(f"workspace {operation} requires a Git workspace")
    return workspace_root


def _git_workspace_diff(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_id: str,
    workspace_root: Path,
) -> WorkspaceDiff:
    index_env = _prepare_git_result_index(provider_config, workspace_root)
    try:
        name_status_output = _run_git_workspace_command(
            provider_config,
            workspace_root,
            ["diff", "--cached", "--name-status", "--find-renames", "-z", "HEAD", "--"],
            "git diff --cached --name-status",
            extra_env=index_env,
        )
        numstat_output = _run_git_workspace_command(
            provider_config,
            workspace_root,
            ["diff", "--cached", "--numstat", "HEAD", "--"],
            "git diff --cached --numstat",
            extra_env=index_env,
        )
    finally:
        _remove_git_result_index(index_env)
    numstat = _parse_git_numstat(numstat_output)
    files = _parse_git_name_status(name_status_output, numstat)
    additions = sum(item[0] for item in numstat.values() if item[0] is not None)
    deletions = sum(item[1] for item in numstat.values() if item[1] is not None)
    return WorkspaceDiff(
        workspace_id=workspace_id,
        files=files,
        additions=additions,
        deletions=deletions,
    )


def _git_workspace_patch(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_root: Path,
) -> str:
    index_env = _prepare_git_result_index(provider_config, workspace_root)
    try:
        return _run_git_workspace_command(
            provider_config,
            workspace_root,
            ["diff", "--cached", "--binary", "HEAD", "--"],
            "git diff --cached",
            extra_env=index_env,
        )
    finally:
        _remove_git_result_index(index_env)


def _prepare_git_result_index(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_root: Path,
) -> dict[str, str]:
    git_dir = workspace_root / ".git"
    with tempfile.NamedTemporaryFile(
        prefix="coding-agent-result-index-",
        dir=git_dir if git_dir.is_dir() else None,
        delete=False,
    ) as temp_index:
        temp_index_path = Path(temp_index.name)

    existing_index = git_dir / "index"
    if existing_index.is_file():
        shutil.copy2(existing_index, temp_index_path)
    index_env = {"GIT_INDEX_FILE": str(temp_index_path)}
    if not existing_index.is_file():
        _run_git_workspace_command(
            provider_config,
            workspace_root,
            ["read-tree", "HEAD"],
            "git read-tree",
            extra_env=index_env,
        )
    _run_git_workspace_command(
        provider_config,
        workspace_root,
        ["add", "-A"],
        "git add",
        extra_env=index_env,
    )
    return index_env


def _remove_git_result_index(index_env: dict[str, str]) -> None:
    index_path = Path(index_env["GIT_INDEX_FILE"])
    try:
        index_path.unlink()
    except FileNotFoundError:
        return


def _publish_git_workspace_branch(
    provider_config: _DockerWorkspaceProviderConfig,
    publication_config: dict[str, object],
    workspace_id: str,
    workspace_root: Path,
    branch_name: str,
    commit_message: str,
) -> WorkspaceBranchPublication:
    author_name = _required_publication_string(
        publication_config,
        "git_author_name",
    )
    author_email = _required_publication_string(
        publication_config,
        "git_author_email",
    )
    branch_name = branch_name.strip()
    if not branch_name:
        raise ValueError("branch_name must be non-empty")
    commit_message = commit_message.strip()
    if not commit_message:
        raise ValueError("commit_message must be non-empty")

    status = _run_git_publish_command(
        provider_config,
        workspace_root,
        ["status", "--porcelain=v1", "-z"],
        "git status",
    )
    if not status:
        raise ValueError("workspace publication requires uncommitted changes")

    _run_git_publish_command(
        provider_config,
        workspace_root,
        ["check-ref-format", "--branch", branch_name],
        "git check-ref-format",
    )
    remote_url = _run_git_publish_command(
        provider_config,
        workspace_root,
        ["config", "--get", "remote.origin.url"],
        "git config remote.origin.url",
    ).strip()
    _validate_publication_remote_url(publication_config, remote_url)
    push_env = _git_publication_push_env(publication_config)
    _run_git_publish_command(
        provider_config,
        workspace_root,
        ["config", "user.name", author_name],
        "git config user.name",
    )
    _run_git_publish_command(
        provider_config,
        workspace_root,
        ["config", "user.email", author_email],
        "git config user.email",
    )
    _run_git_publish_command(
        provider_config,
        workspace_root,
        ["add", "-A"],
        "git add",
    )
    _run_git_publish_command(
        provider_config,
        workspace_root,
        ["commit", "-m", commit_message],
        "git commit",
    )
    commit_sha = _run_git_publish_command(
        provider_config,
        workspace_root,
        ["rev-parse", "HEAD"],
        "git rev-parse",
    ).strip()
    pushed_ref = f"refs/heads/{branch_name}"
    redacted_remote_url = _redact_git_remote_url(remote_url)
    try:
        _run_git_publish_command(
            provider_config,
            workspace_root,
            ["push", "origin", f"HEAD:{pushed_ref}"],
            "git push",
            extra_env=push_env,
        )
    except ValueError as exc:
        return WorkspaceBranchPublication(
            workspace_id=workspace_id,
            branch_name=branch_name,
            pushed_ref=pushed_ref,
            commit_sha=commit_sha,
            remote_url=redacted_remote_url,
            status="partial",
            error=str(exc),
        )
    return WorkspaceBranchPublication(
        workspace_id=workspace_id,
        branch_name=branch_name,
        pushed_ref=pushed_ref,
        commit_sha=commit_sha,
        remote_url=redacted_remote_url,
    )


def _run_git_publish_command(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_root: Path,
    args: list[str],
    operation: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> str:
    try:
        return _run_git_workspace_command(
            provider_config,
            workspace_root,
            args,
            operation,
            extra_env=extra_env,
        )
    except subprocess.CalledProcessError as exc:
        error = ValueError(f"{operation} failed")
        for note in getattr(exc, "__notes__", ()):
            error.add_note(note)
        raise error from exc


def _git_publication_push_env(
    publication_config: dict[str, object],
) -> dict[str, str]:
    token_env = _optional_string(publication_config.get("git_token_env"), None)
    if token_env is None:
        return {}
    token = os.environ.get(token_env)
    if token is None or not token.strip():
        raise ValueError(f"remote_publication.git_token_env is not set: {token_env}")
    credential = base64.b64encode(
        f"x-access-token:{token.strip()}".encode("utf-8")
    ).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credential}",
    }


def _redact_git_remote_url(remote_url: str) -> str:
    parsed = urlsplit(remote_url)
    if parsed.hostname is None:
        return remote_url
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            "",
            "",
        )
    )


def _validate_publication_remote_url(
    publication_config: dict[str, object],
    remote_url: str,
) -> None:
    if not remote_url:
        raise ValueError("Git workspace has no remote.origin.url")
    parsed = urlsplit(remote_url)
    if parsed.scheme not in {"https", "git+https"}:
        raise ValueError("Git workspace remote.origin.url must use https")
    if parsed.hostname is None:
        raise ValueError("Git workspace remote.origin.url must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Git workspace remote.origin.url must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "Git workspace remote.origin.url must not include query or fragment"
        )
    allowed_git_hosts = publication_config.get("allowed_git_hosts")
    if allowed_git_hosts is None:
        raise ValueError("remote_publication.allowed_git_hosts must be configured")
    allowed_hosts = _publication_string_list(allowed_git_hosts, key="allowed_git_hosts")
    if not allowed_hosts:
        raise ValueError("remote_publication.allowed_git_hosts must be configured")
    if parsed.hostname not in allowed_hosts:
        raise ValueError(
            "Git workspace remote.origin.url host is not in remote_publication.allowed_git_hosts"
        )


def _publication_string_list(value: object, *, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"remote_publication.{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"remote_publication.{key} must be a list of strings")
        result.append(item.strip().lower())
    return tuple(result)


def _required_publication_string(
    config: dict[str, object],
    key: str,
) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"remote_publication.{key} must be configured")
    return value.strip()


def _run_git_workspace_command(
    provider_config: _DockerWorkspaceProviderConfig,
    workspace_root: Path,
    args: list[str],
    operation: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> str:
    command = [
        provider_config.git_binary,
        "-C",
        str(workspace_root),
        *args,
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_GIT_OPERATION_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(extra_env or {})},
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _add_git_failure_output_notes(exc, operation, exc.stdout, exc.stderr)
        raise
    return result.stdout


def _parse_git_name_status(
    output: str,
    numstat: dict[str, tuple[int | None, int | None, bool]],
) -> list[WorkspaceDiffFile]:
    tokens = [token for token in output.split("\0") if token]
    files: list[WorkspaceDiffFile] = []
    index = 0
    while index < len(tokens):
        status_and_path = tokens[index]
        index += 1
        status_parts = status_and_path.split("\t", 1)
        if len(status_parts) == 2:
            status_token, first_path = status_parts
        else:
            status_token = status_and_path
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            first_path = tokens[index]
            index += 1
        status_code = status_token[:1]
        old_path: str | None = None
        if status_code in {"R", "C"}:
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            old_path = first_path
            path = tokens[index]
            index += 1
            status: WorkspaceDiffStatus = "renamed"
        else:
            path = first_path
            status = _workspace_diff_status_from_git_status(status_code)

        additions, deletions, binary = numstat.get(path, (None, None, False))
        files.append(
            WorkspaceDiffFile(
                path=path,
                status=status,
                old_path=old_path,
                additions=additions,
                deletions=deletions,
                binary=binary,
            )
        )
    return files


def _workspace_diff_status_from_git_status(status_code: str) -> WorkspaceDiffStatus:
    if status_code == "A":
        return "added"
    if status_code == "M":
        return "modified"
    if status_code == "D":
        return "deleted"
    return "unknown"


def _parse_git_numstat(output: str) -> dict[str, tuple[int | None, int | None, bool]]:
    result: dict[str, tuple[int | None, int | None, bool]] = {}
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise ValueError("git diff numstat output is malformed")
        raw_additions, raw_deletions, path = parts
        if raw_additions == "-" or raw_deletions == "-":
            result[path] = (None, None, True)
            continue
        result[path] = (int(raw_additions), int(raw_deletions), False)
    return result


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


def _setup_container_name(
    provider_config: _DockerWorkspaceProviderConfig, workspace_id: str
) -> str:
    return f"{_container_name(provider_config, workspace_id)}-setup"


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


def _clone_git_workspace_source(
    provider_config: _DockerWorkspaceProviderConfig,
    config: dict[str, object],
    source: CloudWorkspaceSource,
    workspace_root: Path,
) -> None:
    remote_url = source.get("remote_url")
    base_ref = source.get("base_ref")
    base_sha = source.get("base_sha")
    if not isinstance(remote_url, str) or not remote_url.strip():
        raise ValueError("workspace_source.remote_url is required for kind=git")
    if not isinstance(base_ref, str) or not base_ref.strip():
        raise ValueError("workspace_source.base_ref is required for kind=git")
    if not isinstance(base_sha, str) or not base_sha.strip():
        raise ValueError("workspace_source.base_sha is required for kind=git")
    remote_url = remote_url.strip()
    base_ref = base_ref.strip()
    base_sha = base_sha.strip()
    if _GIT_SHA_RE.fullmatch(base_sha) is None:
        raise ValueError("workspace_source.base_sha must be a hex git commit SHA")
    _validate_git_source_remote_url(config, remote_url)

    clone_command = [
        provider_config.git_binary,
        "clone",
        "--no-checkout",
        "--branch",
        base_ref,
        "--",
        remote_url,
        str(workspace_root),
    ]
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        _ = subprocess.run(
            clone_command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_GIT_OPERATION_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=git_env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _add_git_failure_output_notes(exc, "git clone", exc.stdout, exc.stderr)
        raise
    checkout_command = [
        provider_config.git_binary,
        "-C",
        str(workspace_root),
        "checkout",
        "--detach",
        base_sha,
    ]
    try:
        _ = subprocess.run(
            checkout_command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_GIT_OPERATION_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=git_env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _add_git_failure_output_notes(exc, "git checkout", exc.stdout, exc.stderr)
        raise


def _validate_git_source_remote_url(config: dict[str, object], remote_url: str) -> None:
    parsed = urlsplit(remote_url)
    if parsed.scheme not in {"https", "git+https"}:
        raise ValueError("workspace_source.remote_url must use https")
    if parsed.hostname is None:
        raise ValueError("workspace_source.remote_url must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("workspace_source.remote_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "workspace_source.remote_url must not include query or fragment"
        )

    remote_sources = config.get("remote_sources")
    git_sources = (
        remote_sources.get("git") if isinstance(remote_sources, dict) else None
    )
    if not isinstance(git_sources, dict):
        raise ValueError("remote_sources.git.allowed_hosts must be configured")
    allowed_hosts_raw = git_sources.get("allowed_hosts")
    allowed_hosts = _remote_source_string_list(
        allowed_hosts_raw,
        key="allowed_hosts",
    )
    if not allowed_hosts:
        raise ValueError("remote_sources.git.allowed_hosts must be configured")
    if parsed.hostname.lower() not in allowed_hosts:
        raise ValueError(
            "workspace_source.remote_url host is not in remote_sources.git.allowed_hosts"
        )


def _remote_source_string_list(value: object, *, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"remote_sources.git.{key} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"remote_sources.git.{key} must be a list of strings")
        result.append(item.strip().lower())
    return tuple(result)


def _add_git_failure_output_notes(
    exc: BaseException,
    operation: str,
    stdout: object,
    stderr: object,
) -> None:
    stdout_text = _redact_setup_output(stdout, {})
    stderr_text = _redact_setup_output(stderr, {})
    if stdout_text:
        exc.add_note(f"{operation} stdout:\n{stdout_text}")
    if stderr_text:
        exc.add_note(f"{operation} stderr:\n{stderr_text}")


def _run_docker_setup_phase_if_configured(
    provider_config: _DockerWorkspaceProviderConfig,
    config: dict[str, object],
    binding: CloudWorkspaceRef,
    *,
    on_docker_invoked: Callable[[], None] | None = None,
) -> None:
    remote_phases = config.get("remote_phases")
    if not isinstance(remote_phases, dict):
        return
    setup_phase = remote_phases.get(_REMOTE_PHASE_SETUP_KEY)
    if not isinstance(setup_phase, dict):
        return
    if setup_phase.get("enabled") is not True:
        return

    command = " && ".join(_setup_phase_commands(setup_phase.get("commands")))
    setup_network = setup_phase.get("network")
    if setup_network not in {"none", "bridge"}:
        raise ValueError('remote_phases.setup.network must be "none" or "bridge"')
    setup_timeout = setup_phase.get("timeout_seconds")
    if (
        not isinstance(setup_timeout, int)
        or isinstance(setup_timeout, bool)
        or setup_timeout <= 0
    ):
        raise ValueError(
            "remote_phases.setup.timeout_seconds must be a positive integer"
        )

    secret_env_allowlist = setup_phase.get("secret_env_allowlist")
    if secret_env_allowlist is None:
        env = {}
    elif isinstance(secret_env_allowlist, list):
        env = _setup_phase_env(secret_env_allowlist)
    else:
        raise ValueError(
            "remote_phases.setup.secret_env_allowlist must be a list of strings"
        )
    setup_command = [
        provider_config.docker_binary,
        "run",
        "--rm",
        "--name",
        _setup_container_name(provider_config, binding.workspace_id),
        "--network",
        str(setup_network),
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
        f"{_workspace_root_for_id(provider_config.workspace_root, binding.workspace_id)}:{provider_config.container_workspace_root}",
        "-w",
        provider_config.container_workspace_root,
    ]
    if provider_config.exec_user is not None:
        setup_command.extend(["--user", provider_config.exec_user])
    for key in env:
        setup_command.extend(["-e", key])
    setup_command.extend([provider_config.image, "/bin/sh", "-c", command])
    try:
        if on_docker_invoked is not None:
            on_docker_invoked()
        _ = subprocess.run(
            setup_command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=setup_timeout,
            env=None,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        _add_setup_failure_output_notes(exc, exc.stdout, exc.stderr, env)
        raise
    except subprocess.TimeoutExpired as exc:
        _add_setup_failure_output_notes(exc, exc.stdout, exc.stderr, env)
        raise


def _setup_phase_env(secret_env_allowlist: list[object]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in secret_env_allowlist:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("remote_phases.setup.secret_env_allowlist must be strings")
        key = item.strip()
        if _ENV_NAME_RE.fullmatch(key) is None:
            raise ValueError(
                "remote_phases.setup.secret_env_allowlist entries must be valid environment variable names"
            )
        value = os.environ.get(key)
        if value is None:
            raise ValueError(
                f"remote_phases.setup.secret_env_allowlist environment variable is missing: {key}"
            )
        env[key] = value
    return env


def _add_setup_failure_output_notes(
    exc: BaseException,
    stdout: object,
    stderr: object,
    secret_env: dict[str, str],
) -> None:
    stdout_text = _redact_setup_output(stdout, secret_env)
    stderr_text = _redact_setup_output(stderr, secret_env)
    if stdout_text:
        exc.add_note(f"setup phase stdout:\n{stdout_text}")
    if stderr_text:
        exc.add_note(f"setup phase stderr:\n{stderr_text}")


def _redact_setup_output(value: object, secret_env: dict[str, str]) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    else:
        text = str(value)
    for secret in secret_env.values():
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _setup_phase_commands(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("remote_phases.setup.enabled=true requires non-empty commands")
    commands: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError("remote_phases.setup.commands must be non-empty strings")
        commands.append(item.strip())
    return commands


def _start_docker_workspace_container(
    provider_config: _DockerWorkspaceProviderConfig,
    binding: CloudWorkspaceRef,
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
    cleanup_error: BaseException | None = None
    for container_name in (
        _setup_container_name(provider_config, workspace_id),
        _container_name(provider_config, workspace_id),
    ):
        try:
            _remove_docker_container(provider_config, container_name)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
            logger.exception(
                "Docker workspace container cleanup failed container=%s",
                container_name,
            )
    if cleanup_error is not None:
        raise cleanup_error


def _remove_docker_container(
    provider_config: _DockerWorkspaceProviderConfig,
    container_name: str,
) -> None:
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
