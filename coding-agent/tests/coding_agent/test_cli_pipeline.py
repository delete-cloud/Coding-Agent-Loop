# pyright: reportUnusedImport=false, reportMissingTypeStubs=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportAny=false, reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportArgumentType=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportMissingTypeArgument=false, reportUnusedVariable=false, reportUnusedCallResult=false, reportUnusedParameter=false

"""Tests: run + repl command wiring to PipelineAdapter (Pipeline is always active)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_create_agent():
    """Return (mock_pipeline, mock_ctx) pair from a mocked create_agent."""
    mock_pipeline = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.session_id = "test-session"
    return mock_pipeline, mock_ctx


def _make_outcome(**overrides) -> TurnOutcome:
    defaults = dict(
        stop_reason=StopReason.NO_TOOL_CALLS,
        final_message="done",
        steps_taken=1,
    )
    defaults.update(overrides)
    return TurnOutcome(**defaults)


def _make_config():
    """Build a minimal Config-like object for test use."""
    from coding_agent.core.config import Config

    return Config(
        provider="openai",
        model="gpt-4o-test",
        api_key="sk-test-key",
        max_steps=10,
    )


# ---------------------------------------------------------------------------
# TUI path
# ---------------------------------------------------------------------------


class TestTuiRunUsesPipeline:
    """_run_with_tui creates PipelineAdapter (only execution path)."""

    @pytest.mark.asyncio
    async def test_tui_run_uses_pipeline_adapter(self):
        mock_pipeline, mock_ctx = _mock_create_agent()
        mock_outcome = _make_outcome()

        # Mock PipelineAdapter
        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)
        mock_adapter_instance.close = AsyncMock()
        mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)

        mock_renderer = MagicMock()
        mock_consumer = MagicMock()

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=(mock_pipeline, mock_ctx),
            ) as p_create,
            patch(
                "coding_agent.__main__.PipelineAdapter", mock_adapter_cls
            ) as p_adapter,
            patch(
                "coding_agent.ui.stream_renderer.StreamingRenderer",
                return_value=mock_renderer,
            ),
            patch(
                "coding_agent.ui.rich_consumer.RichConsumer",
                return_value=mock_consumer,
            ),
        ):
            from coding_agent.__main__ import _run_with_tui

            config = _make_config()
            await _run_with_tui(config, "test goal")

        p_create.assert_called_once()

        mock_adapter_cls.assert_called_once_with(
            pipeline=mock_pipeline,
            ctx=mock_ctx,
            consumer=mock_consumer,
        )

        mock_adapter_instance.run_turn.assert_awaited_once_with("test goal")
        mock_adapter_instance.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Headless path
# ---------------------------------------------------------------------------


class TestHeadlessRunUsesPipeline:
    """_run_headless creates PipelineAdapter (only execution path)."""

    @pytest.mark.asyncio
    async def test_headless_run_uses_pipeline_adapter(self):
        mock_pipeline, mock_ctx = _mock_create_agent()
        mock_outcome = _make_outcome(final_message="headless done")

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)
        mock_adapter_instance.close = AsyncMock()
        mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)

        mock_consumer = MagicMock()
        mock_consumer_cls = MagicMock(return_value=mock_consumer)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=(mock_pipeline, mock_ctx),
            ) as p_create,
            patch("coding_agent.__main__.PipelineAdapter", mock_adapter_cls),
            patch("coding_agent.__main__.HeadlessConsumer", mock_consumer_cls),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "headless goal")

        p_create.assert_called_once()

        mock_adapter_cls.assert_called_once_with(
            pipeline=mock_pipeline,
            ctx=mock_ctx,
            consumer=mock_consumer,
        )

        mock_adapter_instance.run_turn.assert_awaited_once_with("headless goal")
        mock_adapter_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_headless_run_does_not_use_agent_loop(self):
        """Pipeline is the only path — AgentLoop module has been deleted entirely."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("coding_agent.core.loop")


# ---------------------------------------------------------------------------
# Integration: api_key forwarding
# ---------------------------------------------------------------------------


