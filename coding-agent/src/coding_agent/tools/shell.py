"""Shell execution tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agentkit.tools import tool


def register_shell_tools(registry: Any, cwd: Path | str = ".") -> None:
    pass


@tool(description="Run a shell command and return stdout/stderr.")
def bash_run(command: str, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
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
