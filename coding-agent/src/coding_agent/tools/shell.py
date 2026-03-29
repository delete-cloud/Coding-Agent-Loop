"""Shell execution tool."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
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
    return args


@tool(description="Run a shell command and return stdout/stderr.")
def bash_run(command: str, timeout: int = 120) -> str:
    try:
        args = _parse_command(command)
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
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
