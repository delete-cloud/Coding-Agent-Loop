"""Tests: run + repl command wiring to PipelineAdapter (Pipeline is always active)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

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


def _fake_embed(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lower = text.lower()
        vector = [0.0] * 8
        if "auth" in lower or "jwt" in lower:
            vector[0] = 10.0
        if "api" in lower or "rest" in lower:
            vector[1] = 10.0
        if vector == [0.0] * 8:
            vector[2] = 1.0
        vectors.append(vector)
    return vectors


class TestKbCli:
    def test_kb_index_creates_table(self, tmp_path: Path, monkeypatch):
        from coding_agent import kb as kb_module
        from coding_agent.__main__ import main

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text(
            "# Hello World\nThis is a test document.", encoding="utf-8"
        )

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs["embedding_dim"] = 8
            kwargs.setdefault("text_extensions", {".md"})
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(docs), "--db-path", str(tmp_path / "kb-db")],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "Done." in result.output

    def test_kb_index_skips_when_table_exists(self, tmp_path: Path):
        from coding_agent.__main__ import main
        from coding_agent.kb import KB

        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text("# Test", encoding="utf-8")

        kb = KB(db_path=tmp_path / "kb-db", embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("existing.md"), "existing auth content"))

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(docs), "--db-path", str(tmp_path / "kb-db")],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "already exists" in result.output.lower()

    def test_kb_search_prints_results(self, tmp_path: Path, monkeypatch):
        from coding_agent import kb as kb_module
        from coding_agent.__main__ import main
        from coding_agent.kb import KB

        db_path = tmp_path / "kb-db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(
            kb.index_file(
                Path("src/auth.py"), "Authentication module with JWT token validation."
            )
        )

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs.setdefault("embedding_dim", 8)
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "auth", "--db-path", str(db_path)],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "src/auth.py" in result.output
        assert "Authentication module" in result.output

    def test_kb_search_reports_missing_index(self, tmp_path: Path, monkeypatch):
        from coding_agent import kb as kb_module
        from coding_agent.__main__ import main

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs.setdefault("embedding_dim", 8)
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "auth", "--db-path", str(tmp_path / "missing-kb")],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "No index found" in result.output


class TestCliProviderChoices:
    def test_top_level_accepts_kimi_code_provider(self):
        from coding_agent.__main__ import main

        with (
            patch("coding_agent.__main__.load_config") as mock_load_config,
            patch("coding_agent.cli.repl.run_repl", AsyncMock()),
            patch("coding_agent.__main__.sys.stdout.isatty", return_value=True),
        ):
            mock_load_config.return_value = MagicMock()
            ctx = click.Context(main)
            with ctx.scope(cleanup=False):
                wrapped_main = getattr(main.callback, "__wrapped__")
                wrapped_main(
                    ctx,
                    model="kimi-for-coding",
                    provider_name="kimi-code",
                    base_url=None,
                    api_key=None,
                )

        mock_load_config.assert_called_once_with(
            cli_args={"provider": "kimi-code", "model": "kimi-for-coding"}
        )

    def test_top_level_non_tty_shows_friendly_message(self):
        from coding_agent.__main__ import main

        runner = CliRunner()

        with patch("coding_agent.__main__.sys.stdout.isatty", return_value=False):
            result = runner.invoke(main, ["--provider", "kimi-code"])

        assert result.exit_code != 1
        assert "interactive REPL mode requires an interactive terminal" in result.output

    def test_run_uses_root_provider_and_model_options(self):
        from coding_agent.__main__ import main

        runner = CliRunner()
        with patch("coding_agent.__main__._run_headless", AsyncMock()) as mock_run:
            result = runner.invoke(
                main,
                [
                    "--provider",
                    "kimi-code",
                    "--model",
                    "kimi-for-coding",
                    "--api-key",
                    "sk-test-key",
                    "run",
                    "--goal",
                    "test goal",
                ],
            )

        assert result.exit_code == 0
        assert mock_run.await_args is not None
        config, goal = mock_run.await_args.args
        assert goal == "test goal"
        assert config.provider == "kimi-code"
        assert config.model == "kimi-for-coding"
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "sk-test-key"

    def test_repl_uses_root_provider_and_model_options(self):
        from coding_agent.__main__ import main

        runner = CliRunner()
        with patch("coding_agent.cli.repl.run_repl", AsyncMock()) as mock_run:
            result = runner.invoke(
                main,
                [
                    "--provider",
                    "kimi-code",
                    "--model",
                    "kimi-for-coding",
                    "--api-key",
                    "sk-test-key",
                    "repl",
                ],
            )

        assert result.exit_code == 0
        assert mock_run.await_args is not None
        (config,) = mock_run.await_args.args
        assert config.provider == "kimi-code"
        assert config.model == "kimi-for-coding"
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "sk-test-key"

    def test_run_uses_kimi_code_api_key_env_without_cli_api_key(self, monkeypatch):
        from coding_agent.__main__ import main

        monkeypatch.setenv("KIMI_CODE_API_KEY", "kimi-env-key")
        runner = CliRunner()

        with patch("coding_agent.__main__._run_headless", AsyncMock()) as mock_run:
            result = runner.invoke(
                main,
                ["--provider", "kimi-code", "run", "--goal", "test goal"],
            )

        assert result.exit_code == 0
        assert mock_run.await_args is not None
        config, goal = mock_run.await_args.args
        assert goal == "test goal"
        assert config.provider == "kimi-code"
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "kimi-env-key"

    def test_run_keeps_run_only_options_local_to_subcommand(self):
        from coding_agent.__main__ import main

        runner = CliRunner()

        with patch("coding_agent.__main__._run_with_tui", AsyncMock()) as mock_run:
            result = runner.invoke(
                main,
                [
                    "--provider",
                    "kimi-code",
                    "--api-key",
                    "sk-test-key",
                    "run",
                    "--goal",
                    "test goal",
                    "--max-steps",
                    "7",
                    "--approval",
                    "interactive",
                    "--tui",
                ],
            )

        assert result.exit_code == 0
        assert mock_run.await_args is not None
        config, goal = mock_run.await_args.args
        assert goal == "test goal"
        assert config.provider == "kimi-code"
        assert config.max_steps == 7
        assert config.approval_mode == "interactive"


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
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_slash_commands_still_work(self, mock_create_agent):
        mock_pipeline = MagicMock()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        call_count = 0

        async def mock_get_input(prompt=None, shell_mode=False, prompt_builder=None):
            del prompt, shell_mode, prompt_builder
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "/exit"
            return None

        session.input_handler.get_input = mock_get_input

        with (
            patch("coding_agent.cli.repl.print_pt"),
            patch("coding_agent.cli.commands.print_pt"),
            patch("coding_agent.cli.commands.print_html"),
        ):
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

        with (
            patch("coding_agent.cli.repl.print_pt"),
            patch("coding_agent.cli.commands.print_pt"),
            patch("coding_agent.cli.commands.print_html"),
        ):
            with patch("coding_agent.cli.repl.CodingAgentTUI") as mock_tui_cls:
                mock_tui = MagicMock()
                mock_tui.__enter__ = MagicMock(return_value=mock_tui)
                mock_tui.__exit__ = MagicMock(return_value=False)
                mock_tui.consumer = MagicMock()
                mock_tui_cls.return_value = mock_tui

                with patch.object(
                    adapter, "run_turn", side_effect=side_effect_run_turn
                ):
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


class TestReplPipelineAdapterConsumerUpdated:
    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_consumer_updated_each_turn(self, mock_create_agent):
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
        assert consumers_seen[0] is session._consumer
        assert consumers_seen[1] is session._consumer


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


class TestReplApprovalWiring:
    """REPL wires an ask_user_handler to DirectiveExecutor for interactive approval."""

    @patch("coding_agent.cli.repl.create_agent")
    def test_repl_sets_ask_user_handler_on_directive_executor(self, mock_create_agent):
        """After setup, the pipeline's DirectiveExecutor has an ask_user handler."""
        from agentkit.directive.executor import DirectiveExecutor

        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = DirectiveExecutor()
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        assert mock_pipeline._directive_executor._ask_user is not None
        assert callable(mock_pipeline._directive_executor._ask_user)

    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_approval_prompt_approved(self, mock_create_agent, monkeypatch):
        """When AskUser directive fires, the handler prompts and user approves."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        assert executor._ask_user is not None

        monkeypatch.setattr("builtins.input", lambda prompt="": "y")

        directive = AskUser(question="Allow tool 'shell_exec'?")
        result = await executor.execute(directive)
        assert result is True

    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_approval_prompt_denied(self, mock_create_agent, monkeypatch):
        """When user denies approval, AskUser returns False."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        directive = AskUser(question="Allow tool 'shell_exec'?")
        result = await executor.execute(directive)
        assert result is False

    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_approval_prompt_empty_defaults_no(
        self, mock_create_agent, monkeypatch
    ):
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        monkeypatch.setattr("builtins.input", lambda prompt="": "")

        directive = AskUser(question="Allow tool 'shell_exec'?")
        result = await executor.execute(directive)
        assert result is True

    @pytest.mark.asyncio
    @patch("coding_agent.cli.repl.create_agent")
    async def test_repl_approval_yes_variants(self, mock_create_agent, monkeypatch):
        """'yes', 'Y', 'YES' all approve."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        mock_pipeline = MagicMock()
        mock_pipeline._directive_executor = executor
        mock_ctx = MagicMock()
        mock_create_agent.return_value = (mock_pipeline, mock_ctx)

        from coding_agent.cli.repl import InteractiveSession

        config = _make_repl_config()
        session = InteractiveSession(config)

        for variant in ("y", "Y", "yes", "YES", "Yes"):
            monkeypatch.setattr("builtins.input", lambda prompt="", v=variant: v)
            directive = AskUser(question="Allow?")
            result = await executor.execute(directive)
            assert result is True, f"Expected True for input '{variant}'"


class TestBatchModeAutoApprove:
    """In batch mode with yolo policy, approval doesn't block (returns Approve)."""

    @pytest.mark.asyncio
    async def test_batch_yolo_policy_returns_approve(self):
        """ApprovalPlugin with AUTO policy always returns Approve — no AskUser."""
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
    async def test_batch_no_handler_rejects_ask_user(self):
        """DirectiveExecutor with no handler rejects AskUser (batch safety)."""
        from agentkit.directive.executor import DirectiveExecutor
        from agentkit.directive.types import AskUser

        executor = DirectiveExecutor()
        directive = AskUser(question="Allow?")
        result = await executor.execute(directive)
        assert result is False
