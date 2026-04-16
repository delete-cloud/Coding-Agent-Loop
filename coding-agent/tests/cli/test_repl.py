"""Tests for REPL functionality."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
        assert session.input_handler._paste_refs == {}

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
        assert session.input_handler._paste_refs == {}


class TestFooterIntegration:
    def _make_config(self):
        return SimpleNamespace(
            model="gpt-4o",
            repo=None,
            api_key=None,
            provider="openai",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )

    def test_init_creates_footer(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())
        assert hasattr(session, "_footer")
        assert session._footer is not None

    def test_footer_mode_is_spike_pending_before_run(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())
        assert session._footer.mode == "spike-pending"

    @pytest.mark.asyncio
    async def test_run_enables_and_disables_footer(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        enable_calls: list[str] = []
        disable_calls: list[str] = []

        def fake_spike():
            session._footer._mode = "persistent"
            return "persistent"

        monkeypatch.setattr(session._footer, "run_spike_check", fake_spike)
        monkeypatch.setattr(
            session._footer, "enable", lambda: enable_calls.append("enable")
        )
        monkeypatch.setattr(
            session._footer, "disable", lambda: disable_calls.append("disable")
        )

        async def fake_get_input(**kwargs):
            return None

        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)
        await session.run()

        assert enable_calls == ["enable"]
        assert disable_calls == ["disable"]

    @pytest.mark.asyncio
    async def test_footer_update_called_during_process_message(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        update_calls: list[dict[str, object]] = []

        def tracking_update(**kwargs):
            update_calls.append(kwargs)

        monkeypatch.setattr(session._footer, "update", tracking_update)
        session._footer._mode = "persistent"
        session._footer._enabled = True

        session._renderer = SimpleNamespace(user_message=lambda msg: None)
        session._pipeline_adapter = SimpleNamespace(
            run_turn=lambda msg: _async_return(
                SimpleNamespace(stop_reason=SimpleNamespace(ERROR=None), error=None)
            )
        )

        await session._process_message("hello")
        assert any("phase" in c for c in update_calls)

    @pytest.mark.asyncio
    async def test_clear_command_triggers_footer_redraw(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        session._footer._mode = "persistent"
        session._footer._enabled = True

        redraw_calls: list[str] = []
        monkeypatch.setattr(
            session._footer, "clear_and_redraw", lambda: redraw_calls.append("redraw")
        )

        from coding_agent.cli.commands import handle_command

        await handle_command("/clear", session.context)

        if hasattr(session, "_on_clear"):
            session._on_clear()
            assert redraw_calls == ["redraw"]

    def test_footer_not_enabled_in_nontty(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        spike_result = session._footer.run_spike_check()
        if not session._footer._console.is_terminal:
            assert spike_result == "fallback-toolbar"

    @pytest.mark.asyncio
    async def test_footer_disable_in_finally_on_error(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        disable_calls: list[str] = []
        monkeypatch.setattr(session._footer, "run_spike_check", lambda: "persistent")
        monkeypatch.setattr(session._footer, "enable", lambda: None)
        monkeypatch.setattr(
            session._footer, "disable", lambda: disable_calls.append("disable")
        )

        async def exploding_get_input(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(session.input_handler, "get_input", exploding_get_input)

        with pytest.raises(RuntimeError, match="boom"):
            await session.run()

        assert disable_calls == ["disable"]

    @pytest.mark.asyncio
    async def test_run_closes_pipeline_adapter_on_exit(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        close_calls: list[str] = []
        monkeypatch.setattr(session._footer, "run_spike_check", lambda: "persistent")
        monkeypatch.setattr(session._footer, "enable", lambda: None)
        monkeypatch.setattr(session._footer, "disable", lambda: None)

        async def fake_get_input(**kwargs):
            return None

        class FakeAdapter:
            async def initialize(self) -> None:
                return None

            async def close(self) -> None:
                close_calls.append("close")

        session._pipeline_adapter = FakeAdapter()
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)

        await session.run()

        assert close_calls == ["close"]


class TestSessionManagerIntegration:
    @pytest.mark.asyncio
    async def test_switch_active_session_rebinds_runtime_context(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = SimpleNamespace(
            model="gpt-4o",
            repo=None,
            api_key=None,
            provider="openai",
            base_url=None,
            max_steps=None,
            approval_mode=None,
        )
        session = InteractiveSession(config)

        fake_ctx = SimpleNamespace(
            config={
                "tool_registry": "registry-b",
                "skills_plugin": "skills-b",
                "mcp_plugin": "mcp-b",
            },
            tape=SimpleNamespace(tape_id="tape-b"),
        )
        fake_pipeline = object()

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    runtime_pipeline=fake_pipeline,
                    runtime_ctx=fake_ctx,
                    runtime_adapter="adapter-b",
                )

        session.context["session_manager"] = FakeSessionManager()
        await session._switch_session("session-b")

        assert session.context["session_id"] == "session-b"
        assert session.context["tool_registry"] == "registry-b"
        assert session.context["skills_plugin"] == "skills-b"
        assert session.context["mcp_plugin"] == "mcp-b"
        assert session._pipeline_ctx is fake_ctx
        assert session._pipeline_adapter == "adapter-b"

    def test_status_update_updates_input_toolbar_text(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())

        session._handle_status_update(
            {
                "phase": "idle",
                "tokens_in": 321,
                "tokens_out": 123,
                "elapsed_seconds": 9.0,
                "model_name": "gpt-4o",
                "context_percent": 12.5,
            }
        )

        assert "gpt-4o" in session.input_handler._status_text
        assert "321↑ 123↓" in session.input_handler._status_text

    def test_status_update_pushes_live_data_to_footer_in_persistent_mode(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())
        session._footer._mode = "persistent"
        session._footer._enabled = True

        calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            session._footer, "update", lambda **kwargs: calls.append(kwargs)
        )

        session._handle_status_update(
            {
                "phase": "thinking",
                "tokens_in": 10,
                "tokens_out": 5,
                "elapsed_seconds": 2.0,
                "model_name": "gpt-4o",
                "context_percent": 33.3,
            }
        )

        assert calls
        assert calls[-1]["tokens_in"] == 10
        assert calls[-1]["tokens_out"] == 5
        assert calls[-1]["phase"] == "thinking"


class TestReplInitialization:
    @pytest.mark.asyncio
    async def test_initialize_mounts_pipeline_before_first_command(self, monkeypatch):
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape
        from coding_agent.cli.repl import InteractiveSession

        mock_pipeline = MagicMock()
        mock_pipeline.mount = AsyncMock()
        mock_ctx = PipelineContext(tape=Tape(), session_id="repl-init", config={})
        mock_ctx.config["mcp_plugin"] = MagicMock()

        monkeypatch.setattr(
            "coding_agent.cli.repl.create_agent",
            lambda *args, **kwargs: (mock_pipeline, mock_ctx),
        )

        session = InteractiveSession(TestFooterIntegration()._make_config())

        assert "mcp_plugin" in session.context
        await session.initialize()
        mock_pipeline.mount.assert_awaited_once_with(mock_ctx)

    @pytest.mark.asyncio
    async def test_run_initializes_pipeline_before_prompt_loop(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(TestFooterIntegration()._make_config())

        initialize_calls: list[str] = []

        async def fake_initialize():
            initialize_calls.append("init")

        async def fake_get_input(**kwargs):
            return None

        monkeypatch.setattr(session, "initialize", fake_initialize)
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)

        await session.run()

        assert initialize_calls == ["init"]


async def _async_return(value):
    return value
