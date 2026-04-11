"""Search tools: grep and glob."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from coding_agent.tools.registry import ToolRegistry


def register_search_tools(registry: ToolRegistry, repo_root: Path | str = ".") -> None:
    """Register search tools.
    
    Args:
        registry: Tool registry to register to
        repo_root: Root directory for search
    """
    root = Path(repo_root).resolve()

    async def grep(pattern: str, path: str = ".", include: str | None = None) -> str:
        """Search file contents using regex.
        
        Args:
            pattern: Regex pattern to search for
            path: Directory or file to search in (relative to repo root)
            include: File pattern to include (e.g., "*.py")
            
        Returns:
            Search results with file paths and line numbers
        """
        try:
            target = _resolve_path(root, path)
            results = []
            
            if target.is_file():
                files = [target]
            else:
                files = list(target.rglob("*"))
                files = [f for f in files if f.is_file()]
            
            # Filter by pattern if specified
            if include:
                files = [f for f in files if fnmatch.fnmatch(f.name, include)]
            
            # Skip binary files and common non-source directories
            skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}
            files = [
                f for f in files
                if not any(skip in f.parts for skip in skip_dirs)
                and not f.name.startswith(".")
            ]
            
            compiled = re.compile(pattern)
            max_results = 50
            max_file_size = 10 * 1024 * 1024  # 10MB limit
            total_matches = 0
            
            for file_path in files:
                try:
                    # Skip files that are too large
                    if file_path.stat().st_size > max_file_size:
                        continue
                        
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if compiled.search(line):
                                rel_path = file_path.relative_to(root)
                                results.append({
                                    "path": str(rel_path),
                                    "line": line_num,
                                    "content": line.rstrip()[:200],  # Limit line length
                                })
                                total_matches += 1
                                
                                if len(results) >= max_results:
                                    return json.dumps({
                                        "pattern": pattern,
                                        "matches": results,
                                        "total_found": f"{max_results}+ (truncated)",
                                        "note": f"Found at least {max_results} matches, showing first {max_results}",
                                    })
                except Exception:
                    continue
            
            return json.dumps({
                "pattern": pattern,
                "matches": results,
                "total_found": total_matches,
            })
            
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def glob(pattern: str, path: str = ".") -> str:
        """Find files by glob pattern.
        
        Args:
            pattern: Glob pattern (e.g., "*.py", "src/**/*.py")
            path: Directory to search in (relative to repo root)
            
        Returns:
            List of matching file paths
        """
        try:
            target = _resolve_path(root, path)
            
            # Support both **/pattern and simple patterns
            if "**" in pattern:
                # Use rglob for recursive patterns
                matches = list(target.rglob(pattern.lstrip("/")))
            else:
                matches = list(target.glob(pattern))
            
            # Only files, not directories
            matches = [m for m in matches if m.is_file()]
            
            # Convert to relative paths
            rel_paths = [str(m.relative_to(root)) for m in matches]
            
            # Sort for consistent output
            rel_paths.sort()
            
            # Limit results
            max_results = 100
            total = len(rel_paths)
            if len(rel_paths) > max_results:
                rel_paths = rel_paths[:max_results]
                truncated = True
            else:
                truncated = False
            
            return json.dumps({
                "pattern": pattern,
                "matches": rel_paths,
                "total": total,
                "truncated": truncated,
            })
            
        except Exception as e:
            return json.dumps({"error": str(e)})

    registry.register(
        name="grep",
        description="Search file contents using regex pattern. Returns matching lines with file paths and line numbers.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: current directory)",
                    "default": ".",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to include, e.g., '*.py' (optional)",
                },
            },
            "required": ["pattern"],
        },
        handler=grep,
    )

    registry.register(
        name="glob",
        description="Find files by glob pattern. Returns list of matching file paths.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g., '*.py', 'src/**/*.py'",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
        handler=glob,
    )


def _resolve_path(root: Path, path: str) -> Path:
    """Resolve a path relative to root, preventing directory traversal."""
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"Path must be within repository root: {path}")
    return target
