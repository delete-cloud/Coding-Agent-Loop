from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
import shlex
from typing import Any, Literal


EnvironmentKind = Literal["local", "cloud"]

DEFAULT_MAX_TIMEOUT_SECONDS = 600
_DISALLOWED_SHELL_TOKENS = frozenset({"&&", "||", "|", ";", ">", ">>", "<", "2>", "&"})
_SAFE_COMMANDS = frozenset(
    {
        "cat",
        "echo",
        "git",
        "ls",
        "python",
        "python3",
        "pytest",
        "rg",
        "ruff",
        "test",
        "uv",
    }
)
_APPROVAL_COMMANDS = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "curl",
        "docker",
        "git",
        "make",
        "mv",
        "pip",
        "python",
        "python3",
        "rm",
        "sh",
        "uv",
    }
)
_DESTRUCTIVE_PATTERNS = (
    ("rm", "-rf"),
    ("rm", "-fr"),
    ("git", "clean"),
    ("git", "reset"),
)


class CommandPolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class CommandPolicyReason(StrEnum):
    SAFE_COMMAND = "safe_command"
    VALIDATION_COMMAND = "validation_command"
    EMPTY_COMMAND = "empty_command"
    SHELL_SYNTAX = "shell_syntax"
    CWD_ESCAPE = "cwd_escape"
    PATH_ESCAPE = "path_escape"
    UNSAFE_ENV_KEY = "unsafe_env_key"
    TIMEOUT_EXCEEDS_LIMIT = "timeout_exceeds_limit"
    DESTRUCTIVE_COMMAND = "destructive_command"
    NETWORK_COMMAND = "network_command"
    UNKNOWN_COMMAND = "unknown_command"


@dataclass(frozen=True)
class CommandPolicyVerdict:
    decision: CommandPolicyDecision
    reasons: tuple[CommandPolicyReason, ...]
    command_name: str | None
    environment_kind: EnvironmentKind
    timeout_seconds: int

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": [reason.value for reason in self.reasons],
            "command_name": self.command_name,
            "environment_kind": self.environment_kind,
            "timeout_seconds": self.timeout_seconds,
        }


def evaluate_command_policy(
    command: str,
    *,
    environment_kind: EnvironmentKind,
    workspace_root: Path | str | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 120,
    validation_command: bool = False,
    max_timeout_seconds: int = DEFAULT_MAX_TIMEOUT_SECONDS,
) -> CommandPolicyVerdict:
    raw_syntax_risk = _has_shell_syntax_risk(command)
    try:
        args = shlex.split(command)
    except ValueError:
        return _verdict(
            CommandPolicyDecision.DENY,
            [CommandPolicyReason.SHELL_SYNTAX],
            None,
            environment_kind,
            timeout_seconds,
        )
    if not args:
        return _verdict(
            CommandPolicyDecision.DENY,
            [CommandPolicyReason.EMPTY_COMMAND],
            None,
            environment_kind,
            timeout_seconds,
        )

    command_name = _safe_command_name(args[0])
    deny_reasons = _deny_reasons(
        raw_syntax_risk=raw_syntax_risk,
        args=args,
        workspace_root=workspace_root,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        max_timeout_seconds=max_timeout_seconds,
    )
    if deny_reasons:
        return _verdict(
            CommandPolicyDecision.DENY,
            deny_reasons,
            command_name,
            environment_kind,
            timeout_seconds,
        )

    approval_reasons = _approval_reasons(args)
    if approval_reasons:
        return _verdict(
            CommandPolicyDecision.APPROVAL_REQUIRED,
            approval_reasons,
            command_name,
            environment_kind,
            timeout_seconds,
        )

    if validation_command and command_name in _SAFE_COMMANDS:
        return _verdict(
            CommandPolicyDecision.ALLOW,
            [CommandPolicyReason.VALIDATION_COMMAND],
            command_name,
            environment_kind,
            timeout_seconds,
        )

    if command_name in _SAFE_COMMANDS:
        return _verdict(
            CommandPolicyDecision.ALLOW,
            [CommandPolicyReason.SAFE_COMMAND],
            command_name,
            environment_kind,
            timeout_seconds,
        )

    return _verdict(
        CommandPolicyDecision.APPROVAL_REQUIRED,
        [CommandPolicyReason.UNKNOWN_COMMAND],
        command_name,
        environment_kind,
        timeout_seconds,
    )


