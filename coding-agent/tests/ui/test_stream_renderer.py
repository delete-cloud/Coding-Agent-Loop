import time

from io import StringIO
from rich.console import Console

from coding_agent.ui.stream_renderer import StreamingRenderer, _has_markdown_syntax


class TestStreamingRenderer:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_user_message(self):
        renderer, _, buf = self._make_renderer()
        renderer.user_message("Hello agent")
        output = buf.getvalue()
        assert "Hello agent" in output

    def test_stream_text_accumulates(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Hello ")
        renderer.stream_text("world")
        assert renderer._stream_buffer == "Hello world"
        renderer.stream_end()

    def test_stream_text_renders_incrementally(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Hello ")
        output_after_first = buf.getvalue()
        assert len(output_after_first) > 0
        renderer.stream_end()

    def test_stream_start_marks_stream_active(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        assert renderer._in_stream is True
        renderer.stream_end()

    def test_stream_end_marks_stream_inactive(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("text")
        renderer.stream_end()
        assert renderer._in_stream is False

    def test_stream_renders_markdown_at_end(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Hello\n\nThis is **bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Hello" in output
        assert "bold" in output

    def test_stream_text_appends_directly_to_terminal(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Hello")
        renderer.stream_text(" world")

        assert "Hello world" in buf.getvalue()

    def test_stream_end_writes_trailing_newline_once(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Hello")
        before = buf.getvalue()

        renderer.stream_end()

        after = buf.getvalue()
        assert after.startswith(before)
        assert after.endswith("\n")

    def test_stream_end_clears_buffer(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Hello world")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Hello world" in output
        assert renderer._stream_buffer == ""

    def test_stream_end_sets_in_stream_false(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("text")
        renderer.stream_end()
        assert renderer._in_stream is False

    def test_tool_call_renders_panel(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "ls"})
        output = buf.getvalue()
        assert "bash" in output
        assert "ls" in output

    def test_tool_result_renders(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "ls"})
        renderer.tool_result("call_1", "bash", "file1.py\nfile2.py", is_error=False)
        output = buf.getvalue()
        assert "file1.py" in output

    def test_tool_result_error_shows_error_status(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "bad"})
        renderer.tool_result(
            "call_1", "bash", "Error: command not found", is_error=True
        )
        output = buf.getvalue()
        assert "Error" in output
        assert "✗" in output

    def test_tool_result_success_shows_check(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "ls"})
        renderer.tool_result("call_1", "bash", "ok", is_error=False)
        output = buf.getvalue()
        assert "✓" in output

    def test_turn_end_completed_no_jargon(self):
        renderer, _, buf = self._make_renderer()
        renderer.turn_end("completed")
        output = buf.getvalue()
        assert "StopReason" not in output

    def test_turn_end_error(self):
        renderer, _, buf = self._make_renderer()
        renderer.turn_end("error")
        output = buf.getvalue()
        assert "error" in output.lower()

    def test_thinking_renders(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking("Let me analyze this...")
        output = buf.getvalue()
        assert "analyze" in output

    def test_stream_with_tool_call_mid_stream_flushes(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Analyzing the code...")
        renderer.tool_call("call_1", "file_read", {"path": "main.py"})
        output = buf.getvalue()
        assert "Analyzing the code" in output
        assert "file_read" in output

    def test_tool_result_truncation(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "cat big.txt"})
        long_result = "x" * 2000
        renderer.tool_result("call_1", "bash", long_result, is_error=False)
        output = buf.getvalue()
        assert "truncated" in output.lower()

    def test_tool_timing_shows_duration(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("call_1", "bash", {"command": "sleep 0"})
        time.sleep(0.02)
        renderer.tool_result("call_1", "bash", "done", is_error=False)
        output = buf.getvalue()
        assert "s)" in output

    def test_tool_icon_bash(self):
        from coding_agent.ui.stream_renderer import _tool_icon

        assert _tool_icon("bash") == "⚡"

    def test_tool_icon_file_read(self):
        from coding_agent.ui.stream_renderer import _tool_icon

        assert _tool_icon("file_read") == "📄"

    def test_tool_icon_grep(self):
        from coding_agent.ui.stream_renderer import _tool_icon

        assert _tool_icon("grep") == "🔍"

    def test_tool_icon_unknown(self):
        from coding_agent.ui.stream_renderer import _tool_icon

        assert _tool_icon("custom_tool") == "🔧"

    def test_default_console_creation(self):
        renderer = StreamingRenderer()
        assert renderer.console is not None

    def test_turn_end_ends_active_stream(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("some text")
        renderer.turn_end("completed")
        assert renderer._in_stream is False
        assert renderer._stream_buffer == ""

    def test_empty_stream_text_does_not_mark_output_started(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("")
        renderer.stream_end()
        assert renderer._stream_started_output is False
        assert buf.getvalue() == ""


class TestLineCount:
    def _make_renderer(
        self, width: int = 80
    ) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=width)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_single_line_no_wrap(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("Hello world") == 1

    def test_multiline(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("line1\nline2\nline3") == 3

    def test_line_wrapping(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("abcdefghijklmno") == 2

    def test_exact_width_no_extra_wrap(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("abcdefghij") == 1

    def test_wide_characters(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("你好世界你") == 1

    def test_wide_characters_wrap(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("你好世界你好") == 2

    def test_empty_string(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("") == 0

    def test_trailing_newline(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("hello\n") == 2

    def test_wrapped_text_with_trailing_newline(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("abcdefghijklmno\n") == 3

    def test_mixed_wrap_and_newlines(self):
        renderer, _, _ = self._make_renderer(width=10)
        assert renderer._count_terminal_lines("abcdefghijklm\nxy") == 3


class TestClearOutput:
    def _make_renderer(
        self, force_terminal: bool = True
    ) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=force_terminal, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_clear_emits_control_sequences(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(3)
        output = buf.getvalue()
        assert "\r" in output
        assert "\x1b[1A" in output
        assert "\x1b[2K" in output

    def test_clear_zero_lines_noop(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(0)
        assert buf.getvalue() == ""

    def test_clear_skipped_when_not_terminal(self):
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer._clear_streamed_output(5)
        assert "\x1b[1A" not in buf.getvalue()

    def test_clear_single_line(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(1)
        output = buf.getvalue()
        assert "\r" in output
        assert "\x1b[2K" in output
        assert "\x1b[1A" not in output


class TestMarkdownDetection:
    def test_code_block(self):
        assert (
            _has_markdown_syntax("here is code:\n```python\nprint('hi')\n```") is True
        )

    def test_heading(self):
        assert _has_markdown_syntax("# Title\nSome text") is True

    def test_bold(self):
        assert _has_markdown_syntax("This is **bold** text") is True

    def test_italic_underscore(self):
        assert _has_markdown_syntax("This is __italic__ text") is True

    def test_link(self):
        assert _has_markdown_syntax("See [docs](https://example.com)") is True

    def test_bullet_list(self):
        assert _has_markdown_syntax("Items:\n* item one\n* item two") is True

    def test_dash_list(self):
        assert _has_markdown_syntax("Items:\n- item one\n- item two") is True

    def test_numbered_list(self):
        assert _has_markdown_syntax("Steps:\n1. first\n2. second") is True

    def test_blockquote(self):
        assert _has_markdown_syntax("> This is a quote") is True

    def test_table(self):
        assert (
            _has_markdown_syntax("| col1 | col2 |\n|------|------|\n| a | b |") is True
        )

    def test_plain_text(self):
        assert _has_markdown_syntax("Hello world, this is plain text.") is False

    def test_plain_with_numbers(self):
        assert _has_markdown_syntax("I have 3 items and 2 tasks.") is False

    def test_empty(self):
        assert _has_markdown_syntax("") is False


class TestHybridStreamEnd:
    def _make_renderer(
        self, *, force_terminal: bool = True, width: int = 80
    ) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=force_terminal, width=width)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_markdown_content_gets_rerendered(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Hello\n\nThis is **bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Hello" in output
        assert "bold" in output

    def test_plain_text_no_rerender(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Just a simple answer.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Just a simple answer." in output
        assert "\x1b[1A" not in output

    def test_non_terminal_no_rerender(self):
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer.stream_start()
        renderer.stream_text("# Heading\n\n**Bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Heading" in output
        assert "\x1b[1A" not in output

    def test_empty_stream_no_crash(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        renderer.stream_end()
        assert renderer._in_stream is False

    def test_code_block_gets_rerendered(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Here is code:\n```python\nprint('hello')\n```\n")
        renderer.stream_end()
        output = buf.getvalue()
        assert "print" in output

    def test_markdown_rerender_emits_clear_sequences(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Hello\n\nThis is **bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "\x1b[2K" in output

    def test_repeated_stream_cycles_reset_cleanly(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# First")
        renderer.stream_end()
        renderer.stream_start()
        renderer.stream_text("Second plain response")
        renderer.stream_end()
        output = buf.getvalue()
        assert "First" in output
        assert "Second plain response" in output
        assert renderer._stream_buffer == ""
        assert renderer._in_stream is False

    def test_state_reset_after_rerender(self):
        renderer, _, _ = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Title\nContent")
        renderer.stream_end()
        assert renderer._in_stream is False
        assert renderer._stream_buffer == ""
        assert renderer._stream_started_output is False


class TestCollapsedGroupRendering:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_collapsed_group_shows_summary(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(
            summary="Searched for 2 patterns, read 3 files",
            duration=1.23,
            has_error=False,
        )
        output = buf.getvalue()
        assert "Searched for 2 patterns" in output
        assert "read 3 files" in output

    def test_collapsed_group_shows_duration_sub_second(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.45, has_error=False)
        assert "0.45s" in buf.getvalue()

    def test_collapsed_group_shows_duration_over_one_second(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=2.3, has_error=False)
        assert "2.3s" in buf.getvalue()

    def test_collapsed_group_error_indicator(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=True)
        assert "\u26a0" in buf.getvalue()

    def test_collapsed_group_success_indicator(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=False)
        assert "\u2713" in buf.getvalue()

    def test_collapsed_group_shows_hint(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(
            summary="Read 1 file", duration=0.1, has_error=False, hint="src/main.py"
        )
        assert "src/main.py" in buf.getvalue()

    def test_collapsed_group_no_hint_omitted(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=False)
        output = buf.getvalue()
        assert "Read 1 file" in output

    def test_collapsed_group_slow_duration_highlighted(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=6.0, has_error=False)
        assert "6.0s" in buf.getvalue()

    def test_collapsed_group_flushes_active_stream(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("thinking...")
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=False)
        assert renderer._in_stream is False


class TestEnhancedBoundaries:
    def _make_renderer(
        self,
        *,
        force_terminal: bool = True,
        enhanced_boundaries: bool = True,
        width: int = 80,
    ) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=force_terminal, width=width)
        renderer = StreamingRenderer(
            console=console, enhanced_boundaries=enhanced_boundaries
        )
        return renderer, console, buf

    def test_user_message_panel_when_enhanced(self):
        renderer, _, buf = self._make_renderer()
        renderer.user_message("Hello agent")
        output = buf.getvalue()
        assert "You" in output
        assert "Hello agent" in output

    def test_user_message_no_panel_when_not_terminal(self):
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer.user_message("Hello agent")
        output = buf.getvalue()
        assert "❯" in output
        assert "Hello agent" in output

    def test_user_message_no_panel_when_disabled(self):
        renderer, _, buf = self._make_renderer(enhanced_boundaries=False)
        renderer.user_message("Hello agent")
        output = buf.getvalue()
        assert "❯" in output
        assert "Hello agent" in output

    def test_assistant_markdown_panel_when_enhanced(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Hello\n\nThis is **bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Agent" in output
        assert "Hello" in output

    def test_assistant_plain_text_no_panel(self):
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Just a simple answer.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Agent" not in output
        assert "Just a simple answer." in output

    def test_assistant_no_panel_when_not_terminal(self):
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer.stream_start()
        renderer.stream_text("# Hello\n\n**Bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Agent" not in output

    def test_assistant_no_panel_when_disabled(self):
        renderer, _, buf = self._make_renderer(enhanced_boundaries=False)
        renderer.stream_start()
        renderer.stream_text("# Hello\n\n**Bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Agent" not in output

    def test_turn_separator_on_completed(self):
        renderer, _, buf = self._make_renderer()
        renderer.turn_end("completed")
        output = buf.getvalue()
        assert "─" in output

    def test_turn_separator_on_error(self):
        renderer, _, buf = self._make_renderer()
        renderer.turn_end("error")
        output = buf.getvalue()
        assert "─" in output
        assert "error" in output.lower()

    def test_turn_separator_on_blocked(self):
        renderer, _, buf = self._make_renderer()
        renderer.turn_end("blocked")
        output = buf.getvalue()
        assert "─" in output

    def test_no_separator_when_not_terminal(self):
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer.turn_end("completed")
        output = buf.getvalue()
        assert "─" not in output

    def test_no_separator_when_disabled(self):
        renderer, _, buf = self._make_renderer(enhanced_boundaries=False)
        renderer.turn_end("completed")
        output = buf.getvalue()
        assert "─" not in output
