import time

from io import StringIO
from rich.console import Console

from rich.text import Text

from coding_agent.ui.stream_renderer import (
    StreamingRenderer,
    _has_markdown_syntax,
    _is_diff_output,
    _render_diff,
    _smart_truncate,
    _strip_ansi,
)
from coding_agent.ui.theme import theme


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
        long_result = "\n".join(f"line {i}" for i in range(80))
        renderer.tool_result("call_1", "bash", long_result, is_error=False)
        output = buf.getvalue()
        assert "lines hidden" in output

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


class TestCompactToolRendering:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_compact_tool_call_renders_single_line_summary(self):
        renderer, _, buf = self._make_renderer()
        renderer.compact_tool_call(
            "c1", "bash_run", {"command": "pytest tests/ui/test_collapse.py -q"}
        )
        output = buf.getvalue()
        assert "⏳" in output
        assert "bash_run" in output
        assert "pytest tests/ui/test_collapse.py -q" in output

    def test_compact_tool_result_for_bash_success_shows_line_count(self):
        renderer, _, buf = self._make_renderer()
        renderer.compact_tool_call("c1", "bash_run", {"command": "ls"})
        renderer.compact_tool_result("c1", "bash_run", "a.py\nb.py", is_error=False)
        output = buf.getvalue()
        assert "✓" in output
        assert "bash_run" in output
        assert "2 lines output" in output

    def test_compact_tool_result_for_file_write_shows_path(self):
        renderer, _, buf = self._make_renderer()
        renderer.compact_tool_call(
            "c1", "file_write", {"path": "src/new_file.py", "content": "print('hi')"}
        )
        renderer.compact_tool_result(
            "c1",
            "file_write",
            '{"success": true, "path": "src/new_file.py"}',
            is_error=False,
        )
        output = buf.getvalue()
        assert "file_write" in output
        assert "src/new_file.py" in output

    def test_compact_tool_result_for_bash_error_shows_error_excerpt(self):
        renderer, _, buf = self._make_renderer()
        renderer.compact_tool_call("c1", "bash_run", {"command": "bad-command"})
        renderer.compact_tool_result(
            "c1",
            "bash_run",
            "command not found\nmore detail\nextra detail",
            is_error=True,
        )
        output = buf.getvalue()
        assert "✗" in output
        assert "command not found" in output


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

    def test_user_message_strips_ansi_sequences_inside_panel(self):
        renderer, _, buf = self._make_renderer()
        renderer.user_message("\x1b[31m[Pasted text #2 +35 lines]\x1b[0m test")
        output = buf.getvalue()

        assert "[Pasted text #2 +35 lines] test" in output
        assert "\x1b[31m" not in output

    def test_user_message_normalizes_carriage_returns_inside_panel(self):
        renderer, _, buf = self._make_renderer(width=60)
        renderer.user_message("before\rafter [Pasted text #2 +35 lines]")
        output = buf.getvalue()

        assert "after [Pasted text #2 +35 lines]" in output
        assert "before\r" not in output

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


class TestIsDiffOutput:
    """Tests for header-gated diff detection."""

    def test_git_diff_header(self):
        text = (
            "diff --git a/foo.py b/foo.py\n"
            "index 1234567..abcdefg 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added\n"
        )
        assert _is_diff_output(text) is True

    def test_unified_diff_header(self):
        text = "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n line1\n+added\n"
        assert _is_diff_output(text) is True

    def test_plus_minus_without_diff_header_no_match(self):
        text = "+something\n-something else\n"
        assert _is_diff_output(text) is False

    def test_empty_string(self):
        assert _is_diff_output("") is False

    def test_regular_command_output_no_match(self):
        text = "total 16\ndrwxr-xr-x  5 user staff 160 Jan  1 00:00 .\n"
        assert _is_diff_output(text) is False

    def test_diff_stat_output_no_match(self):
        text = " foo.py | 3 ++-\n 1 file changed, 2 insertions(+), 1 deletion(-)\n"
        assert _is_diff_output(text) is False

    def test_git_diff_preceded_by_non_diff_text_still_matches(self):
        text = (
            "Here is the diff output:\n"
            "diff --git a/bar.py b/bar.py\n"
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
        )
        assert _is_diff_output(text) is True

    def test_python_traceback_math_plus_minus_no_match(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "test.py", line 5, in <module>\n'
            "    result = 3 + 4 - 1\n"
            "ValueError: invalid literal\n"
        )
        assert _is_diff_output(text) is False


