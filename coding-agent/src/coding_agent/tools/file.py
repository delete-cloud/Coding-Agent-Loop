"""File operations: read, write, replace."""

from __future__ import annotations

import json
from pathlib import Path

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
            
            return json.dumps({
                "path": path,
                "content": content,
                "lines": len(lines),
            })
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
                return json.dumps({
                    "error": f"File already exists: {path}. Use file_replace to modify existing files."
                })
            
            # Create parent directories if needed
            target.parent.mkdir(parents=True, exist_ok=True)
            
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            
            return json.dumps({
                "success": True,
                "path": path,
                "bytes_written": len(content.encode("utf-8")),
            })
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
                return json.dumps({
                    "error": f"old_string not found in file: {old_string[:50]}..."
                })
            
            # Count occurrences
            count = content.count(old_string)
            if count > 1:
                return json.dumps({
                    "error": f"old_string appears {count} times in file. Must be unique."
                })
            
            new_content = content.replace(old_string, new_string)
            
            with open(target, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            return json.dumps({
                "success": True,
                "path": path,
                "replacements": 1,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

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


def _resolve_path(root: Path, path: str) -> Path:
    """Resolve a path relative to root, preventing directory traversal."""
    target = (root / path).resolve()
    # Ensure the path is within root
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"Path must be within repository root: {path}")
    return target
