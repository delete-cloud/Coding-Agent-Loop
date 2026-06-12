from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
import platform
import re
from string import ascii_letters, digits
from shutil import which
import subprocess
from typing import Literal, Protocol, cast

try:
    import resource
except ImportError:
    resource = None

SandboxMode = Literal["none", "native", "podman", "docker"]


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
    additional_roots: tuple[Path, ...] = ()
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
    if config.mode == "native":
        return _resolve_native(config)
    if config.mode == "podman":
        return PodmanSandboxRunner(config)
    if config.mode == "docker":
        return DockerSandboxRunner(config)
    raise ValueError(f"Unsupported sandbox mode: {config.mode}")


def _resolve_native(config: SandboxConfig) -> SandboxRunner:
    """Resolve ``sandbox_mode=native`` to a platform-specific backend (ADR-0060).

    ``native`` is a platform resolution, not a single backend: macOS uses a
    Codex-style Seatbelt profile, Linux uses a Codex-style bubblewrap jail.
    Unsupported platforms fail closed rather than silently downgrading.
    """
    system = platform.system()
    if system == "Darwin":
        return MacosSeatbeltSandboxRunner(config)
    if system == "Linux":
        return LinuxNativeSandboxRunner(config)
    raise SandboxUnavailableError(f"native sandbox mode is not supported on {system}")