class TestSmartTruncate:
    """Tests for line-based smart truncation."""

    def test_short_text_not_truncated(self):
        text = "line1\nline2\nline3"
        result, was_truncated = _smart_truncate(text, max_lines=50)
        assert result == text
        assert was_truncated is False

    def test_exact_max_lines_not_truncated(self):
        lines = [f"line{i}" for i in range(50)]
        text = "\n".join(lines)
        result, was_truncated = _smart_truncate(text, max_lines=50)
        assert result == text
        assert was_truncated is False

    def test_over_max_lines_truncates(self):
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        result, was_truncated = _smart_truncate(text, max_lines=50, tail_lines=5)
        assert was_truncated is True
        assert len(result.split("\n")) < 100

    def test_truncation_indicator_appears(self):
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        result, _ = _smart_truncate(text, max_lines=50, tail_lines=5)
        assert "lines hidden" in result

    def test_hidden_count_is_correct(self):
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        # max_lines=50, tail_lines=5 → head = 50-5 = 45, hidden = 100-50 = 50
        result, _ = _smart_truncate(text, max_lines=50, tail_lines=5)
        assert "50 lines hidden" in result

    def test_empty_string(self):
        result, was_truncated = _smart_truncate("")
        assert result == ""
        assert was_truncated is False

    def test_single_line(self):
        result, was_truncated = _smart_truncate("hello")
        assert result == "hello"
        assert was_truncated is False

    def test_tail_lines_preserved(self):
        lines = [f"line{i}" for i in range(100)]
        text = "\n".join(lines)
        result, _ = _smart_truncate(text, max_lines=50, tail_lines=5)
        # Last 5 lines of original should be in result
        for i in range(95, 100):
            assert f"line{i}" in result


class TestRenderDiff:
    _SAMPLE_DIFF = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1234567..abcdefg 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " unchanged\n"
        "+added\n"
        "-removed\n"
    )

    def test_returns_text_object(self):
        result = _render_diff(self._SAMPLE_DIFF)
        assert isinstance(result, Text)

    def test_added_line_present_and_styled(self):
        result = _render_diff(self._SAMPLE_DIFF)
        plain = result.plain
        assert "+added" in plain
        spans = result._spans
        add_spans = [s for s in spans if s.style == theme.Colors.DIFF_ADD]
        assert len(add_spans) > 0

    def test_deleted_line_styled(self):
        result = _render_diff(self._SAMPLE_DIFF)
        spans = result._spans
        del_spans = [s for s in spans if s.style == theme.Colors.DIFF_DELETE]
        assert len(del_spans) > 0

    def test_hunk_header_styled(self):
        result = _render_diff(self._SAMPLE_DIFF)
        spans = result._spans
        hunk_spans = [s for s in spans if s.style == theme.Colors.DIFF_HUNK]
        assert len(hunk_spans) > 0

    def test_meta_lines_styled(self):
        result = _render_diff(self._SAMPLE_DIFF)
        spans = result._spans
        meta_spans = [s for s in spans if s.style == theme.Colors.DIFF_META]
        # diff --git, index, ---, +++ → at least 4 meta lines
        assert len(meta_spans) >= 4

    def test_context_line_unstyled(self):
        text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n unchanged\n"
        result = _render_diff(text)
        plain_lines = result.plain.split("\n")
        context_idx = next(
            i for i, l in enumerate(plain_lines) if l.strip() == "unchanged"
        )
        line_start = sum(len(plain_lines[j]) + 1 for j in range(context_idx))
        line_end = line_start + len(plain_lines[context_idx])
        styled_ranges = [(s.start, s.end) for s in result._spans]
        for s_start, s_end in styled_ranges:
            assert not (s_start < line_end and s_end > line_start), (
                "Context line should not be styled"
            )


