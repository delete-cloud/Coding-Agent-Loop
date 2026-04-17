"""Tests for InputHandler multiline support."""

from collections.abc import Callable
from typing import Any, cast
from types import SimpleNamespace

import pytest
from coding_agent.cli.input_handler import InputHandler, _SHIFT_ENTER_SEQUENCE
from prompt_toolkit.completion.base import CompleteEvent
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding.key_processor import KeyPressEvent

# Sentinel constants — mirror production values (permanent test fixtures).
# These will match the module-level constants added in Task 2.
SWITCH_TO_SHELL = "__SWITCH_TO_SHELL__"
SWITCH_TO_CHAT = "__SWITCH_TO_CHAT__"


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


class DummyBuffer:
    def __init__(self, text: str = "", cursor_position: int | None = None):
        self.text = text
        self.cursor_position = (
            cursor_position if cursor_position is not None else len(text)
        )
        self.insert_text_calls: list[str] = []
        self.delete_before_cursor_calls: list[int] = []
        self.delete_calls: list[int] = []
        self.validate_and_handle_called = False
        self.reset_called = False

    def insert_text(self, text: str) -> None:
        self.insert_text_calls.append(text)
        before = self.text[: self.cursor_position]
        after = self.text[self.cursor_position :]
        self.text = before + text + after
        self.cursor_position += len(text)

    def delete_before_cursor(self, count: int = 1) -> None:
        self.delete_before_cursor_calls.append(count)
        start = max(0, self.cursor_position - count)
        self.text = self.text[:start] + self.text[self.cursor_position :]
        self.cursor_position = start

    def delete(self, count: int = 1) -> None:
        self.delete_calls.append(count)
        self.text = (
            self.text[: self.cursor_position]
            + self.text[self.cursor_position + count :]
        )

    def validate_and_handle(self) -> None:
        self.validate_and_handle_called = True

    def reset(self) -> None:
        self.reset_called = True

    @property
    def document(self):
        return SimpleNamespace(cursor_position=self.cursor_position, text=self.text)


class DummyApp:
    def __init__(self, buffer: DummyBuffer | None = None):
        self.current_buffer = buffer or DummyBuffer()
        self.exit_called = False
        self.exit_result = None

    def exit(self, result=None) -> None:
        self.exit_called = True
        self.exit_result = result


def _make_event(app: DummyApp) -> KeyPressEvent:
    return cast(KeyPressEvent, cast(object, SimpleNamespace(app=app)))


_PT_KEY_ALIASES: dict[str, str] = {
    "backspace": "c-h",
    "delete": "delete",
    "enter": "c-m",
    "left": "left",
    "right": "right",
}


def _normalize_key(key_str: str) -> str:
    return _PT_KEY_ALIASES.get(key_str, key_str)


def _get_key_binding_for_key(handler: InputHandler, key_str: str):
    """Find first binding whose keys tuple contains the given key string.

    Handles prompt_toolkit key aliases (e.g. 'backspace' -> 'c-h').
    """
    normalized = _normalize_key(key_str)
    matches = [b for b in handler.bindings.bindings if normalized in b.keys]
    if not matches:
        raise KeyError(
            f"No binding registered for key: {key_str!r} (normalized: {normalized!r})"
        )
    return matches[0]


def _get_key_binding_for_keys(handler: InputHandler, *keys: str):
    normalized = tuple(_normalize_key(key) for key in keys)
    matches = [b for b in handler.bindings.bindings if b.keys == normalized]
    if not matches:
        raise KeyError(f"No binding registered for keys: {keys!r}")
    return matches[0]


def _get_active_key_binding_for_keys(handler: InputHandler, *keys: str):
    normalized = tuple(_normalize_key(key) for key in keys)
    matches = [b for b in handler.bindings.bindings if b.keys == normalized]
    if not matches:
        raise KeyError(f"No binding registered for keys: {keys!r}")
    for binding in matches:
        filter_fn = getattr(binding, "filter", None)
        if filter_fn is None or filter_fn():
            return binding
    raise KeyError(f"No active binding registered for keys: {keys!r}")


def _get_active_any_key_binding(handler: InputHandler):
    matches = [b for b in handler.bindings.bindings if b.keys == (Keys.Any,)]
    if not matches:
        raise KeyError("No any-key binding registered")
    for binding in matches:
        filter_fn = getattr(binding, "filter", None)
        if filter_fn is None or filter_fn():
            return binding
    raise KeyError("No active any-key binding registered")


# ---------------------------------------------------------------------------
# Original tests (unchanged)
# ---------------------------------------------------------------------------


