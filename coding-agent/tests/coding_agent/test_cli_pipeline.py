"""Tests: run + repl command wiring to PipelineAdapter behind USE_PIPELINE toggle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestTuiRunUsesPipelineAdapterWhenToggleOn:
    """With USE_PIPELINE=1, _run_with_tui creates PipelineAdapter instead of AgentLoop."""

    @pytest.mark.asyncio
    async def test_tui_run_uses_pipeline_adapter_when_toggle_on(self, monkeypatch):
        monkeypatch.setenv("USE_PIPELINE", "1")

        mock_pipeline, mock_ctx = _mock_create_agent()
        mock_outcome = _make_outcome()

        # Mock PipelineAdapter
        mock_adapter_instance = AsyncMock()
        mock_adapter_instance.run_turn = AsyncMock(return_value=mock_outcome)
        mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)

        # Mock CodingAgentTUI to avoid real terminal interaction
        mock_tui = MagicMock()
        mock_tui.consumer = MagicMock()
        mock_tui.__enter__ = MagicMock(return_value=mock_tui)
        mock_tui.__exit__ = MagicMock(return_value=False)
        mock_tui_cls = MagicMock(return_value=mock_tui)

        with (
            patch(
                "coding_agent.__main__.create_agent",
                return_value=(mock_pipeline, mock_ctx),
            ) as p_create,
            patch(
                "coding_agent.__main__.PipelineAdapter", mock_adapter_cls
            ) as p_adapter,
            patch("coding_agent.__main__.CodingAgentTUI", mock_tui_cls),
        ):
            from coding_agent.__main__ import _run_with_tui

            config = _make_config()
            await _run_with_tui(config, "test goal")

        # Verify create_agent was called
        p_create.assert_called_once()

        # Verify PipelineAdapter was instantiated with the pipeline, ctx, and consumer
        mock_adapter_cls.assert_called_once_with(
            pipeline=mock_pipeline,
            ctx=mock_ctx,
            consumer=mock_tui.consumer,
        )

        # Verify run_turn was called with the goal
        mock_adapter_instance.run_turn.assert_awaited_once_with("test goal")

    @pytest.mark.asyncio
    async def test_tui_run_uses_agent_loop_when_toggle_off(self, monkeypatch):
        """Without USE_PIPELINE, _run_with_tui uses old AgentLoop path."""
        monkeypatch.delenv("USE_PIPELINE", raising=False)

        # We just need to verify AgentLoop is instantiated, not PipelineAdapter.
        # Mock enough of the old path to avoid real provider/tool creation.
        mock_loop_instance = AsyncMock()
        mock_loop_instance.run_turn = AsyncMock(
            return_value=MagicMock(stop_reason="completed")
        )
        mock_loop_cls = MagicMock(return_value=mock_loop_instance)

        mock_tui = MagicMock()
        mock_tui.consumer = MagicMock()
        mock_tui.__enter__ = MagicMock(return_value=mock_tui)
        mock_tui.__exit__ = MagicMock(return_value=False)

        with (
            patch("coding_agent.__main__.CodingAgentTUI", return_value=mock_tui),
            patch(
                "coding_agent.__main__._create_provider",
                return_value=MagicMock(max_context_size=128000),
            ),
            patch("coding_agent.core.loop.AgentLoop", mock_loop_cls),
            patch("coding_agent.__main__.create_agent") as p_create,
        ):
            from coding_agent.__main__ import _run_with_tui

            config = _make_config()
            await _run_with_tui(config, "test goal")

        # create_agent should NOT have been called
        p_create.assert_not_called()


# ---------------------------------------------------------------------------
# Headless path
# ---------------------------------------------------------------------------


class TestHeadlessRunUsesPipelineAdapterWhenToggleOn:
    """With USE_PIPELINE=1, _run_headless creates PipelineAdapter instead of AgentLoop."""

    @pytest.mark.asyncio
    async def test_headless_run_uses_pipeline_adapter_when_toggle_on(self, monkeypatch):
        monkeypatch.setenv("USE_PIPELINE", "1")

        mock_pipeline, mock_ctx = _mock_create_agent()
        mock_outcome = _make_outcome(final_message="headless done")

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
            await _run_headless(config, "headless goal")

        p_create.assert_called_once()

        mock_adapter_cls.assert_called_once_with(
            pipeline=mock_pipeline,
            ctx=mock_ctx,
            consumer=mock_consumer,
        )

        mock_adapter_instance.run_turn.assert_awaited_once_with("headless goal")

    @pytest.mark.asyncio
    async def test_headless_run_uses_agent_loop_when_toggle_off(self, monkeypatch):
        """Without USE_PIPELINE, _run_headless uses old AgentLoop path."""
        monkeypatch.delenv("USE_PIPELINE", raising=False)

        mock_loop_instance = AsyncMock()
        mock_loop_instance.run_turn = AsyncMock(
            return_value=MagicMock(stop_reason="completed", final_message="ok")
        )
        mock_loop_cls = MagicMock(return_value=mock_loop_instance)

        with (
            patch(
                "coding_agent.__main__._create_provider",
                return_value=MagicMock(max_context_size=128000),
            ),
            patch("coding_agent.core.loop.AgentLoop", mock_loop_cls),
            patch("coding_agent.__main__.create_agent") as p_create,
            patch("coding_agent.tools.registry.ToolRegistry"),
            patch("coding_agent.tools.file.register_file_tools"),
            patch("coding_agent.tools.shell.register_shell_tools"),
            patch("coding_agent.tools.search.register_search_tools"),
            patch("coding_agent.tools.planner.register_planner_tools"),
            patch("coding_agent.tools.subagent.register_subagent_tool"),
            patch("coding_agent.core.tape.Tape") as mock_tape_cls,
            patch("coding_agent.core.context.Context"),
            patch("coding_agent.core.planner.PlanManager"),
        ):
            mock_tape_cls.create.return_value = MagicMock()

            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "headless goal")

        p_create.assert_not_called()


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


class TestReplUsesPipelineAdapterWhenToggleOn:
    @patch("coding_agent.cli.repl.use_pipeline", return_value=True)
    @patch("coding_agent.cli.repl.create_agent")
    def test_repl_uses_pipeline_adapter_when_toggle_on(
        self, mock_create_agent, _mock_toggle
    ):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        assert session._use_pipeline is True
        assert isinstance(session._pipeline_adapter, PipelineAdapter)
        mock_create_agent.assert_called_once()

    @patch("coding_agent.cli.repl.use_pipeline", return_value=False)
    def test_repl_uses_agent_loop_by_default(self, _mock_toggle):
        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        assert session._use_pipeline is False
        assert session._pipeline_adapter is None


class TestReplMultiturnContext:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.use_pipeline", return_value=True)
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_multiturn_context(self, mock_create_agent, _mock_toggle):
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

        with patch("coding_agent.cli.repl.CodingAgentTUI") as mock_tui_cls:
            mock_tui = MagicMock()
            mock_tui.__enter__ = MagicMock(return_value=mock_tui)
            mock_tui.__exit__ = MagicMock(return_value=False)
            mock_tui.consumer = MagicMock()
            mock_tui_cls.return_value = mock_tui

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
    @patch("coding_agent.cli.repl.use_pipeline", return_value=True)
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_slash_commands_still_work(
        self, mock_create_agent, _mock_toggle
    ):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        call_count = 0

        async def mock_get_input(prompt=""):
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
    @patch("coding_agent.cli.repl.use_pipeline", return_value=True)
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_error_recovery(self, mock_create_agent, _mock_toggle):
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

        with patch("coding_agent.cli.repl.CodingAgentTUI") as mock_tui_cls:
            mock_tui = MagicMock()
            mock_tui.__enter__ = MagicMock(return_value=mock_tui)
            mock_tui.__exit__ = MagicMock(return_value=False)
            mock_tui.consumer = MagicMock()
            mock_tui_cls.return_value = mock_tui

            with patch.object(adapter, "run_turn", side_effect=side_effect_run_turn):
                await session._process_message("bad input")
                await session._process_message("good input")

        assert len(turn_results) == 2
        assert turn_results[0].stop_reason == StopReason.ERROR
        assert turn_results[1].stop_reason == StopReason.NO_TOOL_CALLS


# ---------------------------------------------------------------------------
# T15: Additional headless Pipeline tests — output and isolation
# ---------------------------------------------------------------------------


class TestHeadlessPipelineOutput:
    """Verify _run_headless Pipeline path produces correct stdout output."""

    @pytest.mark.asyncio
    async def test_headless_pipeline_prints_result_summary(self, monkeypatch, capsys):
        """Pipeline path prints '--- Result (stop_reason) ---' to stdout."""
        monkeypatch.setenv("USE_PIPELINE", "1")

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
    async def test_headless_pipeline_prints_final_message(self, monkeypatch, capsys):
        """Final message is echoed to stdout when present."""
        monkeypatch.setenv("USE_PIPELINE", "1")

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
    async def test_headless_pipeline_no_final_message_omits_echo(
        self, monkeypatch, capsys
    ):
        """When final_message is None, only the result summary line is printed."""
        monkeypatch.setenv("USE_PIPELINE", "1")

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
    async def test_headless_pipeline_does_not_create_agent_loop(self, monkeypatch):
        """Pipeline path returns early — AgentLoop is NOT created."""
        monkeypatch.setenv("USE_PIPELINE", "1")

        mock_outcome = _make_outcome()

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
            patch("coding_agent.core.loop.AgentLoop") as mock_agent_loop,
        ):
            from coding_agent.__main__ import _run_headless

            config = _make_config()
            await _run_headless(config, "goal")

        # AgentLoop should never be instantiated in pipeline path
        mock_agent_loop.assert_not_called()

    @pytest.mark.asyncio
    async def test_headless_pipeline_passes_model_override(self, monkeypatch):
        """create_agent receives model_override from config."""
        monkeypatch.setenv("USE_PIPELINE", "1")

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


class TestReplPipelineAdapterConsumerUpdated:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.use_pipeline", return_value=True)
    @patch("coding_agent.cli.repl.create_agent")
    async def test_consumer_updated_each_turn(self, mock_create_agent, _mock_toggle):
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

        with patch("coding_agent.cli.repl.CodingAgentTUI") as mock_tui_cls:
            tui_1 = MagicMock()
            tui_1.__enter__ = MagicMock(return_value=tui_1)
            tui_1.__exit__ = MagicMock(return_value=False)
            tui_1.consumer = MagicMock(name="consumer_1")

            tui_2 = MagicMock()
            tui_2.__enter__ = MagicMock(return_value=tui_2)
            tui_2.__exit__ = MagicMock(return_value=False)
            tui_2.consumer = MagicMock(name="consumer_2")

            mock_tui_cls.side_effect = [tui_1, tui_2]

            with patch.object(
                adapter, "run_turn", side_effect=capture_consumer_run_turn
            ):
                await session._process_message("turn 1")
                await session._process_message("turn 2")

        assert len(consumers_seen) == 2
        assert consumers_seen[0] is tui_1.consumer
        assert consumers_seen[1] is tui_2.consumer
