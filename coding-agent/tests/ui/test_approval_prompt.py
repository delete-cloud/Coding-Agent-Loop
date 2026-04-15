"""Tests for interactive approval prompt."""

import pytest
from io import StringIO
from rich.console import Console

from coding_agent.ui.approval_prompt import (
    format_tool_preview,
    ApprovalChoice,
)
from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta


class TestFormatToolPreview:
    def test_bash_preview_shows_command(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="bash",
            args={"command": "rm -rf /tmp/test"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        # Syntax highlighting inserts ANSI escapes between tokens,
        # so check each token individually
        assert "rm" in output
        assert "-rf" in output
        assert "/tmp/test" in output
        assert "bash" in output.lower() or "\u26a1" in output

    def test_file_write_preview_shows_content(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="file_write",
            args={"path": "/tmp/test.py", "content": "print('hello')"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "test.py" in output
        assert "print" in output

    def test_file_edit_preview_shows_diff(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="file_edit",
            args={
                "path": "/tmp/test.py",
                "old_text": "foo",
                "new_text": "bar",
            },
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "foo" in output
        assert "bar" in output

    def test_generic_tool_preview(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="custom_tool",
            args={"key": "value"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "custom_tool" in output
        assert "key" in output

    def test_child_tool_preview_shows_agent_context(self):
        req = ApprovalRequest(
            session_id="s1",
            agent_id="child-1",
            request_id="r1",
            tool="bash_run",
            args={"command": "pwd"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "child-1" in output
        assert "bash" in output.lower() or "⚡" in output


class TestApprovalChoice:
    def test_enum_values(self):
        assert ApprovalChoice.APPROVE_ONCE.value == "approve_once"
        assert ApprovalChoice.APPROVE_SESSION.value == "approve_session"
        assert ApprovalChoice.REJECT.value == "reject"
        assert ApprovalChoice.REJECT_WITH_REASON.value == "reject_with_reason"