class TestPipelineApiKeyForwarding:
    """Verify create_agent receives the api_key from config."""

    @pytest.mark.asyncio
    async def test_create_agent_receives_api_key(self, monkeypatch):
        monkeypatch.setenv("USE_PIPELINE", "1")

        mock_pipeline, mock_ctx = _mock_create_agent()
        mock_outcome = _make_outcome()

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)
        mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)

        mock_consumer = MagicMock()
        mock_consumer_cls = MagicMock(return_value=mock_consumer)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=(mock_pipeline, mock_ctx),
            ) as p_create,
            patch("coding_agent.__main__.PipelineAdapter", mock_adapter_cls),
            patch("coding_agent.__main__.HeadlessConsumer", mock_consumer_cls),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "test goal")

        # create_agent must be called with api_key and model_override
        call_kwargs = p_create.call_args
        assert call_kwargs.kwargs.get("api_key") == "sk-test-key" or (
            call_kwargs.args and call_kwargs.args[0]
        ), "create_agent must receive api_key"


# ---------------------------------------------------------------------------
# Helpers for REPL tests
# ---------------------------------------------------------------------------


def _make_repl_config(**overrides):
    from coding_agent.core.config import Config

    defaults = dict(
        provider="openai",
        model="gpt-test",
        api_key="sk-test",
        base_url="http://localhost",
        repo=".",
        max_steps=5,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _ok_outcome(**kw) -> TurnOutcome:
    return TurnOutcome(
        stop_reason=StopReason.NO_TOOL_CALLS,
        final_message="ok",
        steps_taken=1,
        **kw,
    )


def _error_outcome(msg: str = "boom") -> TurnOutcome:
    return TurnOutcome(
        stop_reason=StopReason.ERROR,
        final_message=None,
        steps_taken=0,
        error=msg,
    )


# ---------------------------------------------------------------------------
# REPL ← PipelineAdapter wiring
# ---------------------------------------------------------------------------


class TestReplUsesPipeline:
    @patch("coding_agent.cli.repl.create_agent")
    def test_repl_uses_pipeline_adapter(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        assert isinstance(session._pipeline_adapter, PipelineAdapter)
        mock_create_agent.assert_called_once()


class TestMainDefaultEntry:
    def test_default_entry_uses_same_repl_runner(self):
        from coding_agent.__main__ import main

        runner = CliRunner()

        with patch("coding_agent.__main__._run_repl_command") as mock_run_repl_command:
            result = runner.invoke(main, [])

        assert result.exit_code == 0
        mock_run_repl_command.assert_called_once_with(
            repo=None,
            model=None,
            provider_name=None,
            base_url=None,
            api_key=None,
            max_steps=None,
        )

    def test_explicit_repl_uses_same_repl_runner(self):
        from coding_agent.__main__ import main

        runner = CliRunner()

        with patch("coding_agent.__main__._run_repl_command") as mock_run_repl_command:
            result = runner.invoke(
                main,
                [
                    "repl",
                    "--repo",
                    "/tmp/repo",
                    "--model",
                    "gpt-4.1",
                    "--provider",
                    "copilot",
                    "--base-url",
                    "https://example.test/v1",
                    "--api-key",
                    "token-123",
                    "--max-steps",
                    "17",
                ],
            )

        assert result.exit_code == 0
        mock_run_repl_command.assert_called_once_with(
            repo="/tmp/repo",
            model="gpt-4.1",
            provider_name="copilot",
            base_url="https://example.test/v1",
            api_key="token-123",
            max_steps=17,
        )


class TestCliConfigLoading:
    def test_repl_copilot_accepts_github_token_without_api_key(self):
        from coding_agent.__main__ import main

        runner = CliRunner()

        with patch(
            "coding_agent.cli.repl.run_repl", new_callable=AsyncMock
        ) as mock_run_repl:
            result = runner.invoke(
                main,
                ["repl", "--provider", "copilot", "--model", "gpt-4.1"],
                env={"GITHUB_TOKEN": "ghu-test-token"},
            )

        assert result.exit_code == 0
        (config,), _ = mock_run_repl.await_args

        assert config.provider == "copilot"
        assert config.api_key.get_secret_value() == "ghu-test-token"


class TestReplMultiturnContext:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_multiturn_context(self, mock_create_agent):
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape

        mock_pipeline = MagicMock()
        real_tape = Tape()
        mock_ctx = PipelineContext(tape=real_tape, session_id="multi-turn", config={})
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        adapter = session._pipeline_adapter
        assert adapter is not None

        call_messages: list[str] = []

        async def fake_run_turn(msg: str) -> TurnOutcome:
            call_messages.append(msg)
            from agentkit.tape.models import Entry

            mock_ctx.tape.append(
                Entry(kind="message", payload={"role": "user", "content": msg})
            )
            mock_ctx.tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": f"reply to {msg}"},
                )
            )
            return _ok_outcome()

        with patch.object(adapter, "run_turn", side_effect=fake_run_turn):
            await session._process_message("first question")
            await session._process_message("second question")

        assert call_messages == ["first question", "second question"]
        assert session._pipeline_adapter is adapter

        all_entries = list(mock_ctx.tape)
        user_entries = [
            e
            for e in all_entries
            if e.kind == "message" and e.payload.get("role") == "user"
        ]
        assert len(user_entries) == 2
        assert user_entries[0].payload["content"] == "first question"
        assert user_entries[1].payload["content"] == "second question"


