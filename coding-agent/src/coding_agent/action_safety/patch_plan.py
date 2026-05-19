from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


class PatchOperation(StrEnum):
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"


class PatchRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class PatchHunkPlan:
    index: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    additions: int
    deletions: int
    context_lines: int

    def to_safe_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "additions": self.additions,
            "deletions": self.deletions,
            "context_lines": self.context_lines,
        }


@dataclass(frozen=True)
class PatchPlan:
    path: str
    operation: PatchOperation
    file_exists: bool | None
    hunk_count: int
    additions: int
    deletions: int
    risk_level: PatchRiskLevel
    risk_reasons: tuple[str, ...]
    hunks: tuple[PatchHunkPlan, ...]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation.value,
            "file_exists": self.file_exists,
            "hunk_count": self.hunk_count,
            "additions": self.additions,
            "deletions": self.deletions,
            "risk_level": self.risk_level.value,
            "risk_reasons": list(self.risk_reasons),
            "hunks": [hunk.to_safe_dict() for hunk in self.hunks],
        }


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def build_patch_plan(
    path: str,
    patch: str,
    *,
    file_exists: bool | None = None,
) -> PatchPlan:
    hunks = _parse_hunk_plans(patch)
    additions = sum(hunk.additions for hunk in hunks)
    deletions = sum(hunk.deletions for hunk in hunks)
    operation = _classify_operation(hunks=hunks, file_exists=file_exists)
    risk_level, risk_reasons = _classify_risk(
        operation=operation,
        hunk_count=len(hunks),
        additions=additions,
        deletions=deletions,
    )
    return PatchPlan(
        path=path,
        operation=operation,
        file_exists=file_exists,
        hunk_count=len(hunks),
        additions=additions,
        deletions=deletions,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        hunks=tuple(hunks),
    )


def _parse_hunk_plans(patch: str) -> list[PatchHunkPlan]:
    lines = patch.splitlines(keepends=False)
    _reject_multi_file_patch(lines)
    hunks: list[PatchHunkPlan] = []
    i = 0
    while i < len(lines):
        match = _HUNK_HEADER.match(lines[i])
        if match is None:
            i += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        additions = 0
        deletions = 0
        context_lines = 0
        i += 1
        while i < len(lines):
            raw = lines[i]
            if raw.startswith("@@ "):
                break
            if raw.startswith("\\ No newline at end of file"):
                i += 1
                continue
            if not raw:
                context_lines += 1
                i += 1
                continue
            tag = raw[:1]
            if tag == "+":
                additions += 1
            elif tag == "-":
                deletions += 1
            elif tag == " ":
                context_lines += 1
            else:
                break
            i += 1
        expected_old_count = deletions + context_lines
        expected_new_count = additions + context_lines
        if expected_old_count != old_count or expected_new_count != new_count:
            raise ValueError(
                "Hunk body does not match header counts: "
                f"expected -{old_count} +{new_count}, "
                f"got -{expected_old_count} +{expected_new_count}"
            )
        hunks.append(
            PatchHunkPlan(
                index=len(hunks),
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                additions=additions,
                deletions=deletions,
                context_lines=context_lines,
            )
        )
    if not hunks:
        raise ValueError("No hunks found in patch")
    return hunks


def _reject_multi_file_patch(lines: list[str]) -> None:
    file_header_count = sum(
        1
        for line in lines
        if line.startswith("--- ")
        or line.startswith("+++ ")
        or line.startswith("diff ")
    )
    if file_header_count > 2:
        raise ValueError("Patch plan supports a single target file")


def _classify_operation(
    *,
    hunks: list[PatchHunkPlan],
    file_exists: bool | None,
) -> PatchOperation:
    additions = sum(hunk.additions for hunk in hunks)
    deletions = sum(hunk.deletions for hunk in hunks)
    if file_exists is False and additions > 0 and deletions == 0:
        return PatchOperation.CREATE
    if additions == 0 and deletions > 0:
        return PatchOperation.DELETE
    return PatchOperation.MODIFY


def _classify_risk(
    *,
    operation: PatchOperation,
    hunk_count: int,
    additions: int,
    deletions: int,
) -> tuple[PatchRiskLevel, tuple[str, ...]]:
    changed_lines = additions + deletions
    reasons: list[str] = []
    if operation in {PatchOperation.CREATE, PatchOperation.DELETE}:
        reasons.append(f"operation_{operation.value}")
    if hunk_count > 1:
        reasons.append("multiple_hunks")
    if changed_lines > 200:
        reasons.append("large_change")
    elif changed_lines > 25:
        reasons.append("moderate_change")

    if "large_change" in reasons or operation == PatchOperation.DELETE:
        return PatchRiskLevel.HIGH, tuple(reasons)
    if reasons:
        return PatchRiskLevel.MEDIUM, tuple(reasons)
    return PatchRiskLevel.LOW, ("small_modify",)
