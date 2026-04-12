from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import importlib
import platform
import re
import shlex
import subprocess
from pathlib import Path
from shutil import which
from types import ModuleType
from typing import cast

from agentkit.config.loader import load_config
from agentkit.tools import tool

_DISALLOWED_TOKENS = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "&"}
_STRUCTURED_RESULTS: ContextVar[bool] = ContextVar(
    "coding_agent_shell_structured_results", default=False
)


def register_shell_tools(registry: object, cwd: Path | str = ".") -> None:
    del registry, cwd


@contextmanager
def structured_results_scope(enabled: bool):
    token = _STRUCTURED_RESULTS.set(enabled)
    try:
        yield
    finally:
        _STRUCTURED_RESULTS.reset(token)


def _structured_results_enabled() -> bool:
    return _STRUCTURED_RESULTS.get()


def _structured_shell_result(
    result: subprocess.CompletedProcess[str],
) -> dict[str, str | int]:
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


@lru_cache(maxsize=1)
def _default_shell_config() -> dict[str, object]:
    config_path = Path(__file__).resolve().parent.parent / "agent.toml"
    extra = load_config(config_path).extra
    raw_shell_config: object = extra["shell"] if "shell" in extra else {}
    return _normalize_shell_config(raw_shell_config)


def _normalize_shell_config(raw_shell_config: object) -> dict[str, object]:
    if not isinstance(raw_shell_config, dict):
        raise ValueError("shell config must be a dict")
    typed_shell_config = cast(dict[object, object], raw_shell_config)
    normalized: dict[str, object] = {}
    for key, value in typed_shell_config.items():
        normalized[str(key)] = value
    return normalized


def _load_sandbox_module() -> ModuleType:
    try:
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
    except ImportError as exc:
        raise ValueError(
            "Sandbox mode configured but coding_agent.tools.sandbox is unavailable"
        ) from exc
    return cast(ModuleType, sandbox_module)


def _sandbox_mode(shell_config: dict[str, object]) -> str:
    mode_value = shell_config.get("sandbox_mode", "none")
    if not isinstance(mode_value, str):
        raise ValueError("sandbox_mode must be a string")
    if mode_value not in ("none", "nsjail", "docker"):
        raise ValueError(f"Unsupported sandbox mode: {mode_value}")
    return mode_value


def _parse_command(command: str) -> list[str]:
    args = shlex.split(command)
    if not args:
        raise ValueError("Command cannot be empty")
    # bash_run intentionally executes a single command without shell parsing.
    # REPL `!` shell mode uses BashExecutor/create_subprocess_shell instead, so
    # operators like &&, pipes, redirects, and backgrounding work there but are
    # rejected here for predictable, injection-resistant tool execution.
    if any(token in _DISALLOWED_TOKENS for token in args):
        raise ValueError(
            "Unsupported shell syntax in command. Run commands separately; "
            "bash_run does not support &&, ||, |, ;, redirects, or backgrounding."
        )
    if args[0] == "python" and which("python") is None and which("python3") is not None:
        args[0] = "python3"
    return args


def _pipeline_shell_config(__pipeline_ctx__: object | None) -> dict[str, object]:
    defaults = _default_shell_config()
    if __pipeline_ctx__ is None:
        return _default_shell_config_for_execution(defaults)
    config = getattr(__pipeline_ctx__, "config", None)
    if not isinstance(config, dict):
        raise ValueError("pipeline context config must be a dict")
    typed_config = cast(dict[str, object], config)
    raw_shell_config = typed_config.get("shell", {})
    merged = _default_shell_config_for_execution(defaults)
    merged.update(_normalize_shell_config(raw_shell_config))
    return merged


def _default_shell_config_for_execution(
    shell_config: dict[str, object],
) -> dict[str, object]:
    normalized = dict(shell_config)
    if _uses_unsupported_default_memory_limit(normalized):
        del normalized["memory_limit_mb"]
    return normalized


def _uses_unsupported_default_memory_limit(shell_config: dict[str, object]) -> bool:
    return (
        shell_config.get("sandbox_mode", "none") == "none"
        and shell_config.get("memory_limit_mb") is not None
        and platform.system() == "Darwin"
    )


def _resolve_workspace_root(
    cwd: str | None,
    __pipeline_ctx__: object | None,
    shell_config: dict[str, object],
) -> Path:
    raw_pipeline_config = (
        getattr(__pipeline_ctx__, "config", {}) if __pipeline_ctx__ else {}
    )
    if not isinstance(raw_pipeline_config, dict):
        raise ValueError("pipeline context config must be a dict")
    pipeline_config = cast(dict[str, object], raw_pipeline_config)
    workspace_root = pipeline_config.get("workspace_root")
    if workspace_root is None:
        workspace_root = shell_config.get("workspace_root")
    if workspace_root is None:
        workspace_root = cwd or "."
    if not isinstance(workspace_root, (str, Path)):
        raise ValueError("workspace_root must be a string or path")
    return Path(str(workspace_root)).expanduser().resolve()