class TestToolResultDiffIntegration:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    _GIT_DIFF = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1234567..abcdefg 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,4 @@\n"
        " context\n"
        "+new line\n"
        "-old line\n"
    )

    def test_diff_output_renders_with_panel_title(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff"})
        renderer.tool_result("c1", "bash", self._GIT_DIFF, is_error=False)
        output = buf.getvalue()
        assert "bash" in output
        assert "✓" in output

    def test_diff_output_contains_diff_lines(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff"})
        renderer.tool_result("c1", "bash", self._GIT_DIFF, is_error=False)
        output = buf.getvalue()
        assert "+new line" in output
        assert "-old line" in output

    def test_non_diff_output_unchanged(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "ls"})
        plain_result = "file1.py\nfile2.py"
        renderer.tool_result("c1", "bash", plain_result, is_error=False)
        output = buf.getvalue()
        assert "file1.py" in output
        assert "file2.py" in output
        assert "✓" in output


class TestToolResultSmartTruncateIntegration:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_large_nondiff_shows_lines_hidden(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "cat big.txt"})
        big_result = "\n".join(f"line {i}" for i in range(80))
        renderer.tool_result("c1", "bash", big_result, is_error=False)
        output = buf.getvalue()
        assert "lines hidden" in output

    def test_large_nondiff_preserves_head_and_tail(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "cat big.txt"})
        big_result = "\n".join(f"LINE-{i:03d}" for i in range(80))
        renderer.tool_result("c1", "bash", big_result, is_error=False)
        output = buf.getvalue()
        assert "LINE-000" in output
        assert "LINE-079" in output
        assert "LINE-050" not in output

    def test_large_diff_output_truncated_with_indicator(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff"})
        diff_header = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,80 +1,80 @@\n"
        diff_body = "\n".join(f"+added line {i}" for i in range(80))
        big_diff = diff_header + diff_body
        renderer.tool_result("c1", "bash", big_diff, is_error=False)
        output = buf.getvalue()
        assert "lines hidden" in output

    def test_no_chars_truncated_wording(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "cat big.txt"})
        big_result = "\n".join(f"line {i}" for i in range(80))
        renderer.tool_result("c1", "bash", big_result, is_error=False)
        output = buf.getvalue()
        assert "chars truncated" not in output

    def test_short_output_not_truncated(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "ls"})
        short_result = "\n".join(f"file_{i}.py" for i in range(10))
        renderer.tool_result("c1", "bash", short_result, is_error=False)
        output = buf.getvalue()
        assert "lines hidden" not in output
        assert "truncated" not in output.lower()