def _deny_reasons(
    *,
    raw_syntax_risk: bool,
    args: list[str],
    workspace_root: Path | str | None,
    cwd: Path | str | None,
    env: dict[str, str] | None,
    timeout_seconds: int,
    max_timeout_seconds: int,
) -> list[CommandPolicyReason]:
    reasons: list[CommandPolicyReason] = []
    if raw_syntax_risk or any(token in _DISALLOWED_SHELL_TOKENS for token in args):
        reasons.append(CommandPolicyReason.SHELL_SYNTAX)
    if timeout_seconds > max_timeout_seconds:
        reasons.append(CommandPolicyReason.TIMEOUT_EXCEEDS_LIMIT)
    if env is not None and any(not _safe_env_key(key) for key in env):
        reasons.append(CommandPolicyReason.UNSAFE_ENV_KEY)
    if workspace_root is not None:
        root = Path(workspace_root).expanduser().resolve()
        if cwd is not None and not _path_inside_workspace(Path(cwd), root):
            reasons.append(CommandPolicyReason.CWD_ESCAPE)
        if _has_path_escape(args, root):
            reasons.append(CommandPolicyReason.PATH_ESCAPE)
    return reasons


def _approval_reasons(args: list[str]) -> list[CommandPolicyReason]:
    if _is_destructive(args):
        return [CommandPolicyReason.DESTRUCTIVE_COMMAND]
    if args[0] in {"curl", "wget"}:
        return [CommandPolicyReason.NETWORK_COMMAND]
    if _is_package_install(args):
        return [CommandPolicyReason.NETWORK_COMMAND]
    if args[0] in _APPROVAL_COMMANDS and args[0] not in _SAFE_COMMANDS:
        return [CommandPolicyReason.UNKNOWN_COMMAND]
    return []


def _is_destructive(args: list[str]) -> bool:
    command = args[0]
    if command == "rm" and any(flag in {"-rf", "-fr"} for flag in args[1:]):
        return True
    for first, second in _DESTRUCTIVE_PATTERNS:
        if len(args) >= 2 and args[0] == first and args[1] == second:
            return True
    return False


def _is_package_install(args: list[str]) -> bool:
    if len(args) >= 3 and args[0] == "uv" and args[1] == "pip":
        return True
    if len(args) >= 2 and args[0] == "pip" and args[1] == "install":
        return True
    if (
        len(args) >= 4
        and args[0] in {"python", "python3"}
        and args[1:4]
        == [
            "-m",
            "pip",
            "install",
        ]
    ):
        return True
    return False


def _path_inside_workspace(path: Path, root: Path) -> bool:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def _has_path_escape(args: list[str], root: Path) -> bool:
    for arg in args[1:]:
        for match in re.findall(r"/[A-Za-z0-9_./-]+", arg):
            if not _path_inside_workspace(Path(match), root):
                return True
    return False


def _safe_env_key(key: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is not None


def _has_shell_syntax_risk(command: str) -> bool:
    return any(token in command for token in _DISALLOWED_SHELL_TOKENS)


def _safe_command_name(first_token: str) -> str | None:
    if "=" in first_token:
        return None
    name = Path(first_token).name
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name):
        return None
    return name


def _verdict(
    decision: CommandPolicyDecision,
    reasons: list[CommandPolicyReason],
    command_name: str | None,
    environment_kind: EnvironmentKind,
    timeout_seconds: int,
) -> CommandPolicyVerdict:
    return CommandPolicyVerdict(
        decision=decision,
        reasons=tuple(reasons),
        command_name=command_name,
        environment_kind=environment_kind,
        timeout_seconds=timeout_seconds,
    )
