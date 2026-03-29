import pytest
from coding_agent.tools.file_ops import file_read, file_write, file_replace


class TestFileOps:
    def test_file_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = file_read(path=str(f))
        assert "hello world" in result

    def test_file_read_missing(self, tmp_path):
        result = file_read(path=str(tmp_path / "missing.txt"))
        assert "error" in result.lower() or "not found" in result.lower()

    def test_file_write(self, tmp_path):
        f = tmp_path / "out.txt"
        result = file_write(path=str(f), content="written")
        assert f.read_text() == "written"

    def test_file_replace(self, tmp_path):
        f = tmp_path / "repl.txt"
        f.write_text("old text here")
        result = file_replace(path=str(f), old="old", new="new")
        assert f.read_text() == "new text here"