class TestInputHandlerMultiline:
    def test_handler_creation(self):
        handler = InputHandler()
        assert handler.chat_session is not None
        assert handler.shell_session is not None

    def test_handler_uses_separate_histories_for_chat_and_shell(self):
        handler = InputHandler()

        assert handler.chat_history is not handler.shell_history
        assert isinstance(handler.chat_history, InMemoryHistory)
        assert isinstance(handler.shell_history, InMemoryHistory)

    def test_handler_uses_separate_sessions_for_chat_and_shell(self):
        handler = InputHandler()

        assert handler.chat_session is not handler.shell_session
        assert handler.chat_session.history is handler.chat_history
        assert handler.shell_session.history is handler.shell_history

    def test_handler_has_multiline_bindings(self):
        handler = InputHandler()
        assert handler.multiline is True

    def test_slash_command_completer(self):
        from coding_agent.cli.input_handler import SlashCommandCompleter
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter()
        doc = Document("/hel")
        completions = list(completer.get_completions(doc, CompleteEvent()))
        labels = [c.text for c in completions]
        assert any("/help" in label for label in labels) or len(completions) >= 0

    def test_bang_completer(self):
        from coding_agent.cli.input_handler import SlashCommandCompleter
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter()
        doc = Document("!g")
        completions = list(completer.get_completions(doc, CompleteEvent()))
        assert isinstance(completions, list)

    def test_shell_prompt_is_visibly_different(self):
        handler = InputHandler()

        prompt = handler.build_prompt(turn_count=3, shell_mode=True, cwd="/tmp/demo")
        fragments = to_formatted_text(prompt)
        rendered = "".join(text for _, text, *_ in fragments)

        assert "bash" in rendered.lower()
        assert "$" in rendered
        assert "demo" in rendered

    def test_normal_prompt_keeps_turn_indicator(self):
        handler = InputHandler()

        prompt = handler.build_prompt(turn_count=7, shell_mode=False)
        fragments = to_formatted_text(prompt)
        rendered = "".join(text for _, text, *_ in fragments)

        assert "[7]" in rendered
        assert ">" in rendered