class NoneSandboxRunner:
    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        cwd = _validate_cwd(
            request.cwd,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
        _validate_none_mode_limits(self._config.limits)
        _validate_none_mode_command_paths(
            request.args,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
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


class MacosSeatbeltSandboxRunner:
    """macOS native sandbox via ``sandbox-exec`` (ADR-0060, Codex-style).

    Deny-by-default Seatbelt profile: reads are broadly allowed, writes are
    confined to the workspace plus standard scratch devices, and network is
    denied (no network-allow rule is emitted). The command runs with ``cwd``
    pinned inside the workspace.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if which("sandbox-exec") is None:
            raise SandboxUnavailableError("sandbox-exec binary not found on PATH")

        cwd = _validate_cwd(
            request.cwd,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
        _validate_none_mode_limits(self._config.limits)
        profile = self._profile()
        command = ["sandbox-exec", "-p", profile, *request.args]
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            cwd=str(cwd),
            env=_native_sandbox_env(request.env),
            preexec_fn=_resource_limit_preexec(self._config.limits),
        )

    def _profile(self) -> str:
        writable_roots = [
            self._config.workspace_root.resolve(),
            *(root.resolve() for root in self._config.additional_roots),
        ]
        return "\n".join(
            [
                "(version 1)",
                "(deny default)",
                "(allow process-exec)",
                "(allow process-fork)",
                "(allow signal (target self))",
                "(allow sysctl-read)",
                "(allow file-read*)",
                "(allow file-write*",
                *[f'    (subpath "{root}")' for root in writable_roots],
                '    (subpath "/dev/null")',
                '    (subpath "/dev/stdout")',
                '    (subpath "/dev/stderr")',
                f'    (subpath "{_macos_tmpdir()}"))',
            ]
        )


class LinuxNativeSandboxRunner:
    """Linux native sandbox via bubblewrap (ADR-0060, Codex-style).

    The whole root filesystem is bind-mounted read-only, the workspace is
    bind-mounted read-write on top, network is unshared (denied by default),
    and the command runs with ``cwd`` pinned inside the workspace. Landlock is
    reserved as an internal hardening detail and is not a user-visible mode.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if which("bwrap") is None:
            raise SandboxUnavailableError("bubblewrap (bwrap) binary not found on PATH")

        cwd = _validate_cwd(
            request.cwd,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
        command = self._bwrap_command(request, cwd)
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=_native_sandbox_env(request.env),
            preexec_fn=_resource_limit_preexec(self._config.limits),
        )

    def _bwrap_command(self, request: SandboxRequest, cwd: Path) -> list[str]:
        workspace = str(self._config.workspace_root.resolve())
        command = [
            "bwrap",
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            workspace,
            workspace,
        ]
        for root in self._config.additional_roots:
            resolved = str(root.resolve())
            command.extend(["--bind", resolved, resolved])
        command.extend(["--chdir", str(cwd), "--"])
        command.extend(request.args)
        return command


class DockerSandboxRunner:
    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if which("docker") is None:
            raise SandboxUnavailableError("docker binary not found on PATH")

        cwd = _validate_cwd(
            request.cwd,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
        command = self._docker_command(request, cwd)
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=None,
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
        for root in self._config.additional_roots:
            resolved = root.resolve()
            command.extend(["--mount", f"type=bind,src={resolved},dst={resolved}"])
        if self._config.limits.cpu_limit_seconds is not None:
            command.extend(["--ulimit", f"cpu={self._config.limits.cpu_limit_seconds}"])
        if self._config.limits.memory_limit_mb is not None:
            command.extend(["--memory", f"{self._config.limits.memory_limit_mb}m"])
        for key, value in _docker_container_env(request.env).items():
            command.extend(["-e", f"{key}={value}"])
        command.append(self._config.docker_image)
        command.extend(request.args)
        return command


class PodmanSandboxRunner:
    """Explicit Podman container backend (ADR-0060).

    Mirrors the Docker backend's command shape with rootless-friendly hardening:
    no network, workspace bind mount, workdir pinned to ``cwd``, and
    ``no-new-privileges``. Podman is an explicit mode and is never selected by
    ``native``.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config: SandboxConfig = config

    def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
        if which("podman") is None:
            raise SandboxUnavailableError("podman binary not found on PATH")

        cwd = _validate_cwd(
            request.cwd,
            self._config.workspace_root,
            additional_roots=self._config.additional_roots,
        )
        command = self._podman_command(request, cwd)
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=None,
        )

    def _podman_command(self, request: SandboxRequest, cwd: Path) -> list[str]:
        workspace = self._config.workspace_root.resolve()
        command = [
            "podman",
            "run",
            "--rm",
            "--network",
            "none",
            "--security-opt",
            "no-new-privileges",
            "--workdir",
            str(cwd),
            "--mount",
            f"type=bind,src={workspace},dst={workspace}",
        ]
        for root in self._config.additional_roots:
            resolved = root.resolve()
            command.extend(["--mount", f"type=bind,src={resolved},dst={resolved}"])
        if self._config.limits.cpu_limit_seconds is not None:
            command.extend(["--ulimit", f"cpu={self._config.limits.cpu_limit_seconds}"])
        if self._config.limits.memory_limit_mb is not None:
            command.extend(["--memory", f"{self._config.limits.memory_limit_mb}m"])
        for key, value in _docker_container_env(request.env).items():
            command.extend(["-e", f"{key}={value}"])
        command.append(self._config.docker_image)
        command.extend(request.args)
        return command


def _macos_tmpdir() -> str:
    """Per-user temp dir used as a writable scratch root in the Seatbelt profile."""
    return str(Path(os.environ.get("TMPDIR", "/tmp")).resolve())


_NATIVE_ENV_KEYS = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TERM", "TZ"}


def _native_sandbox_env(env: dict[str, str] | None) -> dict[str, str]:
    """Build the environment for a native-sandboxed command (ADR-0060).

    Native backends run the command in the host process tree, so forwarding
    ``None`` would inherit every host variable — including secrets — into the
    sandbox. Honor the ADR's explicit/controlled-env contract: when no env is
    supplied, expose only a minimal allowlist pulled from the host; when env is
    supplied, validate names and use it verbatim with nothing else inherited.
    """
    if env is not None:
        for key in env:
            _validate_docker_env_name(key)
        return dict(env)
    return {key: os.environ[key] for key in _NATIVE_ENV_KEYS if key in os.environ}


def _validate_cwd(
    cwd: Path,
    workspace_root: Path,
    *,
    additional_roots: tuple[Path, ...] = (),
) -> Path:
    resolved_cwd = cwd.resolve()
    roots = (workspace_root.resolve(), *(root.resolve() for root in additional_roots))
    if not _path_under_any_root(resolved_cwd, roots):
        raise SandboxError(
            f"Working directory is outside sandbox workspace: {resolved_cwd}"
        )
    if not resolved_cwd.is_dir():
        raise SandboxError(f"Working directory does not exist: {resolved_cwd}")
    return resolved_cwd


def _path_under_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _resource_limit_preexec(limits: SandboxLimits):
    if os.name != "posix" or resource is None:
        return None
    if limits.cpu_limit_seconds is None and limits.memory_limit_mb is None:
        return None

    def apply_limits() -> None:
        resource_module = resource
        assert resource_module is not None
        if limits.cpu_limit_seconds is not None:
            resource_module.setrlimit(
                resource_module.RLIMIT_CPU,
                (limits.cpu_limit_seconds, limits.cpu_limit_seconds),
            )
        if limits.memory_limit_mb is not None:
            bytes_limit = limits.memory_limit_mb * 1024 * 1024
            resource_module.setrlimit(
                resource_module.RLIMIT_AS, (bytes_limit, bytes_limit)
            )

    return partial(apply_limits)


_DOCKER_ENV_KEYS = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TERM", "TZ"}


def _docker_container_env(env: dict[str, str] | None) -> dict[str, str]:
    if env is None:
        return {}
    validated: dict[str, str] = {}
    for key, value in env.items():
        _validate_docker_env_name(key)
        validated[key] = value
    return validated


def _validate_docker_env_name(name: str) -> None:
    if not name:
        raise SandboxError("Environment variable name cannot be empty")
    if name[0] not in ascii_letters + "_":
        raise SandboxError(f"Unsafe environment variable name: {name}")
    if any(char not in ascii_letters + digits + "_" for char in name[1:]):
        raise SandboxError(f"Unsafe environment variable name: {name}")


def _validate_none_mode_limits(limits: SandboxLimits) -> None:
    if limits.memory_limit_mb is None:
        return
    if platform.system() == "Darwin":
        raise SandboxUnavailableError(
            "memory limit is not supported for sandbox_mode=none on macOS"
        )


def _validate_none_mode_command_paths(
    args: list[str],
    workspace_root: Path,
    *,
    additional_roots: tuple[Path, ...] = (),
) -> None:
    roots = (workspace_root.resolve(), *(root.resolve() for root in additional_roots))
    for candidate in _absolute_path_candidates(args):
        resolved = Path(candidate).expanduser().resolve(strict=False)
        if not _path_under_any_root(resolved, roots):
            raise SandboxError(f"Command path escapes sandbox workspace: {resolved}")


def _validated_env_items(env: dict[str, str] | None) -> list[tuple[str, str]]:
    if env is None:
        return []
    items: list[tuple[str, str]] = []
    for key, value in env.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SandboxError(f"unsafe environment variable name: {key!r}")
        items.append((key, value))
    return items


def _absolute_path_candidates(args: list[str]) -> set[str]:
    candidates: set[str] = set()
    pattern = r"(?:(?<=^)|(?<=[\s(\[=,:\"']))(/[^\s\"')\],;]+)"
    for arg in args:
        matches = cast(list[str], re.findall(pattern, arg))
        for match in matches:
            candidates.add(match)
    return candidates
