"""Tests for REPL functionality."""

from collections.abc import Callable
from inspect import isawaitable, getsource
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import asyncio
import pytest
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys

from coding_agent.cli import input_handler as input_handler_module
from coding_agent.cli.input_handler import InputHandler
from coding_agent.core.config import Config
from coding_agent.ui import session_manager as session_manager_module


ProviderName = Literal[
    "openai",
    "anthropic",
    "copilot",
    "kimi",
    "kimi-code",
    "kimi-code-anthropic",
]
ApprovalMode = Literal["yolo", "interactive", "auto"]


def _make_config(
    *,
    model: str = "gpt-4o",
    provider: ProviderName = "openai",
    base_url: str | None = None,
    max_steps: int = 30,
    approval_mode: ApprovalMode = "auto",
) -> Config:
    return Config(
        model=model,
        api_key=None,
        provider=provider,
        repo=Path("."),
        base_url=base_url,
        max_steps=max_steps,
        approval_mode=approval_mode,
    )


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

        _ = ctrlc_binding.handler(cast(KeyPressEvent, cast(object, event)))

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
        session_constructor = cast(Callable[[], object], InteractiveSession)
        with pytest.raises(TypeError):
            session_constructor()

    def test_repl_does_not_import_server_session_manager_for_local_runtime(self):
        import coding_agent.cli.repl as repl_module

        source = getsource(repl_module)

        assert (
            "from coding_agent.server.session_manager import SessionManager"
            not in source
        )
        assert "create_local_cli_session_manager" in source
        assert "from coding_agent.adapter import PipelineAdapter" not in source
        assert "create_local_cli_runtime" in source

    def test_cli_main_does_not_import_server_session_manager_for_local_runtime(self):
        import coding_agent.cli.main as cli_main_module

        source = getsource(cli_main_module)

        assert (
            "from coding_agent.server.session_manager import "
            "ApprovalPolicy, SessionManager" not in source
        )
        assert "from coding_agent.approval import ApprovalPolicy" in source
        assert "create_local_cli_session_manager" in source


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

        session = InteractiveSession(
            _make_config(model="kimi-for-coding", provider="kimi-code")
        )

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

        session = InteractiveSession(
            _make_config(model="kimi-for-coding", provider="kimi-code")
        )

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

        session = InteractiveSession(
            _make_config(model="kimi-for-coding", provider="kimi-code")
        )

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

        session = InteractiveSession(
            _make_config(model="kimi-for-coding", provider="kimi-code")
        )

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
        session = InteractiveSession(_make_config(model="test"))

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

        monkeypatch.setattr(session, "_renderer", FakeRenderer())
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
        session = InteractiveSession(_make_config(model="test"))

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

        monkeypatch.setattr(session, "_renderer", FakeRenderer())
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
        session = InteractiveSession(_make_config(model="test"))

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

        monkeypatch.setattr(session, "_renderer", FakeRenderer())
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
    def _make_config(self) -> Config:
        return Config(
            model="gpt-4o",
            api_key=None,
            provider="openai",
            repo=Path("."),
            base_url=None,
            max_steps=30,
            approval_mode="auto",
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

        monkeypatch.setattr(
            session, "_renderer", SimpleNamespace(user_message=lambda msg: None)
        )
        session._pipeline_adapter = SimpleNamespace(
            run_turn=lambda msg: _async_return(
                SimpleNamespace(stop_reason=SimpleNamespace(ERROR=None), error=None)
            )
        )

        await session._process_message("hello")
        assert any("phase" in c for c in update_calls)

    @pytest.mark.asyncio
    async def test_process_message_uses_managed_session_when_available(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.wire.local import LocalWire
        from coding_agent.wire.protocol import CompletionStatus, TurnEnd

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(self._make_config())
        wire = LocalWire("sess-repl")
        run_calls: list[tuple[str, str]] = []
        emitted: list[object] = []

        class FakeAdapter:
            def set_consumer(self, consumer):
                self.consumer = consumer

            async def run_turn(self, message: str):
                raise AssertionError(f"unmanaged run_turn used: {message}")

        class FakeSessionManager:
            async def get_session_async(self, session_id: str):
                assert session_id == "sess-repl"
                return SimpleNamespace(
                    wire=wire,
                    runtime_pipeline="pipeline",
                    runtime_ctx=SimpleNamespace(config={"tool_registry": "registry"}),
                    runtime_adapter=FakeAdapter(),
                )

            async def run_agent(self, session_id: str, message: str) -> None:
                run_calls.append((session_id, message))
                await wire.send(
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id="run-repl",
                        completion_status=CompletionStatus.COMPLETED,
                    )
                )

        async def fake_emit(message: object) -> None:
            emitted.append(message)

        monkeypatch.setattr(
            session, "_renderer", SimpleNamespace(user_message=lambda msg: None)
        )
        monkeypatch.setattr(session._consumer, "emit", fake_emit)
        session.context["session_id"] = "sess-repl"
        session._session_manager = FakeSessionManager()
        session._pipeline_adapter = FakeAdapter()

        await session._process_message("hello")

        assert run_calls == [("sess-repl", "hello")]
        assert [getattr(item, "turn_id", None) for item in emitted] == ["run-repl"]

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

        on_clear = session._on_clear
        assert callable(on_clear)
        assert callable(session.context["on_clear"])
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

        async def fake_initialize() -> None:
            return None

        monkeypatch.setattr(session, "initialize", fake_initialize)
        monkeypatch.setattr(session.input_handler, "get_input", fake_get_input)

        await session.run()

        assert close_calls == ["close"]


class TestSessionManagerIntegration:
    @pytest.mark.asyncio
    async def test_switch_active_session_rebinds_runtime_context(self, monkeypatch):
        from coding_agent.approval import ApprovalPolicy
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(_make_config(model="gpt-4o"))

        fake_ctx = SimpleNamespace(
            config={
                "tool_registry": "registry-b",
                "skills_plugin": "skills-b",
                "mcp_plugin": "mcp-b",
            },
            tape=SimpleNamespace(tape_id="tape-b"),
        )
        fake_pipeline = object()

        class FakeAdapter:
            def __init__(self):
                self.consumer = None

            def set_consumer(self, consumer):
                self.consumer = consumer

        fake_adapter = FakeAdapter()

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    provider_name="openai",
                    model_name="gpt-4o-mini",
                    base_url=None,
                    max_steps=30,
                    approval_policy=ApprovalPolicy.AUTO,
                    runtime_pipeline=fake_pipeline,
                    runtime_ctx=fake_ctx,
                    runtime_adapter=fake_adapter,
                )

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager
        await session._switch_session("session-b")

        assert session.context["session_id"] == "session-b"
        assert session.context["tool_registry"] == "registry-b"
        assert session.context["skills_plugin"] == "skills-b"
        assert session.context["mcp_plugin"] == "mcp-b"
        assert session._pipeline_ctx is fake_ctx
        assert session._pipeline_adapter is fake_adapter
        assert fake_adapter.consumer is session._consumer

    @pytest.mark.asyncio
    async def test_switch_active_session_rebinds_adapter_consumer(self, monkeypatch):
        from coding_agent.approval import ApprovalPolicy
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(_make_config(model="gpt-4o"))

        fake_ctx = SimpleNamespace(
            config={
                "tool_registry": "registry-b",
                "skills_plugin": "skills-b",
                "mcp_plugin": "mcp-b",
                "wire_consumer": "wire-b",
            },
            tape=SimpleNamespace(tape_id="tape-b"),
        )

        class FakeAdapter:
            def __init__(self):
                self.consumer = None

            def set_consumer(self, consumer):
                self.consumer = consumer

        adapter = FakeAdapter()

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    provider_name="openai",
                    model_name="gpt-4o-mini",
                    base_url=None,
                    max_steps=30,
                    approval_policy=ApprovalPolicy.AUTO,
                    runtime_pipeline=object(),
                    runtime_ctx=fake_ctx,
                    runtime_adapter=adapter,
                )

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        await session._switch_session("session-b")

        assert session._pipeline_adapter is adapter
        assert adapter.consumer is session._consumer
        assert session._pipeline_ctx is not None
        assert session._pipeline_ctx.config["wire_consumer"] is session._consumer

    @pytest.mark.asyncio
    async def test_switch_active_session_syncs_restored_model_into_repl_context(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.approval import ApprovalPolicy

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = _make_config(
            model="current-model",
            base_url="http://current.local",
            max_steps=30,
            approval_mode="auto",
        )
        session = InteractiveSession(config)
        session.context["model"] = "stale-model"

        fake_ctx = SimpleNamespace(
            config={
                "tool_registry": "registry-b",
            },
            tape=SimpleNamespace(tape_id="tape-b"),
        )

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    provider_name="anthropic",
                    model_name="checkpoint-model",
                    base_url="http://checkpoint.local",
                    max_steps=7,
                    approval_policy=ApprovalPolicy.INTERACTIVE,
                    runtime_pipeline=object(),
                    runtime_ctx=fake_ctx,
                    runtime_adapter=None,
                )

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        await session._switch_session("session-b")

        assert session.context["model"] == "checkpoint-model"
        assert session.config.provider == "anthropic"
        assert session.config.model == "checkpoint-model"
        assert session.config.base_url == "http://checkpoint.local"
        assert session.config.max_steps == 7
        assert session.config.approval_mode == "interactive"

    @pytest.mark.asyncio
    async def test_switch_session_rejects_invalid_restored_max_steps_without_mutating_state(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.approval import ApprovalPolicy

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = _make_config(
            model="current-model",
            base_url="http://current.local",
            max_steps=30,
            approval_mode="auto",
        )
        session = InteractiveSession(config)
        session.context["session_id"] = "session-a"
        session.context["model"] = "current-model"

        fake_ctx = SimpleNamespace(
            config={"tool_registry": "registry-b"},
            tape=SimpleNamespace(tape_id="tape-b"),
        )

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    provider_name="anthropic",
                    model_name="checkpoint-model",
                    base_url="http://checkpoint.local",
                    max_steps="seven",
                    approval_policy=ApprovalPolicy.INTERACTIVE,
                    runtime_pipeline=object(),
                    runtime_ctx=fake_ctx,
                    runtime_adapter=None,
                )

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        with pytest.raises(RuntimeError, match="invalid max_steps"):
            await session._switch_session("session-b")

        assert session.context["session_id"] == "session-a"
        assert session.context["model"] == "current-model"
        assert session.config.provider == "openai"
        assert session.config.model == "current-model"
        assert session.config.base_url == "http://current.local"
        assert session.config.max_steps == 30
        assert session.config.approval_mode == "auto"

    @pytest.mark.asyncio
    async def test_switch_session_rejects_missing_restored_provider_without_mutating_state(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.approval import ApprovalPolicy

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        config = _make_config(
            model="current-model",
            base_url="http://current.local",
            max_steps=30,
            approval_mode="auto",
        )
        session = InteractiveSession(config)
        session.context["session_id"] = "session-a"
        session.context["model"] = "current-model"

        fake_ctx = SimpleNamespace(
            config={"tool_registry": "registry-b"},
            tape=SimpleNamespace(tape_id="tape-b"),
        )

        class FakeSessionManager:
            async def ensure_session_runtime(self, session_id: str):
                assert session_id == "session-b"
                return fake_ctx

            def get_session(self, session_id: str):
                assert session_id == "session-b"
                return SimpleNamespace(
                    id="session-b",
                    model_name="checkpoint-model",
                    base_url="http://checkpoint.local",
                    max_steps=7,
                    approval_policy=ApprovalPolicy.INTERACTIVE,
                    runtime_pipeline=object(),
                    runtime_ctx=fake_ctx,
                    runtime_adapter=None,
                )

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        with pytest.raises(RuntimeError, match="invalid provider_name"):
            await session._switch_session("session-b")

        assert session.context["session_id"] == "session-a"
        assert session.context["model"] == "current-model"
        assert session.config.provider == "openai"
        assert session.config.model == "current-model"
        assert session.config.base_url == "http://current.local"
        assert session.config.max_steps == 30
        assert session.config.approval_mode == "auto"

    def test_status_update_updates_input_toolbar_text(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        monkeypatch.setattr(InteractiveSession, "_setup_agent", lambda self: None)
        session = InteractiveSession(TestFooterIntegration()._make_config())

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
        session = InteractiveSession(TestFooterIntegration()._make_config())
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
    async def test_model_command_rebuilds_pipeline_on_next_turn(self, monkeypatch):
        from coding_agent.cli.commands import handle_command
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.core.config import Config

        create_agent_calls: list[dict[str, object]] = []
        run_models: list[str | None] = []

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, consumer
                self.ctx = ctx

            async def initialize(self) -> None:
                return None

            async def close(self) -> None:
                return None

            async def run_turn(self, message: str):
                del message
                run_models.append(self.ctx.config.get("model"))
                stop_reason = SimpleNamespace(ERROR="error")
                return SimpleNamespace(stop_reason=stop_reason, error=None)

        def fake_create_agent(*args, **kwargs):
            del args
            create_agent_calls.append(dict(kwargs))
            model = kwargs.get("model_override")
            tape = kwargs.get("tape") or SimpleNamespace(tape_id="repl-tape")
            return object(), SimpleNamespace(
                config={"model": model, "tool_registry": "registry-a"},
                tape=tape,
            )

        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.create_agent", fake_create_agent
        )
        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.PipelineAdapter", FakeAdapter
        )
        session = InteractiveSession(Config(model="gpt-4o-test", max_steps=10))

        handled = await handle_command("/model claude-next", session.context)
        await session._process_message("hello")

        assert handled is True
        assert session.config.model == "claude-next"
        assert create_agent_calls[-1]["model_override"] == "claude-next"
        assert run_models == ["claude-next"]

    @pytest.mark.asyncio
    async def test_model_change_failure_keeps_existing_runtime(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.core.config import Config

        current_pipeline = object()
        current_ctx = SimpleNamespace(config={"tool_registry": "registry-a"})

        class CurrentAdapter:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        current_adapter = CurrentAdapter()
        managed_session = SimpleNamespace(
            id="session-a",
            model_name="gpt-4o-test",
            runtime_pipeline=current_pipeline,
            runtime_ctx=current_ctx,
            runtime_adapter=current_adapter,
        )

        class FakeSessionManager:
            def __init__(self) -> None:
                self.replace_calls: list[tuple[str, str]] = []

            async def replace_session_runtime_config(
                self,
                session_id: str,
                *,
                model_name: str,
            ):
                self.replace_calls.append((session_id, model_name))
                raise RuntimeError("new runtime failed")

        session = InteractiveSession(Config(model="gpt-4o-test", max_steps=10))
        fake_session_manager = cast(Any, FakeSessionManager())
        session._session_manager = fake_session_manager
        session.context["session_manager"] = fake_session_manager
        session.context["session_id"] = "session-a"
        session.context["model"] = "gpt-4o-test"
        session._pipeline = current_pipeline
        session._pipeline_ctx = current_ctx
        session._pipeline_adapter = current_adapter

        with pytest.raises(RuntimeError, match="new runtime failed"):
            await session._change_model_for_next_turn("claude-next")

        assert session._pipeline is current_pipeline
        assert session._pipeline_ctx is current_ctx
        assert session._pipeline_adapter is current_adapter
        assert session.context["model"] == "gpt-4o-test"
        assert session.config.model == "gpt-4o-test"
        assert managed_session.model_name == "gpt-4o-test"
        assert managed_session.runtime_pipeline is current_pipeline
        assert managed_session.runtime_ctx is current_ctx
        assert managed_session.runtime_adapter is current_adapter
        assert current_adapter.closed is False
        assert fake_session_manager.replace_calls == [("session-a", "claude-next")]

    @pytest.mark.asyncio
    async def test_model_change_persist_failure_restores_existing_runtime(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.core.config import Config

        current_pipeline = object()
        current_ctx = SimpleNamespace(config={"tool_registry": "registry-a"})

        class CurrentAdapter:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        current_adapter = CurrentAdapter()
        replacement_pipeline = object()
        replacement_ctx = SimpleNamespace(
            config={"tool_registry": "registry-b", "wire_consumer": None},
            tape=SimpleNamespace(tape_id="replacement-tape"),
        )

        class ReplacementAdapter:
            def __init__(self) -> None:
                self.consumer = None
                self.closed = False

            def set_consumer(self, consumer):
                self.consumer = consumer

            async def close(self) -> None:
                self.closed = True

        replacement_adapter = ReplacementAdapter()

        class FakeManagedSession(SimpleNamespace):
            def runtime_binding_snapshot(self):
                return SimpleNamespace(
                    pipeline=self.runtime_pipeline,
                    ctx=self.runtime_ctx,
                    adapter=self.runtime_adapter,
                )

            def restore_runtime_binding(self, snapshot) -> None:
                self.runtime_pipeline = snapshot.pipeline
                self.runtime_ctx = snapshot.ctx
                self.runtime_adapter = snapshot.adapter

            def attach_runtime_binding(self, *, pipeline, ctx, adapter) -> None:
                self.runtime_pipeline = pipeline
                self.runtime_ctx = ctx
                self.runtime_adapter = adapter

        managed_session = FakeManagedSession(
            id="session-a",
            provider=None,
            provider_name="openai",
            model_name="gpt-4o-test",
            base_url=None,
            max_steps=10,
            tape_id="current-tape",
            task=None,
            turn_in_progress=False,
            runtime_pipeline=current_pipeline,
            runtime_ctx=current_ctx,
            runtime_adapter=current_adapter,
        )

        class FakeSessionManager:
            def __init__(self) -> None:
                self.build_calls: list[str] = []
                self.persisted_models: list[str] = []
                self.closed_adapters: list[object] = []
                self._runtime_maintenance_admission = (
                    session_manager_module.RuntimeMaintenanceAdmissionService(
                        turn_lock_for=self._turn_lock_for,
                        assert_owner=self._assert_owner,
                        load_session=self.get_session_async,
                    )
                )
                self._runtime_closer = SimpleNamespace(
                    close_adapter=self.close_runtime_adapter
                )
                self._runtime_replacement_service = (
                    session_manager_module.RuntimeReplacementService(
                        close_runtime_adapter=self.close_runtime_adapter,
                    )
                )

            def _turn_lock_for(self, session_id: str):
                del session_id
                return asyncio.Lock()

            async def _assert_owner(self, session_id: str) -> None:
                assert session_id == "session-a"

            async def get_session_async(self, session_id: str):
                assert session_id == "session-a"
                return managed_session

            async def _build_session_runtime(self, session, *, model_name: str):
                assert session is managed_session
                self.build_calls.append(model_name)
                return replacement_pipeline, replacement_ctx, replacement_adapter

            async def _persist_session_async(self, session) -> None:
                self.persisted_models.append(session.model_name)
                if session.model_name == "claude-next":
                    raise RuntimeError("persist failed")

            async def close_runtime_adapter(self, adapter) -> None:
                self.closed_adapters.append(adapter)
                close = getattr(adapter, "close", None)
                if callable(close):
                    result = close()
                    if isawaitable(result):
                        await result

            replace_session_runtime_config = (
                session_manager_module.SessionManager.replace_session_runtime_config
            )

        session = InteractiveSession(Config(model="gpt-4o-test", max_steps=10))
        fake_session_manager = cast(Any, FakeSessionManager())
        session._session_manager = fake_session_manager
        session.context["session_manager"] = fake_session_manager
        session.context["session_id"] = "session-a"
        session.context["model"] = "gpt-4o-test"
        session._pipeline = current_pipeline
        session._pipeline_ctx = current_ctx
        session._pipeline_adapter = current_adapter

        with pytest.raises(RuntimeError, match="persist failed"):
            await session._change_model_for_next_turn("claude-next")

        assert session.config.model == "gpt-4o-test"
        assert session.context["model"] == "gpt-4o-test"
        assert session._pipeline is current_pipeline
        assert session._pipeline_ctx is current_ctx
        assert session._pipeline_adapter is current_adapter
        assert managed_session.model_name == "gpt-4o-test"
        assert managed_session.tape_id == "current-tape"
        assert managed_session.runtime_pipeline is current_pipeline
        assert managed_session.runtime_ctx is current_ctx
        assert managed_session.runtime_adapter is current_adapter
        assert current_adapter.closed is False
        assert replacement_adapter.closed is True
        assert fake_session_manager.build_calls == ["claude-next"]
        assert fake_session_manager.persisted_models == ["claude-next"]
        assert fake_session_manager.closed_adapters == [replacement_adapter]

    @pytest.mark.asyncio
    async def test_initialize_creates_managed_session_without_asyncio_run(
        self, monkeypatch
    ):
        from coding_agent.cli.repl import InteractiveSession

        created_sessions: list[dict[str, object]] = []

        async def fake_create_session(**kwargs):
            created_sessions.append(dict(kwargs))
            return "session-init"

        class FakeSessionManager:
            def __init__(self) -> None:
                self.renewal_started = False

            async def create_session(self, **kwargs):
                return await fake_create_session(**kwargs)

            def get_session(self, session_id: str):
                assert session_id == "session-init"
                return SimpleNamespace(
                    runtime_pipeline=None,
                    runtime_ctx=None,
                    runtime_adapter=None,
                    tape_id=None,
                )

            async def attach_runtime(
                self,
                managed_session,
                *,
                pipeline,
                pipeline_ctx,
                pipeline_adapter,
            ):
                managed_session.runtime_pipeline = pipeline
                managed_session.runtime_ctx = pipeline_ctx
                managed_session.runtime_adapter = pipeline_adapter
                managed_session.tape_id = pipeline_ctx.tape.tape_id
                assert managed_session.tape_id == "repl-init-tape"

            async def start_owner_lease_renewal(self) -> None:
                self.renewal_started = True

        fake_pipeline = object()
        fake_ctx = SimpleNamespace(
            config={"tool_registry": "registry-a"},
            tape=SimpleNamespace(tape_id="repl-init-tape"),
        )

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del consumer
                assert pipeline is fake_pipeline
                assert ctx is fake_ctx

            async def initialize(self) -> None:
                return None

            async def close(self) -> None:
                return None

        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.create_agent",
            lambda *args, **kwargs: (fake_pipeline, fake_ctx),
        )
        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.PipelineAdapter", FakeAdapter
        )
        session = InteractiveSession(TestFooterIntegration()._make_config())

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        await session.initialize()

        assert session.context["session_id"] == "session-init"
        assert (
            created_sessions
            and created_sessions[0]["provider_name"] == session.config.provider
        )
        assert created_sessions[0]["origin"] == {
            "channel": "local_cli",
            "entrypoint": "repl",
            "mode": "interactive",
        }
        assert session._pipeline_ctx is fake_ctx
        assert fake_session_manager.renewal_started is True

    @pytest.mark.asyncio
    async def test_session_new_hook_creates_repl_origin_session(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        created_sessions: list[dict[str, object]] = []

        class FakeSessionManager:
            async def create_session(self, **kwargs):
                created_sessions.append(dict(kwargs))
                return "session-new"

        session = InteractiveSession(TestFooterIntegration()._make_config())
        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        create_session = session.context["create_session"]
        assert callable(create_session)
        session_id = await create_session()

        assert session_id == "session-new"
        assert created_sessions[0]["origin"] == {
            "channel": "local_cli",
            "entrypoint": "repl",
            "mode": "interactive",
        }

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
            "coding_agent.cli.local_runtime.create_agent",
            lambda *args, **kwargs: (mock_pipeline, mock_ctx),
        )

        session = InteractiveSession(TestFooterIntegration()._make_config())

        assert "mcp_plugin" in session.context
        await session.initialize()
        mock_pipeline.mount.assert_awaited_once_with(mock_ctx)

    @pytest.mark.asyncio
    async def test_initialize_does_not_reregister_attached_runtime(self, monkeypatch):
        from coding_agent.cli.repl import InteractiveSession

        class FakeSessionManager:
            async def create_session(self, **kwargs):
                return "session-init"

            def get_session(self, session_id: str):
                assert session_id == "session-init"
                return SimpleNamespace(
                    runtime_pipeline=None,
                    runtime_ctx=None,
                    runtime_adapter=None,
                    tape_id=None,
                )

            def register_session(self, managed_session):
                raise AssertionError("initialize should not re-register live runtime")

            async def attach_runtime(
                self,
                managed_session,
                *,
                pipeline,
                pipeline_ctx,
                pipeline_adapter,
            ):
                managed_session.runtime_pipeline = pipeline
                managed_session.runtime_ctx = pipeline_ctx
                managed_session.runtime_adapter = pipeline_adapter
                managed_session.tape_id = pipeline_ctx.tape.tape_id
                return None

            async def start_owner_lease_renewal(self) -> None:
                return None

        fake_pipeline = object()
        fake_ctx = SimpleNamespace(
            config={"tool_registry": "registry-a"},
            tape=SimpleNamespace(tape_id="repl-init-tape"),
        )

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, ctx, consumer

            async def initialize(self) -> None:
                return None

            async def close(self) -> None:
                return None

        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.create_agent",
            lambda *args, **kwargs: (fake_pipeline, fake_ctx),
        )
        monkeypatch.setattr(
            "coding_agent.cli.local_runtime.PipelineAdapter", FakeAdapter
        )
        session = InteractiveSession(TestFooterIntegration()._make_config())

        fake_session_manager = FakeSessionManager()
        monkeypatch.setattr(session, "_session_manager", fake_session_manager)
        session.context["session_manager"] = fake_session_manager

        await session.initialize()

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
