"""Tool result caching with LRU eviction and file modification awareness."""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any


class ToolCache:
    """LRU cache for tool results with file modification awareness.

    Only caches file_read operations. Automatically invalidates when files
    are modified via file_write or file_replace.
    """

    def __init__(self, max_size: int = 100):
        """Initialize cache.

        Args:
            max_size: Maximum number of cached entries
        """
        self._max_size = max_size
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._paths_by_key: dict[str, str] = {}
        self._hit_count = 0
        self._miss_count = 0

    def _make_key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Create cache key from tool name and arguments.

        For file_read, also includes file mtime in the value (not key),
        so we can detect stale entries.
        """
        # Sort arguments for consistent key generation
        key_data = json.dumps(
            {
                "tool": tool_name,
                "args": arguments,
            },
            sort_keys=True,
        )
        return hashlib.sha256(key_data.encode()).hexdigest()

    def _get_file_mtime(self, path: str, repo_root: Path) -> float | None:
        """Get file modification time if file exists."""
        try:
            # Resolve both paths to handle symlinks
            resolved_root = repo_root.resolve()
            full_path = (resolved_root / path).resolve()
            # Security check: ensure path is within repo_root
            try:
                full_path.relative_to(resolved_root)
            except ValueError:
                return None  # Path is outside repo_root
            if full_path.exists() and full_path.is_file():
                return os.path.getmtime(full_path)
        except (OSError, ValueError):
            pass
        return None

    def get(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        repo_root: Path | None = None,
    ) -> str | None:
        """Get cached result if available and not stale.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            repo_root: Repository root for resolving file paths

        Returns:
            Cached result or None if not cached or stale
        """
        # Only cache file_read operations
        if tool_name != "file_read":
            return None

        key = self._make_key(tool_name, arguments)

        if key not in self._cache:
            self._miss_count += 1
            return None

        # For file_read, check if file has been modified
        if repo_root and "path" in arguments:
            cached_mtime = self._cache[key][1]
            current_mtime = self._get_file_mtime(arguments["path"], repo_root)

            # Normalize current_mtime: None -> -1.0
            if current_mtime is None:
                current_mtime = -1.0

            # If mtime changed, consider stale
            if current_mtime != cached_mtime:
                del self._cache[key]
                self._miss_count += 1
                return None

        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hit_count += 1
        return self._cache[key][0]

    def set(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        repo_root: Path | None = None,
    ) -> None:
        """Cache a tool result.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            result: Tool result to cache
            repo_root: Repository root for resolving file paths
        """
        # Only cache file_read operations
        if tool_name != "file_read":
            return

        key = self._make_key(tool_name, arguments)

        # Get file mtime for cache invalidation
        # Store -1.0 if file doesn't exist (distinguishes from real mtime >= 0)
        mtime: float = -1.0
        if repo_root and "path" in arguments:
            current_mtime = self._get_file_mtime(arguments["path"], repo_root)
            if current_mtime is None:
                mtime = -1.0
            else:
                mtime = current_mtime

        # Evict oldest if at capacity
        if len(self._cache) >= self._max_size and key not in self._cache:
            evicted_key, _ = self._cache.popitem(last=False)
            self._paths_by_key.pop(evicted_key, None)

        self._cache[key] = (result, mtime)
        if "path" in arguments and isinstance(arguments["path"], str):
            self._paths_by_key[key] = arguments["path"]
        self._cache.move_to_end(key)

    def invalidate(self, path: str, repo_root: Path) -> None:
        """Invalidate all cache entries for a file path.

        Called when file_write or file_replace modifies a file.

        Args:
            path: Path to the modified file
            repo_root: Repository root
        """
        resolved_root = repo_root.resolve()
        target = (resolved_root / path).resolve()

        try:
            target.relative_to(resolved_root)
        except ValueError:
            return

        keys_to_remove = [
            key
            for key, cached_path in self._paths_by_key.items()
            if (resolved_root / cached_path).resolve() == target
        ]

        for key in keys_to_remove:
            self._cache.pop(key, None)
            self._paths_by_key.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._paths_by_key.clear()
        self._hit_count = 0
        self._miss_count = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self._hit_count + self._miss_count
        hit_rate = self._hit_count / total if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": round(hit_rate, 3),
        }
