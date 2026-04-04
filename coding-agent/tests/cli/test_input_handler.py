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
        self.validate_and_handle_called = False
        self.reset_called = False

    def insert_text(self, text: str) -> None:
        self.insert_text_calls.append(text)

    def delete_before_cursor(self, count: int = 1) -> None:
        self.delete_before_cursor_calls.append(count)

    def validate_and_handle(self) -> None:
        self.validate_and_handle_called = True

    def reset(self) -> None:
        self.reset_called = True


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
    "enter": "c-m",
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

    def test_bottom_toolbar_returns_formatted_text_with_hints(self):
        handler = InputHandler()

        toolbar = handler.chat_session.bottom_toolbar
        assert toolbar is not None
        assert callable(toolbar)
        toolbar_fn = cast(Callable[[], Any], toolbar)
        rendered = "".join(text for _, text, *_ in to_formatted_text(toolbar_fn()))

        assert "/help" in rendered
        assert "bash" in rendered.lower()

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
