"""File operation tools — read, write, replace, glob, grep."""

from __future__ import annotations

from pathlib import Path

from agentkit.tools import tool


@tool(description="Read file contents. Returns file text or error message.")
def file_read(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: file not found: {path}"
        return p.read_text()
    except Exception as e:
        return f"Error reading {path}: {e}"


@tool(description="Write content to a file. Creates parent directories if needed.")
def file_write(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


@tool(description="Replace exact string in a file.")
def file_replace(path: str, old: str, new: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
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


@tool(description="Search for files matching a glob pattern.")
def glob_files(pattern: str, directory: str = ".") -> str:
    try:
        base = Path(directory).expanduser().resolve()
        matches = sorted(str(p) for p in base.glob(pattern))
        if not matches:
            return "No files matched."
        return "\n".join(matches[:100])
    except Exception as e:
        return f"Error: {e}"


@tool(description="Search file contents for a regex pattern.")
def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
    import subprocess

    try:
        cmd = ["grep", "-rn", pattern, directory]
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
