"""Tests for file_patch tool."""

import json
import pytest

from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.file import register_file_tools


class TestFilePatchTool:
    """Tests for file_patch tool execution."""

    @pytest.fixture
    def registry(self, tmp_path):
        """Create a tool registry with file tools."""
        reg = ToolRegistry()
        register_file_tools(reg, repo_root=tmp_path)
        return reg

    @pytest.fixture
    def sample_file(self, tmp_path):
        """Create a sample file for patching."""
        file_path = tmp_path / "sample.txt"
        content = """line 1
line 2
line 3
line 4
line 5
"""
        file_path.write_text(content)
        return "sample.txt"

    @pytest.mark.asyncio
    async def test_basic_patch(self, registry, tmp_path, sample_file):
        """Test basic patch application."""
        patch = """@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 modified
 line 3
"""
        result = await registry.execute("file_patch", {
            "path": sample_file,
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["changed"] is True
        assert parsed["path"] == sample_file
        
        # Verify the file was modified
        content = (tmp_path / sample_file).read_text()
        assert "line 2 modified" in content
        assert "line 2\nline 3" not in content

    @pytest.mark.asyncio
    async def test_patch_not_found_file(self, registry):
        """Test patching a non-existent file."""
        patch = """@@ -1,2 +1,2 @@
 line 1
-line 2
+line 2 modified
"""
        result = await registry.execute("file_patch", {
            "path": "nonexistent.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "not found" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_patch_context_mismatch(self, registry, tmp_path, sample_file):
        """Test patch with wrong context fails gracefully."""
        patch = """@@ -1,3 +1,3 @@
 line 1
-this line does not exist
+replacement
 line 3
"""
        result = await registry.execute("file_patch", {
            "path": sample_file,
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "context" in parsed["error"].lower() or "mismatch" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_patch_multiple_hunks(self, registry, tmp_path):
        """Test patch with multiple hunks."""
        file_path = tmp_path / "multi.txt"
        file_path.write_text("""first section
line a
line b
second section
line x
line y
""")
        
        patch = """@@ -1,3 +1,3 @@
 first section
-line a
+line a modified
 line b
@@ -4,3 +4,3 @@
 second section
-line x
+line x modified
 line y
"""
        result = await registry.execute("file_patch", {
            "path": "multi.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["changed"] is True
        
        content = file_path.read_text()
        assert "line a modified" in content
        assert "line x modified" in content

    @pytest.mark.asyncio
    async def test_patch_insert_lines(self, registry, tmp_path):
        """Test patch that inserts new lines."""
        file_path = tmp_path / "insert.txt"
        file_path.write_text("""line 1
line 2
line 3
""")
        
        patch = """@@ -1,2 +1,4 @@
 line 1
+inserted line 1
+inserted line 2
 line 2
"""
        result = await registry.execute("file_patch", {
            "path": "insert.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert "inserted line 1" in content
        assert "inserted line 2" in content
        lines = content.strip().split("\n")
        assert len(lines) == 5  # original 3 + 2 inserted

    @pytest.mark.asyncio
    async def test_patch_delete_lines(self, registry, tmp_path):
        """Test patch that deletes lines."""
        file_path = tmp_path / "delete.txt"
        file_path.write_text("""line 1
line 2
line 3
line 4
""")
        
        patch = """@@ -1,4 +1,2 @@
 line 1
-line 2
-line 3
 line 4
"""
        result = await registry.execute("file_patch", {
            "path": "delete.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "line 1" in content
        assert "line 4" in content
        assert "line 2" not in content

    @pytest.mark.asyncio
    async def test_patch_no_changes(self, registry, tmp_path):
        """Test patch that makes no actual changes."""
        file_path = tmp_path / "nochange.txt"
        file_path.write_text("""line 1
line 2
line 3
""")
        
        # Patch with same content (no actual change)
        patch = """@@ -1,3 +1,3 @@
 line 1
 line 2
 line 3
"""
        result = await registry.execute("file_patch", {
            "path": "nochange.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["changed"] is False

    @pytest.mark.asyncio
    async def test_patch_at_end_of_file(self, registry, tmp_path):
        """Test patching at the end of a file."""
        file_path = tmp_path / "end.txt"
        file_path.write_text("""line 1
line 2
line 3
""")
        
        patch = """@@ -3,1 +3,2 @@
 line 3
+appended line
"""
        result = await registry.execute("file_patch", {
            "path": "end.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert "appended line" in content

    @pytest.mark.asyncio
    async def test_patch_empty_file(self, registry, tmp_path):
        """Test patching an empty file."""
        file_path = tmp_path / "empty.txt"
        file_path.write_text("")
        
        # Insert into empty file
        patch = """@@ -0,0 +1,2 @@
+line 1
+line 2
"""
        result = await registry.execute("file_patch", {
            "path": "empty.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert "line 1" in content
        assert "line 2" in content

    @pytest.mark.asyncio
    async def test_patch_python_code(self, registry, tmp_path):
        """Test patching a Python file."""
        file_path = tmp_path / "script.py"
        original_code = '''def hello():
    print("hello")
    return 42
'''
        file_path.write_text(original_code)
        
        patch = '''@@ -1,3 +1,4 @@
 def hello():
     print("hello")
+    print("world")
     return 42
'''
        result = await registry.execute("file_patch", {
            "path": "script.py",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert 'print("world")' in content

    @pytest.mark.asyncio
    async def test_patch_with_fuzzy_context(self, registry, tmp_path):
        """Test patch with slightly offset context."""
        file_path = tmp_path / "fuzzy.txt"
        file_path.write_text("""line 1
line 2
line 3
line 4
line 5
line 6
line 7
line 8
line 9
line 10
""")
        
        # Patch targets line 5 but context might be found nearby
        patch = """@@ -5,2 +5,3 @@
 line 5
+inserted after 5
 line 6
"""
        result = await registry.execute("file_patch", {
            "path": "fuzzy.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert "inserted after 5" in content

    @pytest.mark.asyncio
    async def test_patch_invalid_diff_format(self, registry, tmp_path):
        """Test patch with invalid diff format."""
        file_path = tmp_path / "invalid.txt"
        file_path.write_text("some content\n")
        
        # No proper hunk header
        patch = """some invalid patch
without proper headers
"""
        result = await registry.execute("file_patch", {
            "path": "invalid.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "hunk" in parsed["error"].lower() or "patch" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_patch_with_line_count_omitted(self, registry, tmp_path):
        """Test patch where line count is omitted (defaults to 1)."""
        file_path = tmp_path / "omitted.txt"
        file_path.write_text("""line 1
line 2
line 3
""")
        
        # @@ -2 +2,2 @@ means old_start=2, old_count=1, new_start=2, new_count=2
        patch = """@@ -2 +2,2 @@
 line 2
+inserted line
"""
        result = await registry.execute("file_patch", {
            "path": "omitted.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_text()
        assert "inserted line" in content

    @pytest.mark.asyncio
    async def test_patch_preserves_newlines(self, registry, tmp_path):
        """Test that patch preserves different newline styles."""
        file_path = tmp_path / "newlines.txt"
        # File with LF newlines
        file_path.write_text("line 1\nline 2\nline 3\n")
        
        patch = """@@ -1,3 +1,3 @@
 line 1
-line 2
+modified line 2
 line 3
"""
        result = await registry.execute("file_patch", {
            "path": "newlines.txt",
            "patch": patch
        })
        
        parsed = json.loads(result)
        assert parsed["success"] is True
        
        content = file_path.read_bytes()
        # Should still have LF newlines
        assert b"\n" in content

    @pytest.mark.asyncio
    async def test_patch_tool_registered(self, registry):
        """Test that file_patch tool is properly registered."""
        tool_names = registry.list_tools()
        assert "file_patch" in tool_names
        
        # Check the schema via schemas() method
        schemas = registry.schemas()
        patch_schema = next(
            (s for s in schemas if s.function["name"] == "file_patch"),
            None
        )
        assert patch_schema is not None
        assert "path" in patch_schema.function["parameters"]["properties"]
        assert "patch" in patch_schema.function["parameters"]["properties"]
        assert "path" in patch_schema.function["parameters"]["required"]
        assert "patch" in patch_schema.function["parameters"]["required"]
