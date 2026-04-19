from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import yaml


_FIX_SUBJECT_RE = re.compile(
    r"^(?P<kind>fix|regression|revert)(?:\((?P<scope>[^)]+)\))?:\s*(?P<summary>.+)$",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FixCommit:
    sha: str
    subject: str
    scope: str | None
    summary: str
    files: list[str]


@dataclass(frozen=True)
class Phase1BuildResult:
    output_dir: Path
    pattern_count: int
    commit_count: int


DEFAULT_MAX_PATTERNS = 20
TAXONOMY = {
    "root_cause_class": [
        "state_desync",
        "race_condition",
        "missing_validation",
        "invalid_configuration",
        "persistence_boundary_mismatch",
        "interface_contract_drift",
        "error_handling_gap",
        "missing_regression_coverage",
    ],
    "failure_mode": [
        "stale_session_state",
        "lost_update",
        "startup_failure",
        "wrong_http_response",
        "partial_persistence",
    ],
    "subsystem": [
        "agentkit",
        "adapter",
        "approval",
        "bootstrap",
        "checkpoint",
        "cli",
        "config",
        "http",
        "kb",
        "plugins",
        "runtime",
        "session_manager",
        "shell",
        "storage_pg",
        "tape",
        "tools",
        "tracing",
        "ui",
        "verification",
    ],
    "prevention_type": [
        "regression_test",
        "invariant_check",
        "fail_fast_validation",
        "interface_boundary_rule",
    ],
}


class PatternRecord(TypedDict):
    id: str
    title: str
    filename: str
    severity: str
    confidence: str
    subsystems: list[str]
    related_files: list[str]
    related_commits: list[str]
    keywords: list[str]
    release_checks: list[str]
    subject: str
    summary: str


def collect_fix_commits(repo_root: Path) -> list[FixCommit]:
    git_root = _git_toplevel(repo_root)
    repo_prefix = _repo_prefix(repo_root=repo_root, git_root=git_root)
    command = [
        "git",
        "log",
        "--reverse",
        "--name-only",
        "--format=%x1e%H%x1f%s",
    ]
    if repo_prefix is not None:
        command.extend(["--", repo_prefix])
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        cwd=git_root,
    )
    commits: list[FixCommit] = []
    for chunk in completed.stdout.split("\x1e"):
        stripped = chunk.strip()
        if not stripped:
            continue
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            continue
        metadata = lines[0]
        parts = metadata.split("\x1f", maxsplit=1)
        if len(parts) != 2:
            continue
        sha = parts[0].strip()
        subject = parts[1].strip()
        matched = _FIX_SUBJECT_RE.match(subject)
        if matched is None:
            continue
        files = _normalize_files(lines[1:], repo_prefix=repo_prefix)
        commits.append(
            FixCommit(
                sha=sha,
                subject=subject,
                scope=matched.group("scope"),
                summary=matched.group("summary"),
                files=files,
            )
        )
    return commits


def build_phase1_artifacts(
    repo_root: Path,
    *,
    output_dir: Path,
    max_patterns: int = DEFAULT_MAX_PATTERNS,
) -> Phase1BuildResult:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    commits = collect_fix_commits(repo_root)
    patterns = _build_patterns(commits, max_patterns=max_patterns)

    _write_text(output_dir / "README.md", _build_readme())
    _write_yaml(output_dir / "taxonomy.yaml", _build_taxonomy())
    _write_yaml(
        output_dir / "index.yaml",
        {
            "patterns": [
                _index_entry(pattern, output_dir=output_dir) for pattern in patterns
            ]
        },
    )
    _write_text(
        output_dir / "onboarding" / "commit-classification-report.md",
        _build_commit_classification_report(commits),
    )
    _write_text(
        output_dir / "onboarding" / "historical-fix-clusters.md",
        _build_historical_fix_clusters(patterns),
    )
    _write_text(
        output_dir / "onboarding" / "ingestion-log.md",
        _build_ingestion_log(repo_root, output_dir, commits),
    )
    _write_text(output_dir / "templates" / "pattern.md", _build_pattern_template())
    _write_text(
        output_dir / "templates" / "release-risk-report.md",
        _build_release_risk_template(),
    )

    for pattern in patterns:
        _write_text(
            output_dir / "patterns" / pattern["filename"], _render_pattern(pattern)
        )

    return Phase1BuildResult(
        output_dir=output_dir,
        pattern_count=len(patterns),
        commit_count=len(commits),
    )


