from __future__ import annotations

import shlex
import subprocess
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from coding_agent.verification.contract import VerificationContract


class VerificationStepResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    command: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str


class VerificationReport(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    verdict: str
    steps: list[VerificationStepResult]


class ChecklistRenderResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    text: str


class VerificationRunner:
    def run(self, contract: VerificationContract) -> VerificationReport:
        results: list[VerificationStepResult] = []

        for step in contract.steps:
            completed = subprocess.run(
                shlex.split(step.command),
                check=False,
                capture_output=True,
                text=True,
            )
            results.append(
                VerificationStepResult(
                    name=step.name,
                    command=step.command,
                    passed=completed.returncode == 0,
                    exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            )

        verdict = "VERIFIED" if all(step.passed for step in results) else "NOT VERIFIED"
        return VerificationReport(verdict=verdict, steps=results)

    def render_checklist(self, contract: VerificationContract) -> ChecklistRenderResult:
        lines = ["Verification Checklist", "======================", ""]
        for index, step in enumerate(contract.steps, start=1):
            lines.append(f"{index}. {step.name}")
            lines.append(f"   $ {step.command}")
        lines.append("")
        lines.append("Pass criteria: all listed commands exit with status 0.")
        return ChecklistRenderResult(text="\n".join(lines))
