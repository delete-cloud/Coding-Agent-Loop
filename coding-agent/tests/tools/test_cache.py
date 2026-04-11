"""Tests for ToolCache functionality."""

from pathlib import Path

from coding_agent.tools.cache import ToolCache


class TestToolCache:
    def test_cache_hit_and_miss(self):
        """Test basic cache hit and miss behavior."""
        cache = ToolCache(max_size=10)
        repo_root = Path(".")

        # First access - miss
        result = cache.get("file_read", {"path": "test.py"}, repo_root)
        assert result is None

        # Set cache
        cache.set("file_read", {"path": "test.py"}, "cached content", repo_root)

        # Second access - hit
        result = cache.get("file_read", {"path": "test.py"}, repo_root)
        assert result == "cached content"

    def test_only_file_read_cached(self):
        """Only file_read operations should be cached."""
        cache = ToolCache(max_size=10)
        repo_root = Path(".")

        # file_read should be cached
        cache.set("file_read", {"path": "test.py"}, "content", repo_root)
        assert cache.get("file_read", {"path": "test.py"}, repo_root) == "content"

        # file_write should not be cached
        cache.set(
            "file_write", {"path": "test.py", "content": "x"}, "result", repo_root
        )
        assert (
            cache.get("file_write", {"path": "test.py", "content": "x"}, repo_root)
            is None
        )

        # bash should not be cached
        cache.set("bash", {"command": "ls"}, "result", repo_root)
        assert cache.get("bash", {"command": "ls"}, repo_root) is None

    def test_cache_stale_on_mtime_change(self, tmp_path):
        """Cache should be invalidated when file is modified."""
        cache = ToolCache(max_size=10)

        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        # Set cache
        cache.set("file_read", {"path": "test.py"}, "cached content", tmp_path)

        # Should get cached value
        result = cache.get("file_read", {"path": "test.py"}, tmp_path)
        assert result == "cached content"

        # Modify the file
        import time

        time.sleep(0.01)  # Ensure mtime changes
        test_file.write_text("modified content")

        # Cache should be stale now
        result = cache.get("file_read", {"path": "test.py"}, tmp_path)
        assert result is None

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = ToolCache(max_size=3)
        repo_root = Path(".")

        # Fill cache
        for i in range(3):
            cache.set("file_read", {"path": f"file{i}.py"}, f"content{i}", repo_root)

        # Access first item to make it recently used
        cache.get("file_read", {"path": "file0.py"}, repo_root)

        # Add new item - should evict file1 (least recently used)
        cache.set("file_read", {"path": "file3.py"}, "content3", repo_root)

        assert cache.get("file_read", {"path": "file0.py"}, repo_root) == "content0"
        assert (
            cache.get("file_read", {"path": "file1.py"}, repo_root) is None
        )  # Evicted
        assert cache.get("file_read", {"path": "file2.py"}, repo_root) == "content2"
        assert cache.get("file_read", {"path": "file3.py"}, repo_root) == "content3"

    def test_cache_stats(self):
        """Test cache statistics."""
        cache = ToolCache(max_size=10)
        repo_root = Path(".")

        # Initial stats
        stats = cache.stats
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0

        # Miss
        cache.get("file_read", {"path": "a.py"}, repo_root)
        stats = cache.stats
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.0

        # Hit
        cache.set("file_read", {"path": "a.py"}, "content", repo_root)
        cache.get("file_read", {"path": "a.py"}, repo_root)
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1  # Only the first miss counts

    def test_cache_clear(self):
        """Test clearing the cache."""
        cache = ToolCache(max_size=10)
        repo_root = Path(".")

        cache.set("file_read", {"path": "test.py"}, "content", repo_root)
        cache.get("file_read", {"path": "test.py"}, repo_root)  # Generate some stats

        cache.clear()

        assert len(cache._cache) == 0
        assert cache.stats["hits"] == 0
        assert cache.stats["misses"] == 0
