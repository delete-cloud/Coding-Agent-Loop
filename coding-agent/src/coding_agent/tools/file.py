"""File operations: read, write, replace."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from coding_agent.tools.registry import ToolRegistry


def register_file_tools(registry: ToolRegistry, repo_root: Path | str = ".") -> None:
    """Register file operation tools.

    Args:
        registry: Tool registry to register to
        repo_root: Root directory for file operations
    """
    root = Path(repo_root).resolve()

    async def file_read(path: str, limit: int = 1000) -> str:
        """Read file content.

        Args:
            path: Relative path to file
            limit: Maximum number of lines to read

        Returns:
            File content or error message
        """
        try:
            target = _resolve_path(root, path)
            if not target.exists():
                return json.dumps({"error": f"File not found: {path}"})
            if not target.is_file():
                return json.dumps({"error": f"Not a file: {path}"})

            # Read as UTF-8 by default, but tolerate non-UTF8 bytes (e.g. binary files)
            # by replacing invalid sequences. This preserves behavior for valid UTF-8 files.
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Limit lines
            if len(lines) > limit:
                content = "".join(lines[:limit])
                content += f"\n... ({len(lines) - limit} more lines)"
            else:
                content = "".join(lines)

            return json.dumps(
                {
                    "path": path,
                    "content": content,
                    "lines": len(lines),
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def file_write(path: str, content: str) -> str:
        """Write content to a new file.

        Args:
            path: Relative path to file
            content: Content to write

        Returns:
            Success message or error
        """
        try:
            target = _resolve_path(root, path)

            # Don't overwrite existing files
            if target.exists():
                return json.dumps(
                    {
                        "error": f"File already exists: {path}. Use file_replace to modify existing files."
                    }
                )

            # Create parent directories if needed
            target.parent.mkdir(parents=True, exist_ok=True)

            with open(target, "w", encoding="utf-8") as f:
                f.write(content)

            return json.dumps(
                {
                    "success": True,
                    "path": path,
                    "bytes_written": len(content.encode("utf-8")),
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def file_replace(path: str, old_string: str, new_string: str) -> str:
        """Replace text in a file.

        Args:
            path: Relative path to file
            old_string: Text to find (must match exactly)
            new_string: Text to replace with

        Returns:
            Success message or error
        """
        try:
            target = _resolve_path(root, path)

            if not target.exists():
                return json.dumps({"error": f"File not found: {path}"})
            if not target.is_file():
                return json.dumps({"error": f"Not a file: {path}"})

            # Be tolerant of non-UTF8 bytes in the target file.
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if old_string not in content:
                return json.dumps(
                    {"error": f"old_string not found in file: {old_string[:50]}..."}
                )

            # Count occurrences
            count = content.count(old_string)
            if count > 1:
                return json.dumps(
                    {
                        "error": f"old_string appears {count} times in file. Must be unique."
                    }
                )

            new_content = content.replace(old_string, new_string)

            with open(target, "w", encoding="utf-8") as f:
                f.write(new_content)

            return json.dumps(
                {
                    "success": True,
                    "path": path,
                    "replacements": 1,
                }
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

    @dataclass
    class _Hunk:
        old_start: int
        old_count: int
        new_start: int
        new_count: int
        lines: List[Tuple[str, str]]  # (tag, text) where tag in {' ', '+', '-'}

    def _parse_unified_diff(patch_text: str) -> List[_Hunk]:
        """Parse a (single-file) unified diff into hunks.

        Accepts typical git-style diffs but only consumes the hunk sections.
        """
        lines = patch_text.splitlines(keepends=True)
        hunks: List[_Hunk] = []
        i = 0
        hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
        while i < len(lines):
            m = hunk_header_re.match(lines[i])
            if not m:
                i += 1
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            i += 1
            hunk_lines: List[Tuple[str, str]] = []
            while i < len(lines):
                l = lines[i]
                if l.startswith("@@ "):
                    break
                if l.startswith("\\ No newline at end of file"):
                    i += 1
                    continue
                if l == "\n" or l == "\r\n":
                    # blank line in diff context is represented as ' ' + '\n' typically,
                    # but tolerate raw blank lines by treating as context.
                    hunk_lines.append((" ", l))
                    i += 1
                    continue
                tag = l[:1]
                if tag not in (" ", "+", "-"):
                    # ignore diff metadata lines
                    break
                hunk_lines.append((tag, l[1:]))
                i += 1
            hunks.append(_Hunk(old_start, old_count, new_start, new_count, hunk_lines))
        if not hunks:
            raise ValueError("No hunks found in patch")
        return hunks

    def _find_hunk_pos(
        file_lines: List[str], hunk: _Hunk, search_window: int = 50
    ) -> Optional[int]:
        """Find the best position to apply the hunk.

        Prefer the line indicated by hunk.old_start, but allow small drift by searching
        for the context sequence in a window.

        Returns:
            0-based index into file_lines where hunk should be applied, or None.
        """
        # Build the sequence of old lines expected in the file: context + deletions
        expected = [text for (tag, text) in hunk.lines if tag in (" ", "-")]
        if not expected:
            # Pure insertion: apply at old_start (clamped)
            return max(0, min(len(file_lines), hunk.old_start - 1))

        def matches_at(pos: int) -> bool:
            if pos < 0:
                return False
            if pos + len(expected) > len(file_lines):
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
        file_lines: List[str], hunks: List[_Hunk]
    ) -> Tuple[List[str], List[dict[str, object]]]:
        """Apply hunks to file lines, returning new lines and per-hunk results."""
        out = list(file_lines)
        results: List[dict[str, object]] = []
        offset = 0
        for idx, hunk in enumerate(hunks):
            # Recompute position on current output for robustness.
            pos = _find_hunk_pos(out, hunk)
            if pos is None:
                results.append(
                    {
                        "hunk": idx,
                        "status": "failed",
                        "error": "Context not found for hunk",
                        "old_start": hunk.old_start,
                    }
                )
                raise ValueError("Context not found for hunk")

            cursor = pos
            new_block: List[str] = []
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

            # Replace the affected range [pos, cursor) with new_block
            before = out[:pos]
            after = out[cursor:]
            out = before + new_block + after

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

    async def file_patch(path: str, patch: str) -> str:
        """Apply a unified diff patch to an existing file.

        The patch should contain one or more hunks (lines starting with @@ ... @@).
        Context is verified before applying; if any hunk fails, no changes are written.
        """
        try:
            target = _resolve_path(root, path)
            if not target.exists():
                return json.dumps(
                    {"success": False, "error": f"File not found: {path}"}
                )
            if not target.is_file():
                return json.dumps({"success": False, "error": f"Not a file: {path}"})

            with open(target, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
            original_lines = original.splitlines(keepends=True)

            hunks = _parse_unified_diff(patch)
            new_lines, hunk_results = _apply_hunks_to_lines(original_lines, hunks)
            new_content = "".join(new_lines)

            if new_content == original:
                return json.dumps(
                    {
                        "success": True,
                        "path": path,
                        "changed": False,
                        "hunks": hunk_results,
                    }
                )

            # Safe write: write to temp then replace
            tmp = target.with_suffix(target.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new_content)
            tmp.replace(target)

            return json.dumps(
                {
                    "success": True,
                    "path": path,
                    "changed": True,
                    "bytes_written": len(new_content.encode("utf-8")),
                    "hunks": hunk_results,
                }
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e), "path": path})

    # Register tools
    registry.register(
        name="file_read",
        description="Read content of a file. Returns file content as string.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: 1000)",
                    "default": 1000,
                },
            },
            "required": ["path"],
        },
        handler=file_read,
    )

    registry.register(
        name="file_write",
        description="Create a new file with given content. Fails if file already exists.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=file_write,
    )

    registry.register(
        name="file_replace",
        description="Replace text in an existing file. The old_string must match exactly and appear only once.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (must be unique in file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Text to replace with",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=file_replace,
    )

    registry.register(
        name="file_patch",
        description="Apply unified diff format patches to files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file",
                },
                "patch": {
                    "type": "string",
                    "description": "Unified diff patch text",
                },
            },
            "required": ["path", "patch"],
        },
        handler=file_patch,
    )


def _resolve_path(root: Path, path: str) -> Path:
    """Resolve a path relative to root, preventing directory traversal."""
    target = (root / path).resolve()
    # Ensure the path is within root
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"Path must be within repository root: {path}")
    return target