class TestTUISeparators:
    def test_build_prompt_contains_separator_line(self):
        handler = InputHandler()

        prompt = handler.build_prompt(turn_count=0)
        fragments = to_formatted_text(prompt)
        rendered = "".join(text for _, text, *_ in fragments)

        assert "─" in rendered

    def test_build_prompt_shell_mode_contains_separator(self):
        handler = InputHandler()

        prompt = handler.build_prompt(turn_count=0, shell_mode=True, cwd="/tmp")
        fragments = to_formatted_text(prompt)
        rendered = "".join(text for _, text, *_ in fragments)

        assert "─" in rendered

    def test_chat_session_has_bottom_toolbar(self):
        handler = InputHandler()

        assert handler.chat_session.bottom_toolbar is not None
        assert callable(handler.chat_session.bottom_toolbar)

    def test_bottom_toolbar_is_empty_without_slash_input(self):
        handler = InputHandler()

        rendered = "".join(
            text
            for _, text, *_ in to_formatted_text(handler._build_slash_toolbar("hello"))
        )

        assert rendered == ""

    def test_bottom_toolbar_shows_available_commands_for_slash(self):
        handler = InputHandler()

        rendered = "".join(
            text for _, text, *_ in to_formatted_text(handler._build_slash_toolbar("/"))
        )

        assert "/help" in rendered
        assert "/clear" in rendered
        assert "available commands" in rendered.lower()

    def test_bottom_toolbar_filters_commands_by_prefix(self):
        handler = InputHandler()

        rendered = "".join(
            text
            for _, text, *_ in to_formatted_text(handler._build_slash_toolbar("/mo"))
        )

        assert "/model" in rendered
        assert "/help" not in rendered

    def test_bottom_toolbar_shows_descriptions(self):
        handler = InputHandler()
        fragments = to_formatted_text(handler._build_slash_toolbar("/"))
        rendered = "".join(text for _, text, *_ in fragments)

        assert "Show available commands" in rendered

    def test_bottom_toolbar_highlights_best_match(self):
        handler = InputHandler()
        fragments = to_formatted_text(handler._build_slash_toolbar("/mo"))

        best_matches = [
            text
            for style, text, *_ in fragments
            if style == "class:toolbar.key.bestmatch"
        ]

        assert best_matches == ["/model"]

    def test_bottom_toolbar_uses_sorted_command_order(self):
        handler = InputHandler()
        fragments = to_formatted_text(handler._build_slash_toolbar("/"))

        commands = [
            text
            for style, text, *_ in fragments
            if style in {"class:toolbar.key", "class:toolbar.key.bestmatch"}
            and text.startswith("/")
        ]

        assert commands == ["/checkpoint", "/clear", "/exit", "/help", "/mcp"]

    def test_bottom_toolbar_limits_to_5_items(self):
        handler = InputHandler()
        fragments = to_formatted_text(handler._build_slash_toolbar("/"))

        keys = [
            text
            for style, text, *_ in fragments
            if style == "class:toolbar.key" or style == "class:toolbar.key.bestmatch"
        ]

        commands = [k for k in keys if k.startswith("/")]
        assert len(commands) == 5

    def test_bottom_toolbar_falls_back_to_sorted_commands_when_no_prefix_matches(self):
        handler = InputHandler()
        fragments = to_formatted_text(handler._build_slash_toolbar("/zzz"))

        commands = [
            text
            for style, text, *_ in fragments
            if style in {"class:toolbar.key", "class:toolbar.key.bestmatch"}
            and text.startswith("/")
        ]

        assert commands == ["/checkpoint", "/clear", "/exit", "/help", "/mcp"]
        assert all(
            style != "class:toolbar.key.bestmatch"
            for style, text, *_ in fragments
            if text.startswith("/")
        )

    def test_bottom_toolbar_truncates_long_descriptions(self, monkeypatch):
        handler = InputHandler()

        monkeypatch.setattr(
            "coding_agent.cli.input_handler.get_commands_with_descriptions",
            lambda: [("/demo", "x" * 80)],
        )

        rendered = "".join(
            text for _, text, *_ in to_formatted_text(handler._build_slash_toolbar("/"))
        )

        assert "x" * 80 not in rendered
        assert "x" * 24 in rendered
        assert "…" in rendered

    def test_bottom_toolbar_shows_status_when_not_in_slash_mode(self):
        handler = InputHandler()
        handler.set_status_text("gpt-4o | 12% | 321↑ 123↓ | 9s")

        rendered = "".join(
            text for _, text, *_ in to_formatted_text(handler._build_bottom_toolbar())
        )

        assert "gpt-4o" in rendered
        assert "321↑ 123↓" in rendered

    def test_slash_toolbar_still_wins_over_status_text(self):
        handler = InputHandler()
        handler.set_status_text("gpt-4o | 12% | 321↑ 123↓ | 9s")

        rendered = "".join(
            text for _, text, *_ in to_formatted_text(handler._build_slash_toolbar("/"))
        )

        assert "Available commands" in rendered
        assert "321↑ 123↓" not in rendered

    def test_prompt_style_has_separator_class(self):
        from coding_agent.cli.input_handler import PROMPT_STYLE

        style_rules = dict(PROMPT_STYLE.style_rules)

        assert "separator" in style_rules


# ---------------------------------------------------------------------------
# Task 1 — RED phase: keystroke-level binding tests
# ---------------------------------------------------------------------------


