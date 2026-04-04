"""Tests for REPL functionality."""

from types import SimpleNamespace

import pytest
from prompt_toolkit.keys import Keys

from coding_agent.cli import input_handler as input_handler_module
from coding_agent.cli.input_handler import InputHandler


def _get_key_binding(handler: InputHandler, key: Keys):
    return next(
        binding for binding in handler.bindings.bindings if binding.keys == (key,)
    )


class TestInputHandler:
    def test_input_handler_creation(self):
        handler = InputHandler()
        assert handler is not None
        assert handler.chat_session is not None
        assert handler.shell_session is not None

    @pytest.mark.asyncio
    async def test_get_input_mock(self, monkeypatch):
        """Test input with mocked prompt."""
        handler = InputHandler()

        # Mock the prompt_async to return test input
        async def mock_prompt(*args, **kwargs):
            return "test input"

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input()
        assert result == "test input"

    @pytest.mark.asyncio
    async def test_get_input_with_custom_prompt(self, monkeypatch):
        """Test input with custom prompt."""
        handler = InputHandler()

        async def mock_prompt(prompt, **kwargs):
            return f"received: {prompt}"

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input(prompt="[0] >")
        # Result is stripped of trailing whitespace
        assert result == "received: [0] >"

    @pytest.mark.asyncio
    async def test_get_input_strips_whitespace(self, monkeypatch):
        """Test that input is properly stripped."""
        handler = InputHandler()

        async def mock_prompt(*args, **kwargs):
            return "  input with spaces  "

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input()
        assert result == "input with spaces"

    @pytest.mark.asyncio
    async def test_get_input_eof_error(self, monkeypatch):
        """Test handling of EOFError (Ctrl+D)."""
        handler = InputHandler()

        async def mock_prompt(*args, **kwargs):
            raise EOFError()

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_input_keyboard_interrupt(self, monkeypatch):
        """Test handling of KeyboardInterrupt (Ctrl+C)."""
        handler = InputHandler()

        async def mock_prompt(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_input_none_result_returns_none(self, monkeypatch):
        handler = InputHandler()

        async def mock_prompt(*args, **kwargs):
            return None

        monkeypatch.setattr(handler.chat_session, "prompt_async", mock_prompt)

        result = await handler.get_input()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_input_uses_shell_session_in_shell_mode(self, monkeypatch):
        handler = InputHandler()
        calls: list[str] = []

        async def chat_prompt(*args, **kwargs):
            calls.append("chat")
            return "chat"

        async def shell_prompt(*args, **kwargs):
            calls.append("shell")
            return "shell"

        monkeypatch.setattr(handler.chat_session, "prompt_async", chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", shell_prompt)

        result = await handler.get_input(shell_mode=True)

        assert result == "shell"
        assert calls == ["shell"]

    @pytest.mark.asyncio
    async def test_get_input_uses_chat_session_by_default(self, monkeypatch):
        handler = InputHandler()
        calls: list[str] = []

        async def chat_prompt(*args, **kwargs):
            calls.append("chat")
            return "chat"

        async def shell_prompt(*args, **kwargs):
            calls.append("shell")
            return "shell"

        monkeypatch.setattr(handler.chat_session, "prompt_async", chat_prompt)
        monkeypatch.setattr(handler.shell_session, "prompt_async", shell_prompt)

        result = await handler.get_input(shell_mode=False)

        assert result == "chat"
        assert calls == ["chat"]

    def test_key_bindings_exist(self):
        """Test that key bindings are set up."""
        handler = InputHandler()
        assert handler.bindings is not None

    def test_ctrlc_tracking_initialised_at_zero(self):
        handler = InputHandler()
        assert handler._last_ctrlc == 0.0

    def test_ctrlc_timeout_is_two_seconds(self):
        from coding_agent.cli.input_handler import _CTRLC_TIMEOUT

        assert _CTRLC_TIMEOUT == 2.0

    def test_single_ctrlc_records_timestamp(self):
        import time
        from coding_agent.cli.input_handler import _CTRLC_TIMEOUT

        handler = InputHandler()
        before = time.monotonic()
        handler._simulate_ctrlc()
        after = time.monotonic()
        assert before <= handler._last_ctrlc <= after

    def test_double_ctrlc_within_timeout_returns_exit_sentinel(self):
        import time

        handler = InputHandler()
        handler._last_ctrlc = time.monotonic()
        assert handler._should_exit_on_ctrlc() is True

    def test_ctrlc_after_timeout_does_not_exit(self):
        from coding_agent.cli.input_handler import _CTRLC_TIMEOUT

        handler = InputHandler()
        handler._last_ctrlc = 0.0
        assert handler._should_exit_on_ctrlc() is False

    def test_first_ctrlc_uses_prompt_toolkit_run_in_terminal(self, monkeypatch):
        handler = InputHandler()
        ctrlc_binding = _get_key_binding(handler, Keys.ControlC)

        terminal_calls: list[str] = []

        def fake_run_in_terminal(callback):
            terminal_calls.append("module")
            callback()
            return None

        monkeypatch.setattr(
            input_handler_module, "run_in_terminal", fake_run_in_terminal
        )

        class DummyBuffer:
            def __init__(self):
                self.reset_called = False

            def reset(self):
                self.reset_called = True

        class DummyApp:
            def __init__(self):
                self.current_buffer = DummyBuffer()
                self.exit_called = False
                self.app_method_calls = 0

            def exit(self):
                self.exit_called = True

            def run_in_terminal(self, callback):
                self.app_method_calls += 1
                callback()

        event = SimpleNamespace(app=DummyApp())

        ctrlc_binding.handler(event)

        assert event.app.current_buffer.reset_called is True
        assert event.app.exit_called is False
        assert event.app.app_method_calls == 0
        assert terminal_calls == ["module"]


class TestREPLImports:
    """Test that REPL module imports work correctly."""

    def test_repl_module_imports(self):
        """Test that repl module can be imported."""
        from coding_agent.cli.repl import InteractiveSession, run_repl

        assert InteractiveSession is not None
        assert run_repl is not None

    def test_repl_session_creation_requires_config(self):
        """Test that InteractiveSession requires a config."""
        from coding_agent.cli.repl import InteractiveSession

        # Should raise TypeError without config
        with pytest.raises(TypeError):
            InteractiveSession()


class TestBashIntegration:
    def test_bang_detected_in_repl(self):
        from coding_agent.cli.bash_executor import is_bash_command

        assert is_bash_command("!ls")
        assert is_bash_command("! git status")
        assert not is_bash_command("hello")
        assert not is_bash_command("/help")

    def test_bang_extraction(self):
        from coding_agent.cli.bash_executor import extract_bash_command

        assert extract_bash_command("!ls") == "ls"
        assert extract_bash_command("! git diff") == "git diff"

    @pytest.mark.asyncio
    async def test_bare_bang_enters_shell_mode_until_exit(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)

        config = SimpleNamespace(
            model="kimi-for-coding",
            repo=None,
            api_key=None,
            provider="kimi-code",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        inputs = iter(["pwd", "exit", None])
        executed: list[str] = []
        processed_messages: list[str] = []

        async def fake_get_input(prompt=None, shell_mode=False, prompt_builder=None):
            return next(inputs)

        async def fake_execute(command: str):
            executed.append(command)
            return 0

        async def fake_process_message(message: str):
            processed_messages.append(message)

        session.input_handler._shell_mode = True
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)
        monkeypatch.setattr(session._bash_executor, "execute", fake_execute)
        monkeypatch.setattr(session, "_process_message", fake_process_message)

        await session.run()

        assert executed == ["pwd"]
        assert processed_messages == []

    @pytest.mark.asyncio
    async def test_bang_bash_enters_shell_mode_until_exit(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)

        config = SimpleNamespace(
            model="kimi-for-coding",
            repo=None,
            api_key=None,
            provider="kimi-code",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        inputs = iter(["ls -la", "exit", None])
        executed: list[str] = []
        processed_messages: list[str] = []

        async def fake_get_input(
            command: str | None = None, shell_mode: bool = False, prompt_builder=None
        ):
            return next(inputs)

        async def fake_execute(command: str):
            executed.append(command)
            return 0

        async def fake_process_message(message: str):
            processed_messages.append(message)

        session.input_handler._shell_mode = True
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)
        monkeypatch.setattr(session._bash_executor, "execute", fake_execute)
        monkeypatch.setattr(session, "_process_message", fake_process_message)

        await session.run()

        assert executed == ["ls -la"]
        assert processed_messages == []

    @pytest.mark.asyncio
    async def test_repl_passes_shell_mode_to_input_handler(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)

        config = SimpleNamespace(
            model="kimi-for-coding",
            repo=None,
            api_key=None,
            provider="kimi-code",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        inputs = iter(["ls -la", None])
        executed: list[str] = []

        async def fake_get_input(prompt=None, shell_mode=False, prompt_builder=None):
            return next(inputs)

        async def fake_execute(command: str):
            executed.append(command)
            return 0

        session.input_handler._shell_mode = True
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)
        monkeypatch.setattr(session._bash_executor, "execute", fake_execute)

        await session.run()

        assert executed == ["ls -la"]

    @pytest.mark.asyncio
    async def test_repl_only_patches_stdout_while_waiting_for_input(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)

        config = SimpleNamespace(
            model="kimi-for-coding",
            repo=None,
            api_key=None,
            provider="kimi-code",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        patched = {"active": False}
        observed: list[bool] = []
        inputs = iter(["hello", None])

        class FakePatchStdout:
            def __enter__(self):
                patched["active"] = True
                return self

            def __exit__(self, exc_type, exc, tb):
                patched["active"] = False
                return False

        async def fake_get_input(prompt=None, shell_mode=False, prompt_builder=None):
            observed.append(patched["active"])
            return next(inputs)

        async def fake_process_message(message: str):
            observed.append(patched["active"])

        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)
        monkeypatch.setattr(session, "_process_message", fake_process_message)
        monkeypatch.setattr("coding_agent.cli.repl.patch_stdout", FakePatchStdout)

        await session.run()

        assert observed == [True, False, True]


class TestPasteFoldingInRepl:
    @pytest.mark.asyncio
    async def test_short_message_unchanged(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = SimpleNamespace(
            model="test",
            repo=None,
            api_key=None,
            provider="test",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        rendered_messages: list[str] = []
        turned_messages: list[str] = []

        class FakeAdapter:
            async def run_turn(self, message: str):
                turned_messages.append(message)
                return SimpleNamespace(
                    stop_reason=SimpleNamespace(ERROR=None), error=None
                )

        class FakeRenderer:
            def user_message(self, msg: str):
                rendered_messages.append(msg)

        session._renderer = FakeRenderer()
        session._pipeline_adapter = FakeAdapter()

        await session._process_message("short message")
        assert rendered_messages == ["short message"]
        assert turned_messages == ["short message"]

    @pytest.mark.asyncio
    async def test_long_message_folded_for_display_but_expanded_for_agent(
        self, monkeypatch
    ):
        from coding_agent.cli.input_handler import fold_pasted_content
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = SimpleNamespace(
            model="test",
            repo=None,
            api_key=None,
            provider="test",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        long_message = "\n".join(f"line {i}" for i in range(25))
        rendered_messages: list[str] = []
        turned_messages: list[str] = []

        class FakeAdapter:
            async def run_turn(self, message: str):
                turned_messages.append(message)
                return SimpleNamespace(
                    stop_reason=SimpleNamespace(ERROR=None), error=None
                )

        class FakeRenderer:
            def user_message(self, msg: str):
                rendered_messages.append(msg)

        session._renderer = FakeRenderer()
        session._pipeline_adapter = FakeAdapter()

        # Simulate what BracketedPaste handler does: fold and store refs
        folded, refs = fold_pasted_content(long_message, ref_id="test")
        session.input_handler._paste_refs.update(refs)

        await session._process_message(folded)
        assert len(rendered_messages) == 1
        assert "[Pasted text" in rendered_messages[0]
        assert len(turned_messages) == 1
        assert turned_messages[0] == long_message

    @pytest.mark.asyncio
    async def test_mixed_context_and_large_block_keeps_context_in_display(
        self, monkeypatch
    ):
        from coding_agent.cli.input_handler import fold_pasted_content
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = SimpleNamespace(
            model="test",
            repo=None,
            api_key=None,
            provider="test",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        block = "\n".join(f"line {i}" for i in range(25))
        mixed_message = f"before context\n\n{block}\n\nafter context"
        rendered_messages: list[str] = []
        turned_messages: list[str] = []

        class FakeAdapter:
            async def run_turn(self, message: str):
                turned_messages.append(message)
                return SimpleNamespace(
                    stop_reason=SimpleNamespace(ERROR=None), error=None
                )

        class FakeRenderer:
            def user_message(self, msg: str):
                rendered_messages.append(msg)

        session._renderer = FakeRenderer()
        session._pipeline_adapter = FakeAdapter()

        # Simulate BracketedPaste: fold and store refs
        folded, refs = fold_pasted_content(mixed_message, ref_id="test")
        session.input_handler._paste_refs.update(refs)

        await session._process_message(folded)
        assert len(rendered_messages) == 1
        assert "before context" not in rendered_messages[0]
        assert "after context" not in rendered_messages[0]
        assert "[Pasted text" in rendered_messages[0]
        assert turned_messages == [mixed_message]
