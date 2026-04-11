import pytest

from coding_agent.tools.file_ops import (
    configure_workspace,
    file_read,
    file_replace,
    file_write,
    glob_files,
)


class TestFileOps:
    def setup_method(self):
        configure_workspace(None)

    def test_file_read(self, tmp_path):
        configure_workspace(tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = file_read(path="test.txt")
        assert "hello world" in result

    def test_file_read_missing(self, tmp_path):
        configure_workspace(tmp_path)
        result = file_read(path="missing.txt")
        assert isinstance(result, str)
        assert "error" in result.lower() or "not found" in result.lower()

    def test_file_write(self, tmp_path):
        configure_workspace(tmp_path)
        f = tmp_path / "out.txt"
        result = file_write(path="out.txt", content="written")
        assert f.read_text() == "written"

    def test_file_replace(self, tmp_path):
        configure_workspace(tmp_path)
        f = tmp_path / "repl.txt"
        f.write_text("old text here")
        result = file_replace(path="repl.txt", old="old", new="new")
        assert f.read_text() == "new text here"

    def test_file_read_rejects_escape_outside_workspace(self, tmp_path):
        configure_workspace(tmp_path)
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("nope")

        result = file_read(path=str(outside))

        assert isinstance(result, str)
        assert "outside workspace" in result.lower()

    def test_glob_files_rejects_escape_directory(self, tmp_path):
        configure_workspace(tmp_path)

        result = glob_files(pattern="*.txt", directory=str(tmp_path.parent))

        assert isinstance(result, str)
        assert "outside workspace" in result.lower()
