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
_BULLET_COMMAND = re.compile(r"^\s*-\s*`(?P<command>.+)`\s*$")


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

    commands: list[str] = []
    for line in lines[header_index + 1 :]:
        if not line.strip():
            if commands:
                break
            continue
        if not line.startswith("-") and not line.startswith(" "):
            break
        match = _BULLET_COMMAND.match(line)
        if match is None:
            continue
        commands.append(match.group("command"))

    if not commands:
        raise ValueError("Target tests section must include at least one command")

    return VerificationContract(
        source_path=path,
        steps=[
            VerificationStep(name=f"Target test {index}", command=command)
            for index, command in enumerate(commands, start=1)
        ],
    )
