from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Literal

from .command_policy import (
    CommandPolicyDecision,
    CommandPolicyVerdict,
    EnvironmentKind,
    evaluate_command_policy,
)


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    TIMED_OUT = "timed_out"
    SPAWN_ERROR = "spawn_error"


ExpectedSuccess = Literal["exit_code"]


@dataclass(frozen=True)
class ValidationCommandSpec:
    label: str
    command: str
    cwd: Path | str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int = 120
    expected_success: ExpectedSuccess = "exit_code"
    expected_exit_code: int = 0

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Validation command label must not be empty")
        if not self.command.strip():
            raise ValueError("Validation command must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("Validation command timeout must be positive")
        if self.expected_success != "exit_code":
            raise ValueError("Validation command expected_success must be exit_code")


@dataclass(frozen=True)
class ValidationOutcome:
    label: str
    status: ValidationStatus
    exit_code: int | None
    duration_ms: int
    policy: CommandPolicyVerdict
    expected_success: ExpectedSuccess
    expected_exit_code: int
    failure_summary: Mapping[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "policy": self.policy.to_safe_dict(),
            "expected_success": self.expected_success,
            "expected_exit_code": self.expected_exit_code,
            "failure_summary": dict(self.failure_summary),
        }


@dataclass(frozen=True)
class ValidationReport:
    status: ValidationStatus
    outcomes: tuple[ValidationOutcome, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "outcomes": [outcome.to_safe_dict() for outcome in self.outcomes],
        }


class ValidationRunner:
    def run(
        self,
        specs: list[ValidationCommandSpec],
        *,
        workspace_root: Path | str,
        environment_kind: EnvironmentKind = "local",
    ) -> ValidationReport:
        if not specs:
            raise ValueError("Validation runner requires at least one command spec")
        if environment_kind != "local":
            raise ValueError("Validation runner only supports local execution")
        root = Path(workspace_root).expanduser().resolve()
        outcomes = tuple(
            self._run_one(
                spec,
                workspace_root=root,
                environment_kind=environment_kind,
            )
            for spec in specs
        )
        report_status = (
            ValidationStatus.PASSED
            if all(outcome.status == ValidationStatus.PASSED for outcome in outcomes)
            else ValidationStatus.FAILED
        )
        return ValidationReport(status=report_status, outcomes=outcomes)

    def _run_one(
        self,
        spec: ValidationCommandSpec,
        *,
        workspace_root: Path,
        environment_kind: EnvironmentKind,
    ) -> ValidationOutcome:
        cwd = _execution_cwd(spec.cwd, workspace_root)
        started = time.monotonic()
        policy = evaluate_command_policy(
            spec.command,
            environment_kind=environment_kind,
            workspace_root=workspace_root,
            cwd=cwd,
            env=dict(spec.env),
            timeout_seconds=spec.timeout_seconds,
            validation_command=True,
        )
        if policy.decision == CommandPolicyDecision.DENY:
            return _outcome(
                spec,
                status=ValidationStatus.DENIED,
                exit_code=None,
                started=started,
                policy=policy,
                failure_summary={"policy_decision": policy.decision.value},
            )
        if policy.decision == CommandPolicyDecision.APPROVAL_REQUIRED:
            return _outcome(
                spec,
                status=ValidationStatus.APPROVAL_REQUIRED,
                exit_code=None,
                started=started,
                policy=policy,
                failure_summary={"policy_decision": policy.decision.value},
            )

        args = shlex.split(spec.command)
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=_execution_env(spec.env),
                timeout=spec.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return _outcome(
                spec,
                status=ValidationStatus.TIMED_OUT,
                exit_code=None,
                started=started,
                policy=policy,
                failure_summary={
                    "timeout_seconds": spec.timeout_seconds,
                    "stdout_bytes": _output_size(exc.stdout),
                    "stderr_bytes": _output_size(exc.stderr),
                },
            )
        except OSError as exc:
            return _outcome(
                spec,
                status=ValidationStatus.SPAWN_ERROR,
                exit_code=None,
                started=started,
                policy=policy,
                failure_summary={"error_kind": exc.__class__.__name__},
            )

        status = (
            ValidationStatus.PASSED
            if completed.returncode == spec.expected_exit_code
            else ValidationStatus.FAILED
        )
        return _outcome(
            spec,
            status=status,
            exit_code=completed.returncode,
            started=started,
            policy=policy,
            failure_summary=_failure_summary(completed.stdout, completed.stderr)
            if status == ValidationStatus.FAILED
            else {},
        )


def _execution_cwd(cwd: Path | str | None, workspace_root: Path) -> Path:
    if cwd is None:
        return workspace_root
    path = Path(cwd)
    if path.is_absolute():
        return path.expanduser().resolve()
    return (workspace_root / path).expanduser().resolve()


def _outcome(
    spec: ValidationCommandSpec,
    *,
    status: ValidationStatus,
    exit_code: int | None,
    started: float,
    policy: CommandPolicyVerdict,
    failure_summary: Mapping[str, Any],
) -> ValidationOutcome:
    return ValidationOutcome(
        label=spec.label,
        status=status,
        exit_code=exit_code,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        policy=policy,
        expected_success=spec.expected_success,
        expected_exit_code=spec.expected_exit_code,
        failure_summary=failure_summary,
    )


def _failure_summary(stdout: str, stderr: str) -> dict[str, int]:
    return {
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "stdout_lines": len(stdout.splitlines()),
        "stderr_lines": len(stderr.splitlines()),
    }


def _output_size(output: bytes | str | None) -> int:
    if output is None:
        return 0
    if isinstance(output, bytes):
        return len(output)
    return len(output.encode("utf-8"))


def _execution_env(spec_env: Mapping[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(spec_env)
    return env