class TestReplSlashCommands:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_slash_commands_still_work(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_pipeline.mount = AsyncMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        call_count = 0

        async def mock_get_input(prompt="", shell_mode=False, prompt_builder=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "/exit"
            return None

        session.input_handler.get_input = mock_get_input

        with patch("coding_agent.cli.repl.console"):
            await session.run()

        assert session.context["should_exit"] is True


class TestReplErrorRecovery:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_error_recovery(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        adapter = session._pipeline_adapter
        assert adapter is not None

        turn_results: list[TurnOutcome] = []

        async def side_effect_run_turn(msg: str) -> TurnOutcome:
            if len(turn_results) == 0:
                outcome = _error_outcome("pipeline exploded")
            else:
                outcome = _ok_outcome()
            turn_results.append(outcome)
            return outcome

        with patch.object(adapter, "run_turn", side_effect=side_effect_run_turn):
            await session._process_message("bad input")
            await session._process_message("good input")

        assert len(turn_results) == 2
        assert turn_results[0].stop_reason == StopReason.ERROR
        assert turn_results[1].stop_reason == StopReason.NO_TOOL_CALLS


class TestReplOutput:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_process_message_renders_user_message(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        adapter = session._pipeline_adapter
        assert adapter is not None

        mock_renderer = MagicMock()
        session._renderer = mock_renderer

        with patch.object(
            adapter,
            "run_turn",
            AsyncMock(return_value=_make_outcome(final_message="Hello from Kimi")),
        ):
            await session._process_message("hello")

        mock_renderer.user_message.assert_called_once_with("hello")


# ---------------------------------------------------------------------------
# T15: Additional headless Pipeline tests — output and isolation
# ---------------------------------------------------------------------------


class TestHeadlessPipelineOutput:
    """Verify _run_headless Pipeline path produces correct stdout output."""

    @pytest.mark.asyncio
    async def test_headless_pipeline_prints_result_summary(self, capsys):
        """Pipeline path prints '--- Result (stop_reason) ---' to stdout."""
        mock_outcome = _make_outcome(
            stop_reason=StopReason.NO_TOOL_CALLS,
            final_message="all done",
        )

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=_mock_create_agent(),
            ),
            patch(
                "coding_agent.__main__.PipelineAdapter",
                return_value=mock_adapter_instance,
            ),
            patch("coding_agent.__main__.HeadlessConsumer", MagicMock()),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "goal")

        captured = capsys.readouterr()
        assert "--- Result (" in captured.out
        assert "NO_TOOL_CALLS" in captured.out

    @pytest.mark.asyncio
    async def test_headless_pipeline_prints_final_message(self, capsys):
        """Final message is echoed to stdout when present."""
        mock_outcome = _make_outcome(final_message="Here is your answer")

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=_mock_create_agent(),
            ),
            patch(
                "coding_agent.__main__.PipelineAdapter",
                return_value=mock_adapter_instance,
            ),
            patch("coding_agent.__main__.HeadlessConsumer", MagicMock()),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "solve it")

        captured = capsys.readouterr()
        assert "Here is your answer" in captured.out

    @pytest.mark.asyncio
    async def test_headless_pipeline_no_final_message_omits_echo(self, capsys):
        """When final_message is None, only the result summary line is printed."""
        mock_outcome = _make_outcome(final_message=None)

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=_mock_create_agent(),
            ),
            patch(
                "coding_agent.__main__.PipelineAdapter",
                return_value=mock_adapter_instance,
            ),
            patch("coding_agent.__main__.HeadlessConsumer", MagicMock()),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "test goal")

        captured = capsys.readouterr()
        # Result summary line present
        assert "--- Result" in captured.out
        # No additional content line after the result summary
        lines = [l for l in captured.out.strip().split("\n") if l.strip()]
        result_lines = [l for l in lines if "--- Result" in l]
        assert len(result_lines) == 1


