"""Shell execution tool."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from agentkit.tools import tool

_DISALLOWED_TOKENS = {"&&", "||", "|", ";", ">", ">>", "<", "2>", "&"}


def register_shell_tools(registry: Any, cwd: Path | str = ".") -> None:
    pass


def _parse_command(command: str) -> list[str]:
    args = shlex.split(command)
    if not args:
        raise ValueError("Command cannot be empty")
    if any(token in _DISALLOWED_TOKENS for token in args):
        raise ValueError("Unsupported shell syntax in command")
    if args[0] == "python" and which("python") is None and which("python3") is not None:
        args[0] = "python3"
    return args


@tool(description="Run a shell command and return stdout/stderr.")
def bash_run(
    command: str,
    timeout: int = 120,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    try:
        changed_dir = _apply_cd(command, cwd)
        if changed_dir is not None:
            return f"Changed directory to {changed_dir}"

        exported = _apply_export(command)
        if exported is not None:
            key, value = exported
            return f"Exported {key}={value}"

        args = _parse_command(command)
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=_build_env(env),
        )
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
    args = shlex.split(command)
    if not args or args[0] != "export":
        return None
    if len(args) != 2 or "=" not in args[1]:
        raise ValueError("export requires KEY=VALUE")
    key, value = args[1].split("=", 1)
    if not key:
        raise ValueError("export requires a non-empty variable name")
    return key, value
