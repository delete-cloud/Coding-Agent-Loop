"""Tests for KB progress tracking."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from coding_agent.kb import KB


class TestKBProgress:
    """Tests for KB progress functionality."""

    @pytest.mark.asyncio
    async def test_index_directory_shows_progress(self, tmp_path, monkeypatch):
        """Test that progress bar is displayed."""
        # Mock TTY environment
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        
        # Create test files
        for i in range(5):
            (tmp_path / f"file{i}.py").write_text(f"content {i}")

        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        # Mock Progress to capture calls - just verify it was instantiated
        with patch("coding_agent.kb.Progress") as mock_progress:
            await kb.index_directory(tmp_path, show_progress=True)

            # Verify progress was created
            assert mock_progress.called

    @pytest.mark.asyncio
    async def test_index_directory_no_progress_when_disabled(self, tmp_path):
        """Test that progress bar is not shown when disabled."""
        (tmp_path / "file.py").write_text("content")

        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        with patch("coding_agent.kb.Progress") as mock_progress:
            await kb.index_directory(tmp_path, show_progress=False)

            # Progress should not be created
            assert not mock_progress.called

    @pytest.mark.asyncio
    async def test_index_directory_no_progress_in_non_tty(self, tmp_path, monkeypatch):
        """Test that progress bar is not shown in non-TTY environment."""
        # Mock sys.stdout.isatty() to return False
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        (tmp_path / "file.py").write_text("content")

        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        with patch("coding_agent.kb.Progress") as mock_progress:
            await kb.index_directory(tmp_path, show_progress=True)

            # Progress should not be created in non-TTY
            assert not mock_progress.called

    @pytest.mark.asyncio
    async def test_index_directory_handles_errors(self, tmp_path):
        """Test that errors are handled gracefully."""
        # Create test files, including one that will cause an error
        (tmp_path / "file1.py").write_text("content 1")
        (tmp_path / "file2.py").write_bytes(b"\xff\xfe")  # Invalid UTF-8
        (tmp_path / "file3.py").write_text("content 3")

        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        # Should complete without raising
        await kb.index_directory(tmp_path, show_progress=False)

        # Verify at least one file was indexed
        table = kb._get_table()
        assert table.count_rows() > 0

    @pytest.mark.asyncio
    async def test_index_directory_empty_directory(self, tmp_path):
        """Test indexing an empty directory."""
        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        # Should complete without error
        await kb.index_directory(tmp_path, show_progress=True)

    @pytest.mark.asyncio
    async def test_index_directory_with_pattern(self, tmp_path):
        """Test indexing with a specific pattern."""
        # Create test files in subdirectories
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file1.py").write_text("content 1")
        (tmp_path / "file2.py").write_text("content 2")

        # Create KB with mock embedding function
        def mock_embed(texts):
            return [[0.1] * 1536 for _ in texts]

        kb = KB(
            db_path=tmp_path / "test.db",
            embedding_fn=mock_embed,
        )

        # Should complete without error
        await kb.index_directory(tmp_path, pattern="*.py", show_progress=False)
