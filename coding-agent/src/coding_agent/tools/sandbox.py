from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
import platform
import re
import resource
from shutil import which
import subprocess
from typing import Literal, Protocol, cast

SandboxMode = Literal["none", "nsjail", "docker"]


class SandboxError(RuntimeError):
    pass


class SandboxUnavailableError(SandboxError):
    pass


@dataclass(frozen=True)
class SandboxLimits:
    cpu_limit_seconds: int | None = None
    memory_limit_mb: int | None = None


@dataclass(frozen=True)
class SandboxConfig:
    mode: SandboxMode
    workspace_root: Path
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    docker_image: str = "python:3.11-slim"


@dataclass(frozen=True)
class SandboxRequest:
    args: list[str]
    cwd: Path
    env: dict[str, str] | None
    timeout_seconds: int


class SandboxRunner(Protocol):
    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]: ...


def build_sandbox(config: SandboxConfig) -> SandboxRunner:
    if config.mode == "none":
        return NoneSandboxRunner(config)
    if config.mode == "nsjail":
        return NsjailSandboxRunner(config)
    if config.mode == "docker":
        return DockerSandboxRunner(config)
    raise ValueError(f"Unsupported sandbox mode: {config.mode}")


class NoneSandboxRunner:
    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        cwd = _validate_cwd(request.cwd, self._config.workspace_root)
        _validate_none_mode_limits(self._config.limits)
        _validate_none_mode_command_paths(request.args, self._config.workspace_root)
        preexec_fn = _resource_limit_preexec(self._config.limits)
        return subprocess.run(
            request.args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            cwd=str(cwd),
            env=request.env,
            preexec_fn=preexec_fn,
        )


class NsjailSandboxRunner:
    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if platform.system() != "Linux":
            raise SandboxUnavailableError(
                "nsjail sandbox mode is only supported on Linux"
            )
        if which("nsjail") is None:
            raise SandboxUnavailableError("nsjail binary not found on PATH")

        cwd = _validate_cwd(request.cwd, self._config.workspace_root)
        command = self._nsjail_command(request, cwd)
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=request.env,
        )

    def _nsjail_command(self, request: SandboxRequest, cwd: Path) -> list[str]:
        command = [
            "nsjail",
            "--mode",
            "o",
            "--cwd",
            str(cwd),
            "--bindmount",
            f"{self._config.workspace_root}:{self._config.workspace_root}",
            "--disable_proc",
            "--iface_no_lo",
            "--",
        ]
        if self._config.limits.cpu_limit_seconds is not None:
            command[1:1] = ["--time_limit", str(self._config.limits.cpu_limit_seconds)]
        if self._config.limits.memory_limit_mb is not None:
            command[1:1] = [
                "--rlimit_as",
                str(self._config.limits.memory_limit_mb * 1024 * 1024),
            ]
        command.extend(request.args)
        return command


class DockerSandboxRunner:
    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if which("docker") is None:
            raise SandboxUnavailableError("docker binary not found on PATH")

        cwd = _validate_cwd(request.cwd, self._config.workspace_root)
        command = self._docker_command(request, cwd)
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=request.env,
        )

    def _docker_command(self, request: SandboxRequest, cwd: Path) -> list[str]:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--workdir",
            str(cwd),
            "--mount",
            f"type=bind,src={self._config.workspace_root},dst={self._config.workspace_root}",
        ]
        if self._config.limits.cpu_limit_seconds is not None:
            command.extend(["--ulimit", f"cpu={self._config.limits.cpu_limit_seconds}"])
        if self._config.limits.memory_limit_mb is not None:
            command.extend(["--memory", f"{self._config.limits.memory_limit_mb}m"])
        for key, value in (request.env or {}).items():
            command.extend(["-e", f"{key}={value}"])
        command.append(self._config.docker_image)
        command.extend(request.args)
        return command


def _validate_cwd(cwd: Path, workspace_root: Path) -> Path:
    resolved_cwd = cwd.resolve()
    resolved_root = workspace_root.resolve()
    try:
        _ = resolved_cwd.relative_to(resolved_root)
    except ValueError as exc:
        raise SandboxError(
            f"Working directory is outside sandbox workspace: {resolved_cwd}"
        ) from exc
    if not resolved_cwd.is_dir():
        raise SandboxError(f"Working directory does not exist: {resolved_cwd}")
    return resolved_cwd


def _resource_limit_preexec(limits: SandboxLimits):
    if os.name != "posix":
        return None
    if limits.cpu_limit_seconds is None and limits.memory_limit_mb is None:
        return None

    def apply_limits() -> None:
        if limits.cpu_limit_seconds is not None:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (limits.cpu_limit_seconds, limits.cpu_limit_seconds),
            )
        if limits.memory_limit_mb is not None:
            bytes_limit = limits.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))

    return partial(apply_limits)


def _validate_none_mode_limits(limits: SandboxLimits) -> None:
    if limits.memory_limit_mb is None:
        return
    if platform.system() == "Darwin":
        raise SandboxUnavailableError(
            "memory limit is not supported for sandbox_mode=none on macOS"
        )


def _validate_none_mode_command_paths(args: list[str], workspace_root: Path) -> None:
    for candidate in _absolute_path_candidates(args):
        resolved = Path(candidate).expanduser().resolve(strict=False)
        try:
            _ = resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise SandboxError(
                f"Command path escapes sandbox workspace: {resolved}"
            ) from exc


def _absolute_path_candidates(args: list[str]) -> set[str]:
    candidates: set[str] = set()
    pattern = r"(?:(?<=^)|(?<=[\s(\[=,:\"']))(/[^\s\"')\],;]+)"
    for arg in args:
        matches = cast(list[str], re.findall(pattern, arg))
        for match in matches:
            candidates.add(match)
    return candidates