def _sandbox_config(*, cwd: str | None, __pipeline_ctx__: object | None) -> object:
    shell_config = _pipeline_shell_config(__pipeline_ctx__)
    workspace_root = _resolve_workspace_root(cwd, __pipeline_ctx__, shell_config)
    mode_value = _sandbox_mode(shell_config)
    sandbox_module = _load_sandbox_module()
    return sandbox_module.SandboxConfig(
        mode=mode_value,
        workspace_root=workspace_root,
        limits=sandbox_module.SandboxLimits(
            cpu_limit_seconds=_optional_int(shell_config.get("cpu_limit_seconds")),
            memory_limit_mb=_optional_int(shell_config.get("memory_limit_mb")),
        ),
        docker_image=str(shell_config.get("docker_image", "python:3.11-slim")),
    )


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid integer limits")
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"unsupported integer value: {value!r}")
    return int(value)


def _sandbox_request(
    *, args: list[str], cwd: str | None, env: dict[str, str] | None, timeout: int
) -> object:
    sandbox_module = _load_sandbox_module()
    return sandbox_module.SandboxRequest(
        args=args,
        cwd=Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve(),
        env=env,
        timeout_seconds=timeout,
    )


def _validated_execution_cwd(
    *, cwd: str | None, __pipeline_ctx__: object | None, shell_config: dict[str, object]
) -> str | None:
    if cwd is None:
        return None
    workspace_root = _resolve_workspace_root(cwd, __pipeline_ctx__, shell_config)
    resolved_cwd = Path(cwd).expanduser().resolve()
    try:
        resolved_cwd.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(
            f"Working directory is outside sandbox workspace: {resolved_cwd}"
        ) from exc
    return str(resolved_cwd)


def _validate_no_path_escape(args: list[str], workspace_root: Path) -> None:
    for arg in args[1:]:
        for match in re.findall(r"/[A-Za-z0-9_./-]+", arg):
            candidate = Path(match).expanduser().resolve()
            try:
                candidate.relative_to(workspace_root)
            except ValueError as exc:
                raise ValueError(
                    f"Path is outside sandbox workspace: {candidate}"
                ) from exc


@tool(description="Run a shell command and return stdout/stderr.")
def bash_run(
    command: str,
    timeout: int = 120,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    __pipeline_ctx__: object | None = None,
) -> str | dict[str, str | int]:
    try:
        shell_config = _pipeline_shell_config(__pipeline_ctx__)
        mode_value = _sandbox_mode(shell_config)
        execution_cwd = _validated_execution_cwd(
            cwd=cwd,
            __pipeline_ctx__=__pipeline_ctx__,
            shell_config=shell_config,
        )
        changed_dir = _apply_cd(command, cwd)
        if changed_dir is not None:
            workspace_root = _resolve_workspace_root(
                cwd, __pipeline_ctx__, shell_config
            )
            if mode_value != "none":
                sandbox_module = _load_sandbox_module()
                sandbox_module._validate_cwd(Path(changed_dir), workspace_root)
            else:
                try:
                    Path(changed_dir).relative_to(workspace_root)
                except ValueError as exc:
                    raise ValueError(
                        f"Directory is outside sandbox workspace: {changed_dir}"
                    ) from exc
            return f"Changed directory to {changed_dir}"

        exported = _apply_export(command)
        if exported is not None:
            key, value = exported
            if env is not None:
                env[key] = value
            return f"Exported {key}={value}"

        args = _parse_command(command)
        if mode_value == "none":
            workspace_root = _resolve_workspace_root(
                execution_cwd or cwd, __pipeline_ctx__, shell_config
            )
            _validate_no_path_escape(args, workspace_root)
            result = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=execution_cwd,
                env=_build_env(env),
            )
        else:
            sandbox_module = _load_sandbox_module()
            sandbox = sandbox_module.build_sandbox(
                _sandbox_config(cwd=execution_cwd, __pipeline_ctx__=__pipeline_ctx__)
            )
            result = sandbox.run(
                _sandbox_request(
                    args=args,
                    cwd=execution_cwd,
                    env=env,
                    timeout=timeout,
                )
            )
        if _structured_results_enabled():
            return _structured_shell_result(result)
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def _build_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    import os

    merged = dict(os.environ)
    merged.update(env)
    return merged


def _apply_cd(command: str, cwd: str | None) -> str | None:
    args = shlex.split(command)
    if not args or args[0] != "cd":
        return None
    if len(args) != 2:
        raise ValueError("cd requires exactly one target directory")
    target = Path(args[1]).expanduser()
    base = Path(cwd).expanduser() if cwd else Path.cwd()
    resolved = (
        (base / target).resolve() if not target.is_absolute() else target.resolve()
    )
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Directory not found: {args[1]}")
    return str(resolved)


def _apply_export(command: str) -> tuple[str, str] | None:
    """Parse ``export KEY=VALUE``.

    Returns *(key, value)* if the command is an export, else *None*.
    The caller is responsible for persisting the variable — typically
    ``CoreToolsPlugin._sync_shell_session`` stores it in the shell
    session context, and ``bash_run`` writes it into the provided
    ``env`` dict when one is supplied.
    """
    args = shlex.split(command)
    if not args or args[0] != "export":
        return None
    if len(args) != 2 or "=" not in args[1]:
        raise ValueError("export requires KEY=VALUE")
    key, value = args[1].split("=", 1)
    if not key:
        raise ValueError("export requires a non-empty variable name")
    return key, value