class TestKeystrokeBashToggle:
    """Tests for !, Escape, and Backspace key bindings (RED — all should FAIL/ERROR)."""

    def test_bang_on_empty_buffer_exits_with_shell_sentinel(self):
        """! on empty buffer must call app.exit(result=SWITCH_TO_SHELL)."""
        handler = InputHandler()
        binding = _get_key_binding_for_key(handler, "!")
        buf = DummyBuffer(text="", cursor_position=0)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is True
        assert app.exit_result == SWITCH_TO_SHELL

    def test_bang_on_nonempty_buffer_inserts_bang(self):
        """! on non-empty buffer must insert '!' normally (not switch mode)."""
        handler = InputHandler()
        binding = _get_key_binding_for_key(handler, "!")
        buf = DummyBuffer(text="hello", cursor_position=5)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert "!" in buf.insert_text_calls

    def test_bang_in_shell_mode_inserts_bang(self):
        """! while already in shell mode must NOT switch — just insert '!'."""
        handler = InputHandler()
        handler._shell_mode = True
        binding = _get_key_binding_for_key(handler, "!")
        # The Condition filter should return False in shell mode, preventing the binding from firing
        assert not binding.filter()

    def test_escape_in_shell_mode_empty_buffer_exits_with_chat_sentinel(self):
        """Escape in shell mode on empty buffer must call app.exit(result=SWITCH_TO_CHAT)."""
        handler = InputHandler()
        handler._shell_mode = True
        binding = _get_key_binding_for_keys(handler, "escape")
        buf = DummyBuffer(text="", cursor_position=0)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is True
        assert app.exit_result == SWITCH_TO_CHAT

    def test_escape_in_shell_mode_nonempty_buffer_no_exit(self):
        """Escape in shell mode with text in buffer must NOT exit (let default handle)."""
        handler = InputHandler()
        handler._shell_mode = True
        binding = _get_key_binding_for_keys(handler, "escape")
        buf = DummyBuffer(text="ls", cursor_position=2)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False

    def test_escape_in_chat_mode_not_active(self):
        """Escape binding's Condition filter must be False in chat mode."""
        handler = InputHandler()
        handler._shell_mode = False
        escape_bindings = [
            b for b in handler.bindings.bindings if b.keys == ("escape",)
        ]
        # If an escape binding exists, its filter must evaluate to False when not in shell mode
        for b in escape_bindings:
            assert not b.filter(), (
                "Escape binding should be inactive (filter=False) in chat mode"
            )

    def test_backspace_in_shell_mode_empty_buffer_exits_to_chat(self):
        """Backspace in shell mode on empty buffer must call app.exit(result=SWITCH_TO_CHAT)."""
        handler = InputHandler()
        handler._shell_mode = True
        binding = _get_key_binding_for_key(handler, "backspace")
        buf = DummyBuffer(text="", cursor_position=0)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is True
        assert app.exit_result == SWITCH_TO_CHAT

    def test_backspace_in_shell_mode_nonempty_buffer_deletes_char(self):
        """Backspace in shell mode with text must delete one char (normal behavior)."""
        handler = InputHandler()
        handler._shell_mode = True
        binding = _get_key_binding_for_key(handler, "backspace")
        buf = DummyBuffer(text="ls", cursor_position=2)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert 1 in buf.delete_before_cursor_calls

    def test_escape_in_chat_mode_deletes_entire_paste_placeholder(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        handler._chat_buffer_has_paste_placeholder = lambda: True
        binding = _get_active_key_binding_for_keys(handler, "escape")
        buf = DummyBuffer(text=placeholder, cursor_position=len(placeholder))
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert buf.text == ""
        assert buf.delete_before_cursor_calls == [len(placeholder)]

    def test_backspace_in_chat_mode_deletes_entire_paste_placeholder(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "backspace")
        buf = DummyBuffer(text=placeholder, cursor_position=len(placeholder))
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert buf.text == ""
        assert buf.delete_before_cursor_calls == [len(placeholder)]

    def test_backspace_in_chat_mode_deletes_single_character_outside_placeholder(self):
        handler = InputHandler()
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "backspace")
        buf = DummyBuffer(text="hello", cursor_position=5)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert buf.text == "hell"
        assert buf.delete_before_cursor_calls == [1]

    def test_delete_in_chat_mode_deletes_entire_paste_placeholder(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "delete")
        buf = DummyBuffer(text=placeholder, cursor_position=0)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert app.exit_called is False
        assert buf.text == ""
        assert buf.delete_calls == [len(placeholder)]

    def test_delete_inside_placeholder_preserves_surrounding_text(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "delete")
        buf = DummyBuffer(text=f"hi {placeholder} ok", cursor_position=8)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert buf.text == "hi  ok"

    def test_left_arrow_skips_to_placeholder_start(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "left")
        buf = DummyBuffer(text=f"hi {placeholder} ok", cursor_position=8)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert buf.cursor_position == 3

    def test_right_arrow_skips_to_placeholder_end(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_key_binding_for_keys(handler, "right")
        start = 3
        buf = DummyBuffer(text=f"hi {placeholder} ok", cursor_position=start + 1)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert buf.cursor_position == start + len(placeholder)

    def test_printable_text_inserts_after_placeholder_instead_of_splitting_it(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"
        handler._shell_mode = False
        binding = _get_active_any_key_binding(handler)
        buf = DummyBuffer(text=f"hi {placeholder} ok", cursor_position=8)
        app = DummyApp(buffer=buf)
        event = cast(
            KeyPressEvent,
            cast(object, SimpleNamespace(app=app, data="x")),
        )

        binding.handler(event)

        assert buf.text == f"hi {placeholder}x ok"

    def test_placeholder_toolbar_render_uses_distinct_style(self):
        handler = InputHandler()
        placeholder = "[Pasted text #2 +35 lines]"

        fragments = to_formatted_text(
            handler._highlight_paste_placeholders(placeholder)
        )

        assert any(
            style == "class:paste.placeholder" and text == placeholder
            for style, text, *_ in fragments
        )

    def test_chat_session_uses_input_processors_for_placeholder_chip_rendering(self):
        handler = InputHandler()

        processors = getattr(handler.chat_session, "input_processors", None)

        assert processors is not None
        assert processors


class TestEnterBindings:
    def test_enter_submits_nonempty_chat_input(self):
        handler = InputHandler()
        binding = _get_key_binding_for_keys(handler, "enter")
        buf = DummyBuffer(text="hello", cursor_position=5)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert buf.validate_and_handle_called is True
        assert buf.insert_text_calls == []

    def test_shift_enter_escape_sequence_is_registered(self):
        assert ANSI_SEQUENCES["\x1b[27;2;13~"] == _SHIFT_ENTER_SEQUENCE
        assert ANSI_SEQUENCES["\x1b[13;2u"] == _SHIFT_ENTER_SEQUENCE

    def test_shift_enter_inserts_newline(self):
        handler = InputHandler()
        binding = _get_key_binding_for_keys(handler, "escape", "c-j")
        buf = DummyBuffer(text="hello", cursor_position=5)
        app = DummyApp(buffer=buf)
        event = _make_event(app)

        binding.handler(event)

        assert buf.insert_text_calls == ["\n"]
        assert buf.validate_and_handle_called is False


# ---------------------------------------------------------------------------
# Task 1 — RED phase: sentinel loop tests
# ---------------------------------------------------------------------------


class TestGetInputSentinelLoop:
    """Tests for the internal sentinel loop inside get_input() (RED — should FAIL)."""

    @pytest.mark.asyncio
    async def test_get_input_switches_to_shell_on_sentinel(self, monkeypatch):
        """chat prompt returns SWITCH_TO_SHELL → loop continues on shell session → returns real input."""
        handler = InputHandler()
        chat_calls = iter([SWITCH_TO_SHELL])
        shell_calls = iter(["ls -la"])

        async def mock_chat_prompt(*args, **kwargs):
            return next(chat_calls)

        async def mock_shell_prompt(*args, **kwargs):
            return next(shell_calls)

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", mock_shell_prompt)

        result = await handler.get_input()

        assert result == "ls -la"

    @pytest.mark.asyncio
    async def test_get_input_switches_back_to_chat_on_sentinel(self, monkeypatch):
        """chat→SWITCH_TO_SHELL, shell→SWITCH_TO_CHAT, chat→'hello' → returns 'hello'."""
        handler = InputHandler()
        chat_calls = iter([SWITCH_TO_SHELL, "hello"])
        shell_calls = iter([SWITCH_TO_CHAT])

        async def mock_chat_prompt(*args, **kwargs):
            return next(chat_calls)

        async def mock_shell_prompt(*args, **kwargs):
            return next(shell_calls)

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", mock_shell_prompt)

        result = await handler.get_input()

        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_input_never_returns_sentinels(self, monkeypatch):
        """get_input() must never return a sentinel string as its final value."""
        handler = InputHandler()
        chat_calls = iter([SWITCH_TO_SHELL, "real input"])
        shell_calls = iter([SWITCH_TO_CHAT])

        async def mock_chat_prompt(*args, **kwargs):
            return next(chat_calls)

        async def mock_shell_prompt(*args, **kwargs):
            return next(shell_calls)

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", mock_shell_prompt)

        result = await handler.get_input()

        assert result == "real input"
        assert result != SWITCH_TO_SHELL
        assert result != SWITCH_TO_CHAT

    @pytest.mark.asyncio
    async def test_shell_mode_property_exposed(self, monkeypatch):
        """handler.shell_mode property must be True after switching to shell."""
        handler = InputHandler()
        chat_calls = iter([SWITCH_TO_SHELL])
        shell_calls = iter(["cmd"])

        async def mock_chat_prompt(*args, **kwargs):
            return next(chat_calls)

        async def mock_shell_prompt(*args, **kwargs):
            return next(shell_calls)

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", mock_shell_prompt)

        await handler.get_input()

        # After get_input returns while in shell mode, shell_mode property must be True
        assert handler.shell_mode is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_rapid_toggle_bang_then_escape(self, monkeypatch):
        """Rapid toggle: chat→SWITCH_TO_SHELL, shell→SWITCH_TO_CHAT, chat→'hi' → returns 'hi'."""
        handler = InputHandler()
        chat_calls = iter([SWITCH_TO_SHELL, "hi"])
        shell_calls = iter([SWITCH_TO_CHAT])

        async def mock_chat_prompt(*args, **kwargs):
            return next(chat_calls)

        async def mock_shell_prompt(*args, **kwargs):
            return next(shell_calls)

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", mock_shell_prompt)

        result = await handler.get_input()

        assert result == "hi"
