#!/usr/bin/env python3
"""Sanitized operator harness for the o6n semantic-memory dogfood."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

REMOTE_NAME = "o6n"
REMOTES_FILE_ENV = "CODING_AGENT_REMOTES_FILE"
DEFAULT_REMOTES_FILE = Path.home() / ".config" / "coding-agent" / "remotes.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOGFOOD_DIR = Path(__file__).resolve().parent
EVIDENCE_PATH = DOGFOOD_DIR / "semantic_memory_dogfood_evidence.jsonl"
REPORT_PATH = DOGFOOD_DIR / "SEMANTIC_MEMORY_RUN_EVIDENCE.md"

FORBIDDEN_KEY_PARTS = (
    "authorization",
    "body",
    "command_output",
    "content",
    "credential",
    "env",
    "goal",
    "header",
    "message",
    "model_output",
    "output",
    "password",
    "prompt",
    "secret",
    "stderr",
    "stdout",
    "token",
    "url",
)
ALLOWED_KEYS = {
    "timestamp",
    "phase",
    "action",
    "session_id",
    "run_id",
    "tape_id",
    "topic_id",
    "candidate_id",
    "counts",
    "statuses",
    "exit_code",
    "judgment",
    "note",
    "title",
    "summary",
    "kind",
}
ALLOWED_PHASES = {
    "phase0",
    "record-run",
    "seed",
    "probe",
    "transition",
    "status",
    "report",
}
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
SAFE_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
CLI_MAPPING_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
INT_RE = re.compile(r"^-?\d+$")
FLOAT_RE = re.compile(r"^-?\d+\.\d+$")
# Keep detail-only auth strings in sync with server/auth.py and
# http_server.py::_require_admin_context; the CLI surfaces them without
# numeric HTTP codes as "Failed to <action>: <detail>".
AUTH_ERROR_RE = re.compile(
    r"\bhttp\s+(?:401|403)\b"
    r"|\b(?:401|403)\s+(?:unauthorized|forbidden)\b"
    r"|\bunauthorized\b"
    r"|\bforbidden\b"
    r"|\bapi key required\b"
    r"|\binvalid api key\b"
    r"|\badmin token required\b"
)
URL_WITH_CREDENTIALS_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+|api[_-]?key\s*[:=]|token\s*[:=]|secret\s*[:=]|password\s*[:=])"
)
HTTP_TIMEOUT_SECONDS = 30
CLI_TIMEOUT_SECONDS = 120


class ExitCode(IntEnum):
    OK = 0
    USAGE_ERROR = 2
    UNREACHABLE = 10
    LOCAL_ENV_MISSING = 20
    AUTH_4XX = 30
    GATE_FAILED = 40


class HarnessError(Exception):
    def __init__(self, message: str, exit_code: ExitCode) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RemoteConfig:
    name: str
    url: str
    token: str | None
    admin_token_env: str | None


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RunRow:
    run_id: str
    status: str
    tape_id: str | None


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def remotes_file_path() -> Path:
    override = os.environ.get(REMOTES_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_REMOTES_FILE


def load_remote_config(name: str = REMOTE_NAME) -> RemoteConfig:
    path = remotes_file_path()
    if not path.exists():
        raise HarnessError(
            "Remotes file is missing; configure the o6n remote first.",
            ExitCode.LOCAL_ENV_MISSING,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError("Remotes file is not valid JSON.", ExitCode.USAGE_ERROR) from exc
    if not isinstance(raw, dict):
        raise HarnessError("Remotes file must contain a JSON object.", ExitCode.USAGE_ERROR)
    remotes = raw.get("remotes")
    if not isinstance(remotes, dict):
        raise HarnessError(
            "Remotes file is missing the remotes object.",
            ExitCode.USAGE_ERROR,
        )
    entry = remotes.get(name)
    if not isinstance(entry, dict):
        raise HarnessError(
            f"Remote {name!r} is not configured.",
            ExitCode.LOCAL_ENV_MISSING,
        )
    url = entry.get("url")
    token = entry.get("token")
    admin_token_env = entry.get("admin_token_env")
    if not isinstance(url, str) or not url.strip():
        raise HarnessError(f"Remote {name!r} is missing url.", ExitCode.USAGE_ERROR)
    if token is not None and not isinstance(token, str):
        raise HarnessError(f"Remote {name!r} has invalid user token.", ExitCode.USAGE_ERROR)
    if admin_token_env is not None and not isinstance(admin_token_env, str):
        raise HarnessError(
            f"Remote {name!r} has invalid admin token env.",
            ExitCode.USAGE_ERROR,
        )
    validate_base_url(url)
    return RemoteConfig(
        name=name,
        url=url.rstrip("/"),
        token=token.strip() if isinstance(token, str) else None,
        admin_token_env=admin_token_env.strip()
        if isinstance(admin_token_env, str)
        else None,
    )


def validate_base_url(url: str) -> None:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise HarnessError("Remote URL is invalid.", ExitCode.USAGE_ERROR)
    if parsed.username is not None or parsed.password is not None:
        raise HarnessError(
            "Remote URL must not contain credentials.",
            ExitCode.USAGE_ERROR,
        )


def require_admin_env(remote: RemoteConfig) -> None:
    if not remote.admin_token_env:
        raise HarnessError(
            "Remote o6n must name an admin-token environment variable.",
            ExitCode.LOCAL_ENV_MISSING,
        )
    value = os.environ.get(remote.admin_token_env)
    if value is None or not value.strip():
        raise HarnessError(
            "Admin token environment variable is not set or is blank: "
            f"{remote.admin_token_env}",
            ExitCode.LOCAL_ENV_MISSING,
        )


def require_user_token(remote: RemoteConfig) -> str:
    if remote.token is None or not remote.token.strip():
        raise HarnessError(
            "Remote o6n must have a stored user token for review transitions.",
            ExitCode.LOCAL_ENV_MISSING,
        )
    return remote.token.strip()


def http_get_status(remote: RemoteConfig, path: str) -> int:
    request = Request(f"{remote.url}{path}", method="GET")
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status)
    except HTTPError as exc:
        return int(exc.code)
    except URLError as exc:
        raise HarnessError(
            f"Remote endpoint is unreachable while checking {path}.",
            ExitCode.UNREACHABLE,
        ) from exc


def post_review_transition(
    remote: RemoteConfig,
    *,
    session_id: str,
    candidate_id: str,
    status: str,
    reason: str,
) -> int:
    token = require_user_token(remote)
    validate_id_arg(session_id, "session_id")
    validate_id_arg(candidate_id, "candidate_id")
    quoted_session_id = quote(session_id, safe="")
    quoted_candidate_id = quote(candidate_id, safe="")
    payload = json.dumps({"status": status, "reason": reason}).encode("utf-8")
    request = Request(
        f"{remote.url}/sessions/{quoted_session_id}/memory/reviews/{quoted_candidate_id}",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status)
    except HTTPError as exc:
        code = int(exc.code)
        if code in {401, 403}:
            raise HarnessError(
                "Review transition failed with HTTP auth error.",
                ExitCode.AUTH_4XX,
            ) from exc
        raise HarnessError(
            f"Review transition failed with HTTP {code}.",
            ExitCode.GATE_FAILED,
        ) from exc
    except URLError as exc:
        raise HarnessError(
            "Remote endpoint is unreachable during review transition.",
            ExitCode.UNREACHABLE,
        ) from exc


def run_remote_cli(args: list[str], *, timeout: int = CLI_TIMEOUT_SECONDS) -> CommandResult:
    command = ("uv", "run", "python", "-m", "coding_agent", *args)
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HarnessError("uv is not available in PATH.", ExitCode.LOCAL_ENV_MISSING) from exc
    except subprocess.TimeoutExpired as exc:
        raise HarnessError("Remote CLI command timed out.", ExitCode.UNREACHABLE) from exc
    result = CommandResult(
        args=command,
        exit_code=int(completed.returncode),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if result.exit_code != 0:
        raise classify_cli_failure(result)
    return result


def classify_cli_failure(result: CommandResult) -> HarnessError:
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if "admin token environment variable is not set" in combined:
        return HarnessError(
            "Admin token environment variable is not set or is blank.",
            ExitCode.LOCAL_ENV_MISSING,
        )
    if AUTH_ERROR_RE.search(combined):
        return HarnessError("Remote CLI failed with an auth error.", ExitCode.AUTH_4XX)
    if any(
        marker in combined
        for marker in (
            "connection refused",
            "failed to establish",
            "name or service not known",
            "nodename nor servname",
            "read timed out",
            "timed out",
            "unreachable",
        )
    ):
        return HarnessError("Remote CLI could not reach the endpoint.", ExitCode.UNREACHABLE)
    return HarnessError(
        f"Remote CLI failed with exit code {result.exit_code}.",
        ExitCode.GATE_FAILED,
    )


def parse_cli_mapping(stdout: str) -> dict[str, object]:
    lines = stdout.splitlines()
    parsed: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        match = CLI_MAPPING_KEY_RE.match(line)
        if match is None:
            if line.strip():
                raise HarnessError("Unexpected CLI mapping output.", ExitCode.USAGE_ERROR)
            index += 1
            continue
        key = match.group(1)
        rest = match.group(2).strip()
        index += 1
        if rest:
            parsed[key] = parse_scalar(rest)
            continue
        block: list[str] = []
        while index < len(lines) and CLI_MAPPING_KEY_RE.match(lines[index]) is None:
            if lines[index].strip():
                block.append(lines[index])
            index += 1
        if not block:
            parsed[key] = None
            continue
        try:
            parsed[key] = json.loads("\n".join(block))
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"Could not parse CLI mapping block for {key}.",
                ExitCode.GATE_FAILED,
            ) from exc
    return parsed


def parse_scalar(value: str) -> object:
    if value == "True":
        return True
    if value == "False":
        return False
    if value in {"None", "null"}:
        return None
    if INT_RE.match(value):
        return int(value)
    if FLOAT_RE.match(value):
        return float(value)
    return value


def fetch_memory_status(session_id: str) -> dict[str, object]:
    result = run_remote_cli(
        ["remote", "memory", "status", REMOTE_NAME, "--session", session_id]
    )
    status = parse_cli_mapping(result.stdout)
    required = (
        "document_count",
        "reviewed_memory_count",
        "accepted_reviewed_memory_count",
        "topic_store_available",
    )
    missing = [key for key in required if key not in status]
    if missing:
        raise HarnessError(
            "Memory status response is missing required fields.",
            ExitCode.GATE_FAILED,
        )
    return status


def fetch_memory_reviews(
    session_id: str,
    *,
    status: str | None = None,
) -> list[dict[str, object]]:
    args = ["remote", "memory", "reviews", REMOTE_NAME, "--session", session_id]
    if status is not None:
        args.extend(["--status", status])
    result = run_remote_cli(args)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Memory reviews output is not JSON.", ExitCode.GATE_FAILED) from exc
    if not isinstance(raw, list):
        raise HarnessError("Memory reviews output must be a JSON list.", ExitCode.GATE_FAILED)
    reviews: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise HarnessError("Memory review entry must be an object.", ExitCode.GATE_FAILED)
        reviews.append(dict(item))
    return reviews


def parse_runs(stdout: str) -> list[RunRow]:
    if not stdout.strip() or stdout.strip() == "No remote runs found.":
        return []
    rows: list[RunRow] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        columns = line.split("\t")
        if len(columns) != 4:
            raise HarnessError("Remote runs output has unexpected columns.", ExitCode.GATE_FAILED)
        run_id, status, _executor_id, tape_id = columns
        if not run_id or not status:
            raise HarnessError("Remote runs output is missing run_id/status.", ExitCode.GATE_FAILED)
        normalized_tape_id = tape_id.strip()
        if normalized_tape_id.lower() in {"", "none", "null"}:
            normalized_tape_id = None
        rows.append(RunRow(run_id=run_id, status=status, tape_id=normalized_tape_id))
    return rows


def fetch_runs(session_id: str) -> list[RunRow]:
    result = run_remote_cli(["remote", "runs", REMOTE_NAME, "--session", session_id])
    return parse_runs(result.stdout)


def seed_dogfood_topic(
    *,
    session_id: str,
    title: str,
    summary: str,
    kind: str,
) -> dict[str, object]:
    result = run_remote_cli(
        [
            "remote",
            "memory",
            "dogfood-topic",
            REMOTE_NAME,
            "--session",
            session_id,
            "--title",
            title,
            "--summary",
            summary,
            "--kind",
            kind,
        ]
    )
    parsed = parse_cli_mapping(result.stdout)
    if "topic_id" not in parsed or "candidate_id" not in parsed or "warnings" not in parsed:
        raise HarnessError("Dogfood-topic output is missing required fields.", ExitCode.GATE_FAILED)
    warnings = parsed["warnings"]
    if not isinstance(warnings, list):
        raise HarnessError("Dogfood-topic warnings must be a list.", ExitCode.GATE_FAILED)
    return parsed


def status_counts(status: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in (
        "document_count",
        "reviewed_memory_count",
        "accepted_reviewed_memory_count",
    ):
        value = status.get(key)
        if not isinstance(value, int):
            raise HarnessError(
                f"Memory status field {key} must be an integer.",
                ExitCode.GATE_FAILED,
            )
        counts[key] = value
    return counts


def status_flags(status: dict[str, object]) -> dict[str, str]:
    value = status.get("topic_store_available")
    if not isinstance(value, bool):
        raise HarnessError(
            "Memory status field topic_store_available must be boolean.",
            ExitCode.GATE_FAILED,
        )
    return {"topic_store_available": str(value).lower()}


def ensure_topic_store_available(status: dict[str, object], *, phase: str) -> None:
    if status_flags(status)["topic_store_available"] == "true":
        return
    raise HarnessError(
        f"topic_store_available is false during {phase}.",
        ExitCode.GATE_FAILED,
    )


def record_status_snapshot(
    *,
    phase: str,
    action: str,
    session_id: str,
    status: dict[str, object],
) -> None:
    counts = status_counts(status)
    statuses = status_flags(status)
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": phase,
            "action": action,
            "session_id": session_id,
            "counts": counts,
            "statuses": statuses,
        }
    )
    if statuses["topic_store_available"] != "true":
        append_fail_record(
            phase=phase,
            action=f"{action}-gate",
            session_id=session_id,
            status="topic_store_unavailable",
            exit_code=ExitCode.GATE_FAILED,
        )
        raise HarnessError("topic_store_available is false.", ExitCode.GATE_FAILED)


def record_reviews(
    *,
    phase: str,
    action: str,
    session_id: str,
    reviews: list[dict[str, object]],
) -> None:
    counts_by_status: dict[str, int] = {}
    for review in reviews:
        review_status = safe_evidence_status(review.get("status")) or "unknown"
        counts_by_status[review_status] = counts_by_status.get(review_status, 0) + 1
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": phase,
            "action": f"{action}-summary",
            "session_id": session_id,
            "counts": {"review_count": len(reviews), **counts_by_status},
        }
    )
    for review in reviews:
        append_evidence(
            {
                "timestamp": utc_timestamp(),
                "phase": phase,
                "action": action,
                "session_id": session_id,
                "candidate_id": safe_optional_string(review.get("candidate_id")),
                "topic_id": safe_optional_string(review.get("topic_id")),
                "tape_id": safe_optional_string(review.get("tape_id")),
                "statuses": {"review": safe_evidence_status(review.get("status")) or "unknown"},
            }
        )


def record_runs(*, phase: str, action: str, session_id: str, runs: list[RunRow]) -> None:
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": phase,
            "action": f"{action}-summary",
            "session_id": session_id,
            "counts": {"run_count": len(runs)},
        }
    )
    for row in runs:
        append_evidence(
            {
                "timestamp": utc_timestamp(),
                "phase": phase,
                "action": action,
                "session_id": session_id,
                "run_id": row.run_id,
                "tape_id": row.tape_id,
                "statuses": {"run": safe_evidence_status(row.status) or "unknown"},
            }
        )


def append_fail_record(
    *,
    phase: str,
    action: str,
    status: str,
    exit_code: ExitCode,
    session_id: str | None = None,
    run_id: str | None = None,
    topic_id: str | None = None,
    candidate_id: str | None = None,
    note: str | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    record: dict[str, object] = {
        "timestamp": utc_timestamp(),
        "phase": phase,
        "action": action,
        "statuses": {"result": status},
        "exit_code": int(exit_code),
    }
    if session_id is not None:
        record["session_id"] = session_id
    if run_id is not None:
        record["run_id"] = run_id
    if topic_id is not None:
        record["topic_id"] = topic_id
    if candidate_id is not None:
        record["candidate_id"] = candidate_id
    if note is not None:
        record["note"] = note
    if counts is not None:
        record["counts"] = counts
    append_evidence(record)


def append_evidence(record: dict[str, object]) -> None:
    validate_evidence_record(record)
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVIDENCE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def validate_evidence_record(record: dict[str, object]) -> None:
    unknown = set(record) - ALLOWED_KEYS
    if unknown:
        raise ValueError(f"Evidence record has disallowed keys: {sorted(unknown)}")
    for key in record:
        lowered = key.lower()
        if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
            raise ValueError(f"Evidence key is forbidden: {key}")
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise ValueError("Evidence timestamp must be a UTC ISO string ending in Z.")
    phase = record.get("phase")
    if phase not in ALLOWED_PHASES:
        raise ValueError(f"Evidence phase is invalid: {phase!r}")
    action = record.get("action")
    if not isinstance(action, str) or not SAFE_MAP_KEY_RE.match(action):
        raise ValueError("Evidence action must be a short safe identifier.")
    for key in ("session_id", "run_id", "tape_id", "topic_id", "candidate_id"):
        value = record.get(key)
        if value is not None and not (isinstance(value, str) and SAFE_ID_RE.match(value)):
            raise ValueError(f"Evidence {key} must be a safe id or null.")
    validate_counts(record.get("counts"))
    validate_statuses(record.get("statuses"))
    validate_optional_int(record.get("exit_code"), "exit_code")
    validate_choice(record.get("judgment"), {"pass", "fail", "blocked"}, "judgment")
    validate_safe_note(record.get("note"), "note", max_length=240)
    validate_safe_note(record.get("title"), "title", max_length=256)
    validate_safe_note(record.get("summary"), "summary", max_length=256)
    validate_safe_note(record.get("kind"), "kind", max_length=64)


def validate_counts(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Evidence counts must be an object.")
    for key, count in value.items():
        if not isinstance(key, str) or not SAFE_MAP_KEY_RE.match(key):
            raise ValueError("Evidence count keys must be safe identifiers.")
        if not isinstance(count, int):
            raise ValueError("Evidence count values must be integers.")


def validate_statuses(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Evidence statuses must be an object.")
    for key, status in value.items():
        if not isinstance(key, str) or not SAFE_MAP_KEY_RE.match(key):
            raise ValueError("Evidence status keys must be safe identifiers.")
        if not isinstance(status, str) or not SAFE_MAP_KEY_RE.match(status):
            raise ValueError("Evidence status values must be safe identifiers.")


def validate_optional_int(value: object, key: str) -> None:
    if value is not None and not isinstance(value, int):
        raise ValueError(f"Evidence {key} must be an integer.")


def validate_id_arg(value: str, name: str) -> None:
    if not SAFE_ID_RE.match(value):
        raise HarnessError(f"{name} must be a safe identifier.", ExitCode.USAGE_ERROR)


def validate_choice(value: object, choices: set[str], key: str) -> None:
    if value is not None and value not in choices:
        raise ValueError(f"Evidence {key} must be one of {sorted(choices)}.")


def validate_safe_note(value: object, key: str, *, max_length: int) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"Evidence {key} must be a string.")
    if not value or len(value) > max_length or "\n" in value or "\r" in value:
        raise ValueError(f"Evidence {key} must be a short single-line string.")
    if "://" in value:
        raise ValueError(f"Evidence {key} must not contain URLs.")
    if URL_WITH_CREDENTIALS_RE.search(value) or SECRET_VALUE_RE.search(value):
        raise ValueError(f"Evidence {key} appears to contain sensitive material.")


def safe_optional_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


def safe_evidence_status(value: object) -> str | None:
    status = safe_optional_string(value)
    if status is None:
        return None
    if SAFE_MAP_KEY_RE.match(status):
        return status
    return "unknown"


def command_phase0(args: argparse.Namespace) -> None:
    remote = load_remote_config()
    health = http_get_status(remote, "/healthz")
    ready = http_get_status(remote, "/readyz")
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": "phase0",
            "action": "health",
            "statuses": {
                "healthz": f"http_{health}",
                "readyz": f"http_{ready}",
            },
        }
    )
    if health != 200 or ready != 200:
        raise HarnessError("healthz/readyz returned non-200.", ExitCode.UNREACHABLE)
    print_manual_probe_command()
    if args.session is None:
        print("No --session provided; baseline capture skipped.")
        return
    require_admin_env(remote)
    status = fetch_memory_status(args.session)
    record_status_snapshot(
        phase="phase0",
        action="baseline-status",
        session_id=args.session,
        status=status,
    )
    reviews = fetch_memory_reviews(args.session)
    record_reviews(
        phase="phase0",
        action="baseline-review",
        session_id=args.session,
        reviews=reviews,
    )
    print("Phase 0 evidence recorded.")


def print_manual_probe_command() -> None:
    print("Manual retained probe command:")
    print(
        "uv run python -m coding_agent remote run o6n "
        '--empty-workspace --goal "<small real question>"'
    )


def command_record_run(args: argparse.Namespace) -> None:
    runs = fetch_runs(args.session)
    record_runs(phase="record-run", action="run", session_id=args.session, runs=runs)
    print(f"Recorded {len(runs)} run row(s).")


def command_seed(args: argparse.Namespace) -> None:
    validate_seed_inputs(args.title, args.summary, args.kind)
    remote = load_remote_config()
    require_admin_env(remote)
    before_status = fetch_memory_status(args.session)
    ensure_topic_store_available(before_status, phase="seed-before")
    before_count = status_counts(before_status)["document_count"]
    seed_result = seed_dogfood_topic(
        session_id=args.session,
        title=args.title,
        summary=args.summary,
        kind=args.kind,
    )
    warnings = seed_result["warnings"]
    assert isinstance(warnings, list)
    topic_id = safe_optional_string(seed_result.get("topic_id"))
    candidate_id = safe_optional_string(seed_result.get("candidate_id"))
    if warnings:
        append_fail_record(
            phase="seed",
            action="seed",
            session_id=args.session,
            topic_id=topic_id,
            candidate_id=candidate_id,
            status="warnings",
            exit_code=ExitCode.GATE_FAILED,
            counts={"warning_count": len(warnings)},
        )
        raise HarnessError("Dogfood-topic returned warnings.", ExitCode.GATE_FAILED)
    after_status = fetch_memory_status(args.session)
    ensure_topic_store_available(after_status, phase="seed-after")
    after_count = status_counts(after_status)["document_count"]
    if after_count <= before_count:
        append_fail_record(
            phase="seed",
            action="document-count-gate",
            session_id=args.session,
            topic_id=topic_id,
            candidate_id=candidate_id,
            status="document_count_not_increased",
            exit_code=ExitCode.GATE_FAILED,
            counts={
                "before_document_count": before_count,
                "after_document_count": after_count,
            },
        )
        raise HarnessError("document_count did not increase after seed.", ExitCode.GATE_FAILED)
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": "seed",
            "action": "seed",
            "session_id": args.session,
            "topic_id": topic_id,
            "candidate_id": candidate_id,
            "title": args.title,
            "summary": args.summary,
            "kind": args.kind,
            "counts": {
                "warning_count": 0,
                "before_document_count": before_count,
                "after_document_count": after_count,
            },
            "statuses": {"result": "pass"},
        }
    )
    print("Seed evidence recorded.")


def validate_seed_inputs(title: str, summary: str, kind: str) -> None:
    validate_safe_note(title, "title", max_length=256)
    validate_safe_note(summary, "summary", max_length=256)
    validate_safe_note(kind, "kind", max_length=64)


def command_probe(args: argparse.Namespace) -> None:
    runs = fetch_runs(args.session)
    if not runs:
        append_fail_record(
            phase="probe",
            action=args.kind,
            session_id=args.session,
            status="no_runs",
            exit_code=ExitCode.GATE_FAILED,
            note=args.note,
        )
        raise HarnessError("No run rows found for probe session.", ExitCode.GATE_FAILED)
    row = runs[-1]
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": "probe",
            "action": args.kind,
            "session_id": args.session,
            "run_id": row.run_id,
            "tape_id": row.tape_id,
            "judgment": args.judgment,
            "note": args.note,
            "statuses": {"run": safe_evidence_status(row.status) or "unknown"},
        }
    )
    print("Probe judgment recorded.")


def command_transition(args: argparse.Namespace) -> None:
    validate_safe_note(args.reason, "note", max_length=240)
    remote = load_remote_config()
    before_accepted_count: int | None = None
    if args.status == "accepted":
        require_admin_env(remote)
        before_memory_status = fetch_memory_status(args.session)
        ensure_topic_store_available(before_memory_status, phase="transition-before")
        before_accepted_count = status_counts(before_memory_status)[
            "accepted_reviewed_memory_count"
        ]
    before_reviews = fetch_memory_reviews(args.session)
    before_status = find_candidate_status(before_reviews, args.candidate)
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": "transition",
            "action": "before",
            "session_id": args.session,
            "candidate_id": args.candidate,
            "statuses": {"review": safe_evidence_status(before_status) or "missing"},
        }
    )
    if before_status is None:
        raise HarnessError(
            "Candidate is not visible in that session; pin --session to the session "
            "that produced the candidate.",
            ExitCode.GATE_FAILED,
        )
    http_status = post_review_transition(
        remote,
        session_id=args.session,
        candidate_id=args.candidate,
        status=args.status,
        reason=args.reason,
    )
    after_reviews = fetch_memory_reviews(args.session, status=args.status)
    after_status = find_candidate_status(after_reviews, args.candidate)
    append_evidence(
        {
            "timestamp": utc_timestamp(),
            "phase": "transition",
            "action": "after",
            "session_id": args.session,
            "candidate_id": args.candidate,
            "statuses": {
                "review": safe_evidence_status(after_status) or "missing",
                "http": f"http_{http_status}",
            },
            "note": args.reason,
        }
    )
    if after_status != args.status:
        append_fail_record(
            phase="transition",
            action="verify",
            session_id=args.session,
            candidate_id=args.candidate,
            status="status_mismatch",
            exit_code=ExitCode.GATE_FAILED,
            note=args.reason,
        )
        raise HarnessError("Review transition did not verify through CLI.", ExitCode.GATE_FAILED)
    if before_accepted_count is not None:
        after_memory_status = fetch_memory_status(args.session)
        ensure_topic_store_available(after_memory_status, phase="transition-after")
        after_accepted_count = status_counts(after_memory_status)[
            "accepted_reviewed_memory_count"
        ]
        append_evidence(
            {
                "timestamp": utc_timestamp(),
                "phase": "transition",
                "action": "accepted-count",
                "session_id": args.session,
                "candidate_id": args.candidate,
                "counts": {
                    "before_accepted_reviewed_memory_count": before_accepted_count,
                    "after_accepted_reviewed_memory_count": after_accepted_count,
                },
            }
        )
        if after_accepted_count <= before_accepted_count:
            append_fail_record(
                phase="transition",
                action="accepted-count-gate",
                session_id=args.session,
                candidate_id=args.candidate,
                status="accepted_count_not_increased",
                exit_code=ExitCode.GATE_FAILED,
                note=args.reason,
            )
            raise HarnessError(
                "accepted_reviewed_memory_count did not increase.",
                ExitCode.GATE_FAILED,
            )
    print("Transition evidence recorded.")


def find_candidate_status(
    reviews: list[dict[str, object]],
    candidate_id: str,
) -> str | None:
    for review in reviews:
        if review.get("candidate_id") == candidate_id:
            return safe_optional_string(review.get("status"))
    return None


def command_status(args: argparse.Namespace) -> None:
    remote = load_remote_config()
    require_admin_env(remote)
    status = fetch_memory_status(args.session)
    record_status_snapshot(
        phase="status",
        action="snapshot",
        session_id=args.session,
        status=status,
    )
    print("Status snapshot recorded.")


def command_report(_args: argparse.Namespace) -> None:
    records = read_evidence_records()
    markdown = render_report(records)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    print(f"Wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}.")


def read_evidence_records() -> list[dict[str, object]]:
    if not EVIDENCE_PATH.exists():
        return []
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(EVIDENCE_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"Evidence JSONL line {line_number} is invalid JSON.",
                ExitCode.GATE_FAILED,
            ) from exc
        if not isinstance(raw, dict):
            raise HarnessError(
                f"Evidence JSONL line {line_number} is not an object.",
                ExitCode.GATE_FAILED,
            )
        record = dict(raw)
        try:
            validate_evidence_record(record)
        except ValueError as exc:
            raise HarnessError(
                f"Evidence JSONL line {line_number} violates privacy schema.",
                ExitCode.GATE_FAILED,
            ) from exc
        records.append(record)
    return records


def render_report(records: list[dict[str, object]]) -> str:
    lines = [
        "# Semantic Memory Dogfood Run Evidence",
        "",
        "This report is rendered deterministically from "
        "`docs/dogfood/semantic_memory_dogfood_evidence.jsonl`.",
        "It intentionally omits prompts, model output, command stdout/stderr, "
        "URLs, environment values, and tokens.",
        "",
        "## Summary",
        "",
        f"- Records: {len(records)}",
        f"- Sessions: {len(unique_values(records, 'session_id'))}",
        f"- Runs: {len(unique_values(records, 'run_id'))}",
        f"- Topics: {len(unique_values(records, 'topic_id'))}",
        f"- Candidates: {len(unique_values(records, 'candidate_id'))}",
        "",
        "## Records",
        "",
    ]
    if not records:
        lines.extend(["No evidence records found.", ""])
        return "\n".join(lines)
    lines.extend(
        [
            "| # | Timestamp | Phase | Action | IDs | Counts | Statuses | Judgment | Note |",
            "| - | - | - | - | - | - | - | - | - |",
        ]
    )
    for index, record in enumerate(records, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    md_cell(record["timestamp"]),
                    md_cell(record["phase"]),
                    md_cell(record["action"]),
                    md_cell(format_ids(record)),
                    md_cell(format_mapping(record.get("counts"))),
                    md_cell(format_mapping(record.get("statuses"))),
                    md_cell(record.get("judgment")),
                    md_cell(format_note(record)),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def unique_values(records: list[dict[str, object]], key: str) -> set[str]:
    values: set[str] = set()
    for record in records:
        value = record.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    return values


def format_ids(record: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("session_id", "run_id", "tape_id", "topic_id", "candidate_id"):
        value = record.get(key)
        if value is None and key == "candidate_id" and key in record:
            parts.append("candidate_id=null")
        elif isinstance(value, str) and value:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def format_mapping(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, dict):
        return ""
    parts = [f"{key}={value[key]}" for key in sorted(value)]
    return "; ".join(parts)


def format_note(record: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("kind", "title", "summary", "note"):
        value = record.get(key)
        if isinstance(value, str) and value:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def md_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record sanitized evidence for semantic-memory dogfood execution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    phase0 = subparsers.add_parser("phase0", help="Run phase 0 checks and baseline capture.")
    phase0.add_argument("--session", help="Probe session ID after the operator creates it.")
    phase0.set_defaults(func=command_phase0)

    record_run = subparsers.add_parser("record-run", help="Record remote run rows.")
    record_run.add_argument("--session", required=True, help="Remote session ID.")
    record_run.set_defaults(func=command_record_run)

    seed = subparsers.add_parser("seed", help="Seed and gate one dogfood topic.")
    seed.add_argument("--session", required=True, help="Remote session ID.")
    seed.add_argument("--title", required=True, help="Permanent-quality seed title.")
    seed.add_argument("--summary", required=True, help="Permanent-quality seed summary.")
    seed.add_argument("--kind", default="coding", help="Seed topic kind.")
    seed.set_defaults(func=command_seed)

    probe = subparsers.add_parser("probe", help="Record operator judgment for a probe.")
    probe.add_argument("--session", required=True, help="Remote session ID.")
    probe.add_argument(
        "--kind",
        required=True,
        choices=("topic", "negative", "accepted"),
        help="Probe kind.",
    )
    probe.add_argument(
        "--judgment",
        required=True,
        choices=("pass", "fail", "blocked"),
        help="Operator judgment.",
    )
    probe.add_argument("--note", required=True, help="Short operator note.")
    probe.set_defaults(func=command_probe)

    transition = subparsers.add_parser(
        "transition",
        help="Transition a reviewed-memory candidate through raw HTTP.",
    )
    transition.add_argument("--session", required=True, help="Remote session ID.")
    transition.add_argument("--candidate", required=True, help="Candidate ID.")
    transition.add_argument(
        "--status",
        required=True,
        choices=("accepted", "rejected", "archived"),
        help="Target review status.",
    )
    transition.add_argument("--reason", required=True, help="Short transition reason.")
    transition.set_defaults(func=command_transition)

    status = subparsers.add_parser("status", help="Record a memory status snapshot.")
    status.add_argument("--session", required=True, help="Remote session ID.")
    status.set_defaults(func=command_status)

    report = subparsers.add_parser("report", help="Render deterministic Markdown evidence.")
    report.set_defaults(func=command_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        args.func(args)
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(ExitCode.USAGE_ERROR)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
