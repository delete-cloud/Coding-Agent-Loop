from __future__ import annotations

from pathlib import Path

from coding_agent.tools.cache import ToolCache


def test_invalidate_removes_matching_file_read_entry(tmp_path: Path) -> None:
    cache = ToolCache(max_size=10)
    target = tmp_path / "tracked.py"
    target.write_text("print('hello')")

    cache.set("file_read", {"path": "tracked.py"}, "cached", tmp_path)

    assert cache.get("file_read", {"path": "tracked.py"}, tmp_path) == "cached"

    cache.invalidate("tracked.py", tmp_path)

    assert cache.get("file_read", {"path": "tracked.py"}, tmp_path) is None
