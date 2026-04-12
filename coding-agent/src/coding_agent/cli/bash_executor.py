from __future__ import annotations

import asyncio
import sys

from prompt_toolkit.output import Output
from rich.console import Console

from coding_agent.cli.terminal_output import print_pt


def is_bash_command(user_input: str) -> bool:
    stripped = user_input.strip()
    return stripped.startswith("!") and stripped != "!"


def extract_bash_command(user_input: str) -> str:
    return user_input[1:].strip()


class BashExecutor:
    def __init__(
        self,
        console: Console | None = None,
        cwd: str | None = None,
        output: Output | None = None,
    ) -> None:
        self.console = console or Console(
            file=sys.__stdout__, force_terminal=True, soft_wrap=False
        )
        self.cwd = cwd
        self.output = output

    async def execute(self, command: str) -> int:
        if not command.strip():
            return 0

        print_pt(f"$ {command}", output=self.output)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
        )

        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            print_pt(line.decode("utf-8", errors="replace"), output=self.output, end="")

        await proc.wait()
        exit_code = proc.returncode or 0

        if exit_code != 0:
            print_pt(f"exit code: {exit_code}", output=self.output)

        return exit_code