def _pattern_from_commit(index: int, commit: FixCommit) -> PatternRecord:
    pattern_id = f"PM-{index:04d}"
    title = _normalize_title(commit.summary)
    slug = _slugify(title)
    keywords = [token for token in _slugify(commit.summary).split("-") if token]
    subsystem = _normalize_subsystem(commit.scope or _infer_subsystem(commit.files))
    return {
        "id": pattern_id,
        "title": title,
        "filename": f"{pattern_id}-{slug}.md",
        "severity": "medium",
        "confidence": "medium",
        "subsystems": [subsystem],
        "related_files": commit.files,
        "related_commits": [commit.sha],
        "keywords": keywords,
        "release_checks": [
            f"Run focused tests for {subsystem} changes before release.",
            "Review affected files for the same control-flow shape before shipping.",
        ],
        "subject": commit.subject,
        "summary": commit.summary,
    }


def _build_patterns(
    commits: list[FixCommit], *, max_patterns: int
) -> list[PatternRecord]:
    grouped: dict[tuple[str, str], list[FixCommit]] = {}
    for commit in commits:
        title = _canonicalize_group_title(_normalize_title(commit.summary))
        subsystem = _normalize_subsystem(commit.scope or _infer_subsystem(commit.files))
        grouped.setdefault((subsystem, title), []).append(commit)

    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0][0], item[0][1]),
    )
    patterns: list[PatternRecord] = []
    for index, ((_subsystem, _title), group_commits) in enumerate(
        ordered_groups[:max_patterns],
        start=1,
    ):
        pattern = _pattern_from_commit(index, group_commits[0])
        pattern["related_commits"] = [commit.sha for commit in group_commits]
        pattern["related_files"] = sorted(
            {path for commit in group_commits for path in commit.files}
        )
        patterns.append(pattern)
    return patterns


