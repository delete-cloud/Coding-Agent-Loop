"""File operation tools — read, write, replace, glob, grep."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentkit.tools import tool

_WORKSPACE_ROOT: Path | None = None


def configure_workspace(root: Path | str | None) -> None:
    global _WORKSPACE_ROOT
    _WORKSPACE_ROOT = None if root is None else Path(root).resolve()


def _resolve_workspace_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if _WORKSPACE_ROOT is None:
        return candidate.resolve()

    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (_WORKSPACE_ROOT / candidate).resolve()
    )

    try:
        resolved.relative_to(_WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"Path is outside workspace root: {path}") from exc

    return resolved


def build_file_tools(
    workspace_root: Path | str | None,
) -> tuple[
    Callable[[str], str],
    Callable[[str, str], str],
    Callable[[str, str, str], str],
    Callable[[str, str], str],
    Callable[[str, str, str], str],
]:
    root = None if workspace_root is None else Path(workspace_root).resolve()

    def resolve_path(path: str) -> Path:
        candidate = Path(path).expanduser()
        if root is None:
            return candidate.resolve()

        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )

        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Path is outside workspace root: {path}") from exc

        return resolved

    @tool(
        name="file_read",
        description="Read file contents. Returns file text or error message.",
    )
    def bound_file_read(path: str) -> str:
        try:
            p = resolve_path(path)
            if not p.exists():
                return f"Error: file not found: {path}"
            return p.read_text()
        except Exception as e:
            return f"Error reading {path}: {e}"

    @tool(
        name="file_write",
        description="Write content to a file. Creates parent directories if needed.",
    )
    def bound_file_write(path: str, content: str) -> str:
        try:
            p = resolve_path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Written {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error writing {path}: {e}"

    @tool(name="file_replace", description="Replace exact string in a file.")
    def bound_file_replace(path: str, old: str, new: str) -> str:
        try:
            p = resolve_path(path)
            if not p.exists():
                return f"Error: file not found: {path}"
            content = p.read_text()
            if old not in content:
                return f"Error: '{old}' not found in {path}"
            updated = content.replace(old, new, 1)
            p.write_text(updated)
            return f"Replaced in {path}"
        except Exception as e:
            return f"Error: {e}"

    @tool(name="glob_files", description="Search for files matching a glob pattern.")
    def bound_glob_files(pattern: str, directory: str = ".") -> str:
        try:
            base = resolve_path(directory)
            matches = sorted(str(p) for p in base.glob(pattern))
            if not matches:
                return "No files matched."
            return "\n".join(matches[:100])
        except Exception as e:
            return f"Error: {e}"

    @tool(name="grep_search", description="Search file contents for a regex pattern.")
    def bound_grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
        import subprocess

        try:
            search_root = resolve_path(directory)
            cmd = ["grep", "-rn", pattern, str(search_root)]
            if include:
                cmd.extend(["--include", include])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout.strip()
            if not output:
                return "No matches found."
            lines = output.split("\n")
            if len(lines) > 50:
                return "\n".join(lines[:50]) + f"\n... ({len(lines)} total matches)"
            return output
        except Exception as e:
            return f"Error: {e}"

    return (
        bound_file_read,
        bound_file_write,
        bound_file_replace,
        bound_glob_files,
        bound_grep_search,
    )


@tool(description="Read file contents. Returns file text or error message.")
def file_read(path: str) -> str:
    return build_file_tools(_WORKSPACE_ROOT)[0](path)


@tool(description="Write content to a file. Creates parent directories if needed.")
def file_write(path: str, content: str) -> str:
    return build_file_tools(_WORKSPACE_ROOT)[1](path, content)


@tool(description="Replace exact string in a file.")
def file_replace(path: str, old: str, new: str) -> str:
    return build_file_tools(_WORKSPACE_ROOT)[2](path, old, new)


@tool(description="Search for files matching a glob pattern.")
def glob_files(pattern: str, directory: str = ".") -> str:
    return build_file_tools(_WORKSPACE_ROOT)[3](pattern, directory)


@tool(description="Search file contents for a regex pattern.")
def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
    return build_file_tools(_WORKSPACE_ROOT)[4](pattern, directory, include)
