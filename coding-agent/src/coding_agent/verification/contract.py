from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class VerificationStep(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    command: str


class VerificationContract(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    source_path: Path
    steps: list[VerificationStep]


_TARGET_TESTS_HEADER = re.compile(r"^Target tests:\s*$", re.MULTILINE)
_BULLET_COMMAND = re.compile(
    r"^(?P<indent>[ \t]*)-\s*(?:`(?P<backticked>.*)`|(?P<plain>.*))\s*$"
)


def load_task_packet_contract(path: Path) -> VerificationContract:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    header_index: int | None = None
    for index, line in enumerate(lines):
        if _TARGET_TESTS_HEADER.match(line):
            header_index = index
            break

    if header_index is None:
        raise ValueError("Task packet must include a Target tests section")

    section_lines: list[str] = []
    saw_section_content = False
    for line in lines[header_index + 1 :]:
        if not line.strip():
            if saw_section_content:
                break
            continue

        stripped = line.lstrip(" ")
        if line.startswith((" ", "\t")) or stripped.startswith("-"):
            section_lines.append(line)
            saw_section_content = True
            continue
        break

    allowed_top_level_indents: set[int] | None = None
    commands: list[str] = []
    for line in section_lines:
        match = _BULLET_COMMAND.match(line)
        if match is None:
            continue

        indent = match.group("indent")
        if "\t" in indent:
            continue

        indent_width = len(indent)
        if indent_width > 3:
            continue

        command = match.group("backticked") or match.group("plain") or ""
        stripped_command = command.strip()
        if not stripped_command or stripped_command.startswith("-"):
            continue

        if allowed_top_level_indents is None:
            lower_bound = max(0, indent_width - 1)
            upper_bound = min(3, indent_width + 1)
            allowed_top_level_indents = set(range(lower_bound, upper_bound + 1))

        if indent_width not in allowed_top_level_indents:
            continue

        commands.append(stripped_command)

    if not commands:
        raise ValueError("Target tests section must include at least one command")

    return VerificationContract(
        source_path=path,
        steps=[
            VerificationStep(name=f"Target test {index}", command=command)
            for index, command in enumerate(commands, start=1)
        ],
    )
