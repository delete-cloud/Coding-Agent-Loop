"""Tests for Rich TUI components."""

import pytest
from rich.panel import Panel

from coding_agent.ui.components import (
    create_header_panel,
    create_message_panel,
    create_plan_panel,
    create_tool_panel,
)


class TestComponents:
    def test_create_message_panel_user(self):
        panel = create_message_panel("user", "Hello")
        assert isinstance(panel, Panel)
        assert "You" in panel.title
    
    def test_create_message_panel_assistant(self):
        panel = create_message_panel("assistant", "Hi there")
        assert isinstance(panel, Panel)
        assert "Agent" in panel.title
    
    def test_create_tool_panel(self):
        panel = create_tool_panel("bash", {"command": "ls"}, "output")
        assert isinstance(panel, Panel)
        assert "bash" in panel.title
    
    def test_create_tool_panel_success(self):
        panel = create_tool_panel("bash", {"command": "ls"}, "file1.py file2.py")
        assert isinstance(panel, Panel)
        assert "✅" in panel.title  # Success icon
    
    def test_create_tool_panel_no_result(self):
        panel = create_tool_panel("file_read", {"path": "test.py"})
        assert isinstance(panel, Panel)
        assert "💭" in panel.title  # Thinking icon
    
    def test_create_plan_panel(self):
        tasks = [
            {"title": "Task 1", "status": "done"},
            {"title": "Task 2", "status": "in_progress"},
        ]
        panel = create_plan_panel(tasks)
        assert isinstance(panel, Panel)
    
    def test_create_header_panel(self):
        panel = create_header_panel("gpt-4", 5, 10)
        assert isinstance(panel, Panel)
        assert "Coding Agent" in str(panel.renderable)


class TestCodingAgentTUI:
    def test_tui_initialization(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI(model_name="test-model", max_steps=20)
        assert tui.model_name == "test-model"
        assert tui.max_steps == 20
        assert tui.current_step == 0
    
    def test_add_user_message(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.add_user_message("Test message")
        assert len(tui.messages) == 1
        assert tui.messages[0]["role"] == "user"
        assert tui.messages[0]["content"] == "Test message"
    
    def test_append_stream(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.append_stream("Hello")
        assert tui.current_stream == "Hello"
        tui.append_stream(" World")
        assert tui.current_stream == "Hello World"
    
    def test_show_tool_call(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.show_tool_call("call_1", "bash", {"command": "ls"})
        assert len(tui.tools) == 1
        assert tui.tools[0]["name"] == "bash"
        assert tui.tools[0]["call_id"] == "call_1"
    
    def test_update_tool_result(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.show_tool_call("call_1", "bash", {"command": "ls"})
        tui.update_tool_result("call_1", "file1.py file2.py")
        assert tui.tools[0]["result"] == "file1.py file2.py"
        assert tui.tools[0]["duration"] is not None