class TestHeadlessPipelineIsolation:
    """Pipeline path must not instantiate AgentLoop."""

    @pytest.mark.asyncio
    async def test_headless_pipeline_does_not_create_agent_loop(self):
        """AgentLoop module has been deleted — cannot be imported at all."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("coding_agent.core.loop")

    @pytest.mark.asyncio
    async def test_headless_pipeline_passes_model_override(self):
        """create_agent receives model_override from config."""
        mock_outcome = _make_outcome()

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)

        captured_kwargs: dict = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return _mock_create_agent()

        with (
            patch(
                "coding_agent.__main__.create_agent",
                side_effect=fake_create_agent,
            ),
            patch(
                "coding_agent.__main__.PipelineAdapter",
                return_value=mock_adapter_instance,
            ),
            patch("coding_agent.__main__.HeadlessConsumer", MagicMock()),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "goal")

        assert captured_kwargs.get("model_override") == "gpt-4o-test"

    @pytest.mark.asyncio
    async def test_headless_pipeline_forwards_runtime_config(self):
        mock_outcome = _make_outcome()

        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)

        captured_kwargs: dict = {}

        def fake_create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return _mock_create_agent()

        with (
            patch(
                "coding_agent.__main__.create_agent",
                side_effect=fake_create_agent,
            ),
            patch(
                "coding_agent.__main__.PipelineAdapter",
                return_value=mock_adapter_instance,
            ),
            patch("coding_agent.__main__.HeadlessConsumer", MagicMock()),
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_repl_config(
                provider="anthropic",
                model="claude-test",
                base_url="http://llm.local",
                repo="/tmp/repo",
                max_steps=7,
                approval_mode="interactive",
            )
            await _run_headless(config, "goal")

        assert captured_kwargs.get("model_override") == "claude-test"
        assert captured_kwargs.get("provider_override") == "anthropic"
        assert captured_kwargs.get("base_url_override") == "http://llm.local"
        assert str(captured_kwargs.get("workspace_root")) == "/tmp/repo"
        assert captured_kwargs.get("max_steps_override") == 7
        assert captured_kwargs.get("approval_mode_override") == "interactive"


class TestReplPipelineAdapterConsumerStable:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_consumer_is_same_across_turns(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        adapter = session._pipeline_adapter
        consumers_seen: list = []

        async def capture_consumer_run_turn(msg: str) -> TurnOutcome:
            consumers_seen.append(adapter._consumer)
            return _ok_outcome()

        with patch.object(adapter, "run_turn", side_effect=capture_consumer_run_turn):
            await session._process_message("turn 1")
            await session._process_message("turn 2")

        assert len(consumers_seen) == 2
        assert consumers_seen[0] is consumers_seen[1]
        assert consumers_seen[0] is session._consumer


class _RecordingConsumer:
    def __init__(self) -> None:
        self.messages: list[object] = []

    async def emit(self, msg: object) -> None:
        self.messages.append(msg)

    async def request_approval(self, req):
        from coding_agent.wire.protocol import ApprovalResponse

        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
        )


class _ScriptedSubagentProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "scripted-subagent"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(self, messages, tools=None, **kwargs):
        del messages, kwargs
        self.calls += 1
        if self.calls == 1:
            yield ToolCallEvent(
                tool_call_id="tc-repl-subagent",
                name="subagent",
                arguments={"goal": "Inspect child task"},
            )
            yield DoneEvent()
            return

        if self.calls == 2:
            assert tools is not None
            tool_names = {
                tool["function"]["name"]
                for tool in tools
                if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
            }
            assert "subagent" not in tool_names
            yield TextEvent(text="Child finished summary")
            yield DoneEvent()
            return

        yield TextEvent(text="Parent received child result")
        yield DoneEvent()


class TestReplSubagentEndToEnd:
    @pytest.mark.asyncio
    async def test_repl_process_message_runs_real_subagent_pipeline(self, tmp_path):
        from coding_agent.app import create_agent as create_real_agent
        from coding_agent.cli.repl import InteractiveSession
        from coding_agent.wire.protocol import (
            StreamDelta,
            ToolCallDelta,
            ToolResultDelta,
        )

        pipeline, ctx = create_real_agent(
            data_dir=tmp_path,
            api_key="sk-test",
            workspace_root=tmp_path,
            approval_mode_override="yolo",
        )
        provider = _ScriptedSubagentProvider()
        llm_plugin = pipeline._registry.get("llm_provider")
        llm_plugin._instance = provider

        recording_consumer = _RecordingConsumer()
        mock_renderer = MagicMock()

        with (
            patch(
                "coding_agent.cli.repl.create_agent",
                return_value=(pipeline, ctx),
            ),
            patch(
                "coding_agent.cli.repl.StreamingRenderer", return_value=mock_renderer
            ),
            patch(
                "coding_agent.cli.repl.RichConsumer", return_value=recording_consumer
            ),
        ):
            session = InteractiveSession(_make_repl_config(repo=str(tmp_path)))

        await session._process_message("Please delegate this to a subagent")

        tool_calls = [
            msg for msg in recording_consumer.messages if isinstance(msg, ToolCallDelta)
        ]
        assert any(msg.tool_name == "subagent" for msg in tool_calls)

        tool_results = [
            msg
            for msg in recording_consumer.messages
            if isinstance(msg, ToolResultDelta)
        ]
        assert any(
            msg.tool_name == "subagent"
            and msg.display_result == "Subagent completed: Child finished summary"
            for msg in tool_results
        )

        child_streams = [
            msg
            for msg in recording_consumer.messages
            if isinstance(msg, StreamDelta) and msg.agent_id.startswith("child-")
        ]
        assert child_streams
        assert any(msg.content == "Child finished summary" for msg in child_streams)
        assert provider.calls == 3


class TestReplCreateAgentConfigForwarding:
    @patch("coding_agent.cli.repl.create_agent")
    def test_repl_forwards_runtime_config(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config(
            provider="anthropic",
            model="claude-repl",
            base_url="http://llm.local",
            repo="/tmp/repl-repo",
            max_steps=9,
            approval_mode="auto",
        )
        InteractiveSession(config)

        call_kwargs = mock_create_agent.call_args.kwargs
        assert call_kwargs["model_override"] == "claude-repl"
        assert call_kwargs["provider_override"] == "anthropic"
        assert call_kwargs["base_url_override"] == "http://llm.local"
        assert str(call_kwargs["workspace_root"]) == "/tmp/repl-repo"
        assert call_kwargs["max_steps_override"] == 9
        assert call_kwargs["approval_mode_override"] == "auto"


# ---------------------------------------------------------------------------
# T16: Interactive approval via DirectiveExecutor in REPL
# ---------------------------------------------------------------------------


class TestApprovalWiringViaPipelineAdapter:
    """PipelineAdapter wires ask_user_handler to DirectiveExecutor via consumer."""

    def test_adapter_wires_handler_when_consumer_present(self):
        """After PipelineAdapter init, the DirectiveExecutor has an ask_user handler."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape
        from coding_agent.adapter import PipelineAdapter

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        ctx = PipelineContext(tape=Tape(), session_id="test")
        consumer = AsyncMock()

        PipelineAdapter(pipeline=mock_pipeline, ctx=ctx, consumer=consumer)

        assert executor._ask_user is not None
        assert callable(executor._ask_user)

    def test_adapter_no_consumer_leaves_handler_none(self):
        """Without consumer, DirectiveExecutor handler stays None."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape
        from coding_agent.adapter import PipelineAdapter

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        ctx = PipelineContext(tape=Tape(), session_id="test")

        PipelineAdapter(pipeline=mock_pipeline, ctx=ctx, consumer=None)

        assert executor._ask_user is None

    @pytest.mark.asyncio
    async def test_ask_user_handler_calls_consumer_request_approval(self):
        """Handler bridges to consumer.request_approval with correct ApprovalRequest."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape
        from coding_agent.adapter import PipelineAdapter
        from coding_agent.wire.protocol import ApprovalResponse

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        ctx = PipelineContext(tape=Tape(), session_id="test-session")

        consumer = AsyncMock()
        consumer.request_approval.return_value = ApprovalResponse(
            session_id="test-session", request_id="r1", approved=True
        )

        PipelineAdapter(pipeline=mock_pipeline, ctx=ctx, consumer=consumer)

        directive = AskUser(
            question="Allow web_search?",
            metadata={"tool_name": "web_search", "arguments": {"query": "test"}},
        )
        result = await executor.execute(directive)

        assert result is True
        consumer.request_approval.assert_called_once()
        req = consumer.request_approval.call_args[0][0]
        assert req.tool == "web_search"
        assert req.args == {"query": "test"}

    @pytest.mark.asyncio
    async def test_ask_user_handler_denied_returns_false(self):
        """When consumer denies, executor returns False."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser
        from agentkit.runtime.pipeline import PipelineContext
        from agentkit.tape.tape import Tape
        from coding_agent.adapter import PipelineAdapter
        from coding_agent.wire.protocol import ApprovalResponse

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        ctx = PipelineContext(tape=Tape(), session_id="test")

        consumer = AsyncMock()
        consumer.request_approval.return_value = ApprovalResponse(
            session_id="test", request_id="r1", approved=False
        )

        PipelineAdapter(pipeline=mock_pipeline, ctx=ctx, consumer=consumer)

        directive = AskUser(
            question="Allow bash_run?",
            metadata={"tool_name": "bash_run", "arguments": {"command": "rm -rf /"}},
        )
        result = await executor.execute(directive)

        assert result is False


class TestBatchModeAutoApprove:
    """In batch mode with yolo policy, approval doesn't block (returns Approve)."""

    @pytest.mark.asyncio
    async def test_batch_yolo_policy_returns_approve(self):
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import Approve
        from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy

        plugin = ApprovalPlugin(policy=ApprovalPolicy.YOLO)
        directive = plugin.approve_tool_call(
            tool_name="shell_exec", arguments={"cmd": "ls"}
        )

        assert isinstance(directive, Approve)

        executor = DirectiveExecutor()
        result = await executor.execute(directive)
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_auto_policy_requires_approval_for_unsafe_tool(self):
        from agentkit.directive.types import AskUser
        from coding_agent.plugins.approval import ApprovalPlugin, ApprovalPolicy

        plugin = ApprovalPlugin(policy=ApprovalPolicy.AUTO, safe_tools={"file_read"})
        directive = plugin.approve_tool_call(
            tool_name="shell_exec", arguments={"cmd": "ls"}
        )

        assert isinstance(directive, AskUser)

    @pytest.mark.asyncio
    async def test_batch_no_handler_rejects_ask_user(self):
        """DirectiveExecutor with no handler rejects AskUser (batch safety)."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        directive = AskUser(question="Allow?")
        result = await executor.execute(directive)
        assert result is False


class TestMemoryHandlerWiring:
    @pytest.mark.asyncio
    async def test_create_agent_directive_executor_has_memory_handler(self):
        from coding_agent.__main__ import create_agent

        pipeline, _ = create_agent(api_key="sk-test")
        executor = pipeline._directive_executor
        assert executor._memory is not None, (
            "memory_handler must be wired in create_agent"
        )

    @pytest.mark.asyncio
    async def test_memory_handler_calls_add_memory(self):
        from coding_agent.plugins.memory import MemoryPlugin
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import MemoryRecord

        plugin = MemoryPlugin()

        async def handler(directive: MemoryRecord) -> None:
            plugin.add_memory(directive)

        executor = DirectiveExecutor(memory_handler=handler)
        record = MemoryRecord(summary="test memory", tags=["auth.py"], importance=0.7)
        await executor.execute(record)

        assert len(plugin._working_memories) == 1
        assert plugin._working_memories[0]["summary"] == "test memory"


class TestHookRuntimeHasSpecs:
    @pytest.mark.asyncio
    async def test_hook_runtime_has_specs(self):
        from coding_agent.__main__ import create_agent
        from agentkit.runtime.hookspecs import HOOK_SPECS

        pipeline, _ = create_agent(api_key="sk-test")
        assert pipeline._runtime._specs == HOOK_SPECS


class TestPluginRegistryHasSpecs:
    @pytest.mark.asyncio
    async def test_plugin_registry_has_specs(self):
        from coding_agent.__main__ import create_agent
        from agentkit.runtime.hookspecs import HOOK_SPECS

        pipeline, _ = create_agent(api_key="sk-test")
        assert pipeline._registry._specs == HOOK_SPECS
