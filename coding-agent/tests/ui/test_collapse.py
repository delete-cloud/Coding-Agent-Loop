import time

from coding_agent.ui.collapse import CollapseGroup, is_collapsible


class TestIsCollapsible:
    def test_file_read_is_collapsible(self):
        assert is_collapsible("file_read") is True

    def test_grep_search_is_collapsible(self):
        assert is_collapsible("grep_search") is True

    def test_glob_files_is_collapsible(self):
        assert is_collapsible("glob_files") is True

    def test_grep_is_collapsible(self):
        assert is_collapsible("grep") is True

    def test_glob_is_collapsible(self):
        assert is_collapsible("glob") is True

    def test_bash_run_is_not_collapsible(self):
        assert is_collapsible("bash_run") is False

    def test_file_write_is_not_collapsible(self):
        assert is_collapsible("file_write") is False

    def test_file_replace_is_not_collapsible(self):
        assert is_collapsible("file_replace") is False

    def test_unknown_tool_is_not_collapsible(self):
        assert is_collapsible("custom_tool") is False


class TestCollapseGroup:
    def test_empty_group(self):
        group = CollapseGroup()
        assert group.is_empty is True
        assert group.summary_text() == ""

    def test_add_search_with_pattern(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "TODO"})
        assert group.search_count == 1
        assert group.search_patterns == ["TODO"]

    def test_add_search_with_regex_variant(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep", {"regex": "foo.*bar"})
        assert group.search_patterns == ["foo.*bar"]

    def test_add_read_with_path(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "main.py"})
        assert group.read_count == 1
        assert group.read_file_paths == ["main.py"]

    def test_add_read_with_file_path_variant(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"file_path": "x.py"})
        assert group.read_file_paths == ["x.py"]

    def test_add_glob(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "glob_files", {"pattern": "**/*.py"})
        assert group.list_count == 1

    def test_add_glob_alias(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "glob", {"pattern": "**/*.ts"})
        assert group.list_count == 1

    def test_has_call_true_before_result(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        assert group.has_call("c1") is True

    def test_has_call_false_after_result(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        group.add_tool_result("c1")
        assert group.has_call("c1") is False

    def test_error_tracking_positive(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        group.add_tool_result("c1", is_error=True)
        assert group.has_error is True

    def test_error_tracking_negative(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        group.add_tool_result("c1", is_error=False)
        assert group.has_error is False

    def test_summary_single_search(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "TODO"})
        assert group.summary_text() == "Searched for 1 pattern"

    def test_summary_plural_search(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "x"})
        group.add_tool_call("c2", "grep", {"pattern": "y"})
        assert group.summary_text() == "Searched for 2 patterns"

    def test_summary_single_read(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        assert group.summary_text() == "Read 1 file"

    def test_summary_plural_read(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        group.add_tool_call("c2", "file_read", {"path": "b.py"})
        assert group.summary_text() == "Read 2 files"

    def test_summary_list_singular(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "glob_files", {"pattern": "**/*.py"})
        assert group.summary_text() == "Listed 1 pattern"

    def test_summary_list_plural(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "glob_files", {"pattern": "**/*.py"})
        group.add_tool_call("c2", "glob", {"pattern": "**/*.ts"})
        assert group.summary_text() == "Listed 2 patterns"

    def test_summary_mixed_search_and_read(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "x"})
        group.add_tool_call("c2", "file_read", {"path": "a.py"})
        group.add_tool_call("c3", "file_read", {"path": "b.py"})
        assert group.summary_text() == "Searched for 1 pattern, read 2 files"

    def test_summary_all_types(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "x"})
        group.add_tool_call("c2", "file_read", {"path": "a.py"})
        group.add_tool_call("c3", "glob_files", {"pattern": "**/*.py"})
        text = group.summary_text()
        assert "Searched for 1 pattern" in text
        assert "read 1 file" in text
        assert "listed 1 pattern" in text

    def test_duration_is_non_negative(self):
        group = CollapseGroup()
        time.sleep(0.01)
        assert group.duration >= 0.0

    def test_is_empty_false_after_add(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        assert group.is_empty is False