def _normalize_title(summary: str) -> str:
    cleaned = summary.strip().rstrip(".")
    replacements = [
        (r"^repair\s+", ""),
        (r"^stabilize\s+", ""),
        (r"^stabilise\s+", ""),
        (r"^harden\s+", ""),
        (r"^validate\s+", ""),
        (r"^fix\s+", ""),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned or "Historical fix pattern"


def _slugify(value: str) -> str:
    lowered = value.lower()
    slug = _NON_WORD_RE.sub("-", lowered).strip("-")
    return slug or "pattern"


def _git_toplevel(repo_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    return Path(completed.stdout.strip())


def _repo_prefix(repo_root: Path, git_root: Path) -> str | None:
    relative = repo_root.resolve().relative_to(git_root.resolve())
    if str(relative) == ".":
        return None
    return relative.as_posix()


def _normalize_files(files: list[str], *, repo_prefix: str | None) -> list[str]:
    if repo_prefix is None:
        return files
    prefix = f"{repo_prefix}/"
    normalized: list[str] = []
    for path in files:
        if path.startswith(prefix):
            normalized.append(path.removeprefix(prefix))
    return normalized


def _infer_subsystem(files: list[str]) -> str:
    for path in files:
        lowered = path.lower()
        if "ui" in lowered:
            return "ui"
        if "http" in lowered:
            return "http"
        if "storage" in lowered or "pg" in lowered:
            return "storage_pg"
        if "cli" in lowered:
            return "cli"
        if "adapter" in lowered:
            return "adapter"
        if "approval" in lowered:
            return "approval"
        if "checkpoint" in lowered:
            return "checkpoint"
        if "trace" in lowered:
            return "tracing"
        if "tape" in lowered:
            return "tape"
        if "tool" in lowered:
            return "tools"
        if "shell" in lowered:
            return "shell"
        if "plugin" in lowered:
            return "plugins"
        if "config" in lowered:
            return "config"
        if "kb" in lowered:
            return "kb"
        if "session_manager" in lowered or "session" in lowered:
            return "session_manager"
        if "verify" in lowered:
            return "verification"
        if "agentkit" in lowered:
            return "agentkit"
    return "runtime"


def _normalize_subsystem(subsystem: str) -> str:
    aliases = {
        "agentkit": "agentkit",
        "adapter": "adapter",
        "approval": "approval",
        "bootstrap": "bootstrap",
        "checkpoint": "checkpoint",
        "cli": "cli",
        "coding-agent": "runtime",
        "config": "config",
        "core": "runtime",
        "docker": "runtime",
        "gitignore": "runtime",
        "http": "http",
        "kb": "kb",
        "p0": "runtime",
        "p1": "runtime",
        "plugins": "plugins",
        "review": "ui",
        "runtime": "runtime",
        "session": "session_manager",
        "session_manager": "session_manager",
        "shell": "shell",
        "skills": "plugins",
        "storage": "storage_pg",
        "tape": "tape",
        "test": "verification",
        "tools": "tools",
        "tracing": "tracing",
        "tui": "ui",
        "ui": "ui",
        "verification": "verification",
    }
    normalized = aliases.get(subsystem.lower(), subsystem.lower())
    if normalized not in TAXONOMY["subsystem"]:
        return "runtime"
    return normalized


def _canonicalize_group_title(title: str) -> str:
    lowered = title.lower()
    if "review" in lowered:
        subsystem_match = re.search(r"for ([a-z0-9-]+)$", lowered)
        if subsystem_match is not None:
            return f"Address review findings for {subsystem_match.group(1)}"
        return "Address review findings"
    return title


def _index_entry(pattern: PatternRecord, *, output_dir: Path) -> dict[str, object]:
    _ = output_dir
    return {
        "id": pattern["id"],
        "title": pattern["title"],
        "path": str(Path("patterns") / pattern["filename"]),
        "severity": pattern["severity"],
        "confidence": pattern["confidence"],
        "subsystems": pattern["subsystems"],
        "related_files": pattern["related_files"],
        "related_commits": pattern["related_commits"],
        "keywords": pattern["keywords"],
    }


def _build_readme() -> str:
    return """# Postmortem Knowledge Base

This directory stores recurring failure patterns extracted from historical fix commits.

## Contents

- `taxonomy.yaml` — shared classification values
- `index.yaml` — machine-readable pattern index
- `patterns/` — recurring failure patterns
- `onboarding/` — Phase 1 historical ingestion reports
- `templates/` — starter templates for future updates

## Phase 1 Scope

Phase 1 builds a deterministic starting corpus from local git history. It records patterns, affected files, and release review checks that later release automation can reuse.
"""


def _build_taxonomy() -> dict[str, list[str]]:
    return TAXONOMY


def _build_commit_classification_report(commits: list[FixCommit]) -> str:
    lines = [
        "# Commit Classification Report",
        "",
        f"Total fix commits classified: {len(commits)}",
        "",
        "## Commits",
        "",
    ]
    for commit in commits:
        scope = commit.scope or _infer_subsystem(commit.files)
        lines.append(f"- `{commit.sha[:7]}` `{scope}` — {commit.subject}")
    return "\n".join(lines) + "\n"


def _build_historical_fix_clusters(patterns: list[PatternRecord]) -> str:
    lines = ["# Historical Fix Clusters", ""]
    for pattern in patterns:
        lines.extend(
            [
                f"## {pattern['id']} {pattern['title']}",
                "",
                f"- Subsystem: {', '.join(pattern['subsystems'])}",
                f"- Commits: {', '.join(pattern['related_commits'])}",
                f"- Files: {', '.join(pattern['related_files'])}",
                "",
            ]
        )
    return "\n".join(lines)


def _build_ingestion_log(
    repo_root: Path, output_dir: Path, commits: list[FixCommit]
) -> str:
    _ = repo_root
    return "\n".join(
        [
            "# Ingestion Log",
            "",
            "Repository: `.`",
            f"Output directory: `{output_dir.name}`",
            f"Collected fix commits: {len(commits)}",
            "",
            "Phase 1 uses local git history only and writes deterministic onboarding artifacts.",
            "",
        ]
    )


def _build_pattern_template() -> str:
    return """---
id: PM-XXXX
title: Pattern title
status: active
severity: medium
confidence: medium
subsystems:
  - ui
related_commits:
  - abc1234
related_files:
  - src/example.py
release_checks:
  - Run focused regression tests.
---

# Summary

Short pattern summary.

# Release Review Checklist

- Review touched files.
- Run related regression tests.
"""


def _build_release_risk_template() -> str:
    return """---
release: v0.0.0
date: YYYY-MM-DD
commit_range: abc123..def456
matched_patterns: []
risk_level: medium
---

# Release Risk Summary

Summarize the release risk here.

# Pattern Matches

List matched patterns and recommended checks.
"""


def _render_pattern(pattern: PatternRecord) -> str:
    subsystem_list = pattern["subsystems"]
    related_commits = pattern["related_commits"]
    related_files = pattern["related_files"]
    release_checks = pattern["release_checks"]
    yaml_header = yaml.safe_dump(
        {
            "id": pattern["id"],
            "title": pattern["title"],
            "status": "active",
            "severity": pattern["severity"],
            "confidence": pattern["confidence"],
            "subsystems": subsystem_list,
            "related_commits": related_commits,
            "related_files": related_files,
            "release_checks": release_checks,
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return (
        f"---\n{yaml_header}\n---\n\n"
        f"# Summary\n\n{pattern['summary']}\n\n"
        f"# Trigger Conditions\n\n"
        f"- Changes in {', '.join(subsystem_list)} paths\n"
        f"- Historical commit: `{pattern['subject']}`\n\n"
        f"# Known Fix Signals\n\n"
        f"{'\n'.join(f'- `{path}`' for path in related_files)}\n\n"
        f"# Release Review Checklist\n\n"
        f"{'\n'.join(f'- {check}' for check in release_checks)}\n"
    )


def _write_yaml(path: Path, payload: object) -> None:
    _write_text(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            _ = handle.write(content)
            handle.flush()
            _ = os.fsync(handle.fileno())
        os.replace(temp_path, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            _ = os.fsync(dir_fd)
        finally:
            _ = os.close(dir_fd)
    except Exception:
        try:
            _ = os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
