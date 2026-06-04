from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agentkit.tools import tool
from coding_agent.action_safety import build_patch_plan, validate_safe_edit_path


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(patch_text: str) -> list[_Hunk]:
    lines = patch_text.splitlines(keepends=True)
    hunks: list[_Hunk] = []
    i = 0
    while i < len(lines):
        m = _HUNK_HEADER.match(lines[i])
        if not m:
            i += 1
            continue
        hunk = _Hunk(
            old_start=int(m.group(1)),
            old_count=int(m.group(2) or "1"),
            new_start=int(m.group(3)),
            new_count=int(m.group(4) or "1"),
        )
        i += 1
        while i < len(lines):
            raw = lines[i]
            if raw.startswith("@@ "):
                break
            if raw.startswith("\\ No newline at end of file"):
                i += 1
                continue
            if raw in ("\n", "\r\n"):
                hunk.lines.append((" ", raw))
                i += 1
                continue
            tag = raw[:1]
            if tag not in (" ", "+", "-"):
                break
            hunk.lines.append((tag, raw[1:]))
            i += 1
        hunks.append(hunk)
    if not hunks:
        raise ValueError("No hunks found in patch")
    return hunks


def _find_hunk_pos(
    file_lines: list[str], hunk: _Hunk, search_window: int = 50
) -> int | None:
    expected = [text for (tag, text) in hunk.lines if tag in (" ", "-")]
    if not expected:
        return max(0, min(len(file_lines), hunk.old_start - 1))

    def matches_at(pos: int) -> bool:
        if pos < 0 or pos + len(expected) > len(file_lines):
            return False
        return file_lines[pos : pos + len(expected)] == expected

    preferred = max(0, hunk.old_start - 1)
    if matches_at(preferred):
        return preferred

    start = max(0, preferred - search_window)
    end = min(len(file_lines), preferred + search_window)
    for pos in range(start, end + 1):
        if matches_at(pos):
            return pos
    return None


def _apply_hunks_to_lines(
    file_lines: list[str], hunks: list[_Hunk]
) -> tuple[list[str], list[dict[str, int | str]]]:
    out = list(file_lines)
    results: list[dict[str, int | str]] = []
    for idx, hunk in enumerate(hunks):
        pos = _find_hunk_pos(out, hunk)
        if pos is None:
            raise ValueError("Context not found for hunk")

        cursor = pos
        new_block: list[str] = []
        for tag, text in hunk.lines:
            if tag == " ":
                if cursor >= len(out) or out[cursor] != text:
                    raise ValueError("Context mismatch while applying hunk")
                new_block.append(text)
                cursor += 1
            elif tag == "-":
                if cursor >= len(out) or out[cursor] != text:
                    raise ValueError("Deletion mismatch while applying hunk")
                cursor += 1
            elif tag == "+":
                new_block.append(text)

        out = out[:pos] + new_block + out[cursor:]
        results.append(
            {
                "hunk": idx,
                "status": "applied",
                "applied_at": pos + 1,
                "old_start": hunk.old_start,
                "new_start": hunk.new_start,
            }
        )
    return out, results


def build_file_patch_tool(
    workspace_root: Path | str | None,
    *,
    additional_roots: list[Path | str] | tuple[Path | str, ...] = (),
) -> Callable[[str, str], str]:
    root = None if workspace_root is None else Path(workspace_root).resolve()
    resolved_additional_roots = tuple(
        Path(extra).expanduser().resolve() for extra in additional_roots
    )

    def _matching_root(path: Path) -> Path | None:
        if root is None:
            return None
        roots = (root, *resolved_additional_roots)
        for candidate_root in roots:
            try:
                path.relative_to(candidate_root)
            except ValueError:
                continue
            return candidate_root
        return None

    def _resolve(path: str) -> Path:
        candidate = Path(path).expanduser()
        if root is None:
            return candidate.resolve()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        if _matching_root(resolved) is None:
            raise ValueError(f"Path is outside workspace root: {path}")
        return resolved

    @tool(
        name="file_patch",
        description="Apply a unified diff patch to an existing file. The patch should contain @@ hunk headers.",
    )
    def bound_file_patch(path: str, patch: str, dry_run: bool = False) -> str:
        try:
            target = _resolve(path)
            if not target.exists():
                return json.dumps(
                    {"success": False, "error": f"File not found: {path}"}
                )
            if not target.is_file():
                return json.dumps({"success": False, "error": f"Not a file: {path}"})
            edit_root = _matching_root(target)
            if edit_root is not None:
                edit_decision = validate_safe_edit_path(edit_root, target)
                if not edit_decision.allowed:
                    return json.dumps(
                        {
                            "success": False,
                            "path": path,
                            "dry_run": dry_run,
                            "error": "Safe edit policy rejected target",
                            "reason": edit_decision.reason.value,
                            "decision": edit_decision.to_safe_dict(),
                        }
                    )

            plan = build_patch_plan(path, patch, file_exists=True)

            original = target.read_text(encoding="utf-8", errors="replace")
            original_lines = original.splitlines(keepends=True)

            hunks = _parse_unified_diff(patch)
            new_lines, hunk_results = _apply_hunks_to_lines(original_lines, hunks)
            new_content = "".join(new_lines)

            if dry_run:
                return json.dumps(
                    {
                        "success": True,
                        "path": path,
                        "dry_run": True,
                        "changed": new_content != original,
                        "plan": plan.to_safe_dict(),
                        "hunks": hunk_results,
                    }
                )

            if new_content == original:
                return json.dumps(
                    {
                        "success": True,
                        "path": path,
                        "dry_run": False,
                        "changed": False,
                        "hunks": hunk_results,
                    }
                )

            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(new_content, encoding="utf-8")
            tmp.replace(target)

            return json.dumps(
                {
                    "success": True,
                    "path": path,
                    "dry_run": False,
                    "changed": True,
                    "bytes_written": len(new_content.encode("utf-8")),
                    "hunks": hunk_results,
                }
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": str(e), "path": path, "dry_run": dry_run}
            )

    return bound_file_patch