class TestStripAnsi:
    def test_removes_color_from_diff_header(self):
        colored = "\x1b[1mdiff --git a/foo.py b/foo.py\x1b[0m"
        assert _strip_ansi(colored) == "diff --git a/foo.py b/foo.py"

    def test_removes_sgr_sequences(self):
        colored = "\x1b[32m+added line\x1b[0m"
        assert _strip_ansi(colored) == "+added line"

    def test_removes_multiple_sequences(self):
        colored = "\x1b[1;31m--- a/foo.py\x1b[0m\n\x1b[1;32m+++ b/foo.py\x1b[0m"
        assert _strip_ansi(colored) == "--- a/foo.py\n+++ b/foo.py"

    def test_passthrough_plain_text(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_removes_256_color_codes(self):
        colored = "\x1b[38;5;196mred text\x1b[0m"
        assert _strip_ansi(colored) == "red text"

    def test_removes_rgb_color_codes(self):
        colored = "\x1b[38;2;255;0;0mred text\x1b[0m"
        assert _strip_ansi(colored) == "red text"


class TestAnsiDiffDetectionIntegration:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    _ANSI_DIFF = (
        "\x1b[1mdiff --git a/foo.py b/foo.py\x1b[0m\n"
        "index 1234567..abcdefg 100644\n"
        "\x1b[1;31m--- a/foo.py\x1b[0m\n"
        "\x1b[1;32m+++ b/foo.py\x1b[0m\n"
        "\x1b[36m@@ -1,3 +1,4 @@\x1b[0m\n"
        " context\n"
        "\x1b[32m+new line\x1b[0m\n"
        "\x1b[31m-old line\x1b[0m\n"
    )

    def test_ansi_diff_detected_by_tool_result(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff --color"})
        renderer.tool_result("c1", "bash", self._ANSI_DIFF, is_error=False)
        output = buf.getvalue()
        assert "+new line" in output
        assert "-old line" in output

    def test_ansi_diff_no_raw_escape_codes_in_output(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff --color"})
        renderer.tool_result("c1", "bash", self._ANSI_DIFF, is_error=False)
        output = buf.getvalue()
        assert "\x1b[1;31m--- a/foo.py\x1b[0m" not in output
        assert "\x1b[1;32m+++ b/foo.py\x1b[0m" not in output

    def test_is_diff_output_false_on_raw_ansi_header(self):
        assert _is_diff_output(self._ANSI_DIFF) is False


class TestBinaryDiffRendering:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    _BINARY_DIFF = (
        "diff --git a/icon.png b/icon.png\n"
        "index abc1234..def5678 100644\n"
        "Binary files a/icon.png and b/icon.png differ\n"
    )

    def test_binary_diff_renders_without_crash(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff"})
        renderer.tool_result("c1", "bash", self._BINARY_DIFF, is_error=False)
        output = buf.getvalue()
        assert "Binary files" in output
        assert "icon.png" in output

    def test_binary_diff_shows_panel_status(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git diff"})
        renderer.tool_result("c1", "bash", self._BINARY_DIFF, is_error=False)
        output = buf.getvalue()
        assert "✓" in output


class TestEmptyToolResult:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_empty_result_no_crash(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "true"})
        renderer.tool_result("c1", "bash", "", is_error=False)
        output = buf.getvalue()
        assert "✓" in output

    def test_empty_result_no_truncation_wording(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "true"})
        renderer.tool_result("c1", "bash", "", is_error=False)
        output = buf.getvalue()
        assert "lines hidden" not in output
        assert "truncated" not in output.lower()


class TestMixedOutputDiffDetection:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    _MIXED_ANSI = (
        "Applying patch...\n"
        "\x1b[1mdiff --git a/bar.py b/bar.py\x1b[0m\n"
        "\x1b[1;31m--- a/bar.py\x1b[0m\n"
        "\x1b[1;32m+++ b/bar.py\x1b[0m\n"
        "\x1b[36m@@ -1,2 +1,3 @@\x1b[0m\n"
        " existing\n"
        "\x1b[32m+new\x1b[0m\n"
    )

    def test_mixed_ansi_diff_detected(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git apply --stat"})
        renderer.tool_result("c1", "bash", self._MIXED_ANSI, is_error=False)
        output = buf.getvalue()
        assert "+new" in output

    def test_mixed_output_preserves_prelude(self):
        renderer, _, buf = self._make_renderer()
        renderer.tool_call("c1", "bash", {"command": "git apply --stat"})
        renderer.tool_result("c1", "bash", self._MIXED_ANSI, is_error=False)
        output = buf.getvalue()
        assert "Applying patch" in output


class TestThinkingSpinner:
    def _make_renderer(
        self, *, terminal: bool = True
    ) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=terminal, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_thinking_start_outputs_spinner(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking_start()
        output = buf.getvalue()
        assert "Thinking" in output
        assert renderer._thinking_active is True

    def test_thinking_update_shows_elapsed(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking_start()
        renderer.thinking_update(elapsed_seconds=5.0)
        output = buf.getvalue()
        assert "5s" in output

    def test_thinking_update_cycles_spinner_characters(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking_start()
        initial_idx = renderer._spinner_idx
        renderer.thinking_update(elapsed_seconds=1.0)
        assert renderer._spinner_idx != initial_idx

    def test_thinking_end_clears_spinner(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking_start()
        renderer.thinking_end()
        assert renderer._thinking_active is False

    def test_thinking_end_noop_when_not_active(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking_end()
        assert renderer._thinking_active is False

    def test_spinner_cycles_through_all_characters(self):
        renderer, _, _ = self._make_renderer()
        from coding_agent.ui.stream_renderer import _SPINNER

        renderer.thinking_start()
        seen = {_SPINNER[renderer._spinner_idx]}
        for i in range(len(_SPINNER)):
            renderer.thinking_update(elapsed_seconds=float(i))
            seen.add(_SPINNER[renderer._spinner_idx])
        assert len(seen) == len(_SPINNER)

    def test_nontty_thinking_start_suppressed(self):
        renderer, _, buf = self._make_renderer(terminal=False)
        renderer.thinking_start()
        output = buf.getvalue()
        assert "\x1b[" not in output
        assert renderer._thinking_active is True

    def test_nontty_thinking_update_no_ansi(self):
        renderer, _, buf = self._make_renderer(terminal=False)
        renderer.thinking_start()
        buf.truncate(0)
        buf.seek(0)
        renderer.thinking_update(elapsed_seconds=3.0)
        output = buf.getvalue()
        assert "\x1b[" not in output

    def test_old_thinking_method_still_works(self):
        renderer, _, buf = self._make_renderer()
        renderer.thinking("some thought")
        output = buf.getvalue()
        assert "some thought" in output

    def test_update_status_renders_token_info(self):
        renderer, _, buf = self._make_renderer()
        renderer.update_status(tokens_in=500, tokens_out=200, elapsed_seconds=12.3)
        output = buf.getvalue()
        assert "500" in output
        assert "200" in output
