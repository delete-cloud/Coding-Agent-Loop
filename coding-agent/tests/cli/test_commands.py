"""Tests for CLI commands."""

from io import StringIO

import pytest
from agentkit.tools.decorator import tool
from agentkit.tools.registry import ToolRegistry
from prompt_toolkit.output.defaults import create_output

from coding_agent.cli.commands import (
    get_command_completions,
    get_commands_with_descriptions,
    handle_command,
)


class TestCommands:
    @pytest.mark.asyncio
    async def test_help_command(self, capsys):
        context = {"should_exit": False}
        handled = await handle_command("/help", context)
        assert handled is True
        # Should not set should_exit
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_help_command_does_not_emit_ansi_sequences(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        context = {"should_exit": False}
        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module,
            "_prompt_output",
            create_output(stdout=buf),
        )

        await handle_command("/help", context)

        output = buf.getvalue()
        assert "Available Commands:" in output
        assert "\x1b[" not in output

    @pytest.mark.asyncio
    async def test_exit_command(self):
        context = {"should_exit": False}
        handled = await handle_command("/exit", context)
        assert handled is True
        assert context["should_exit"] is True

    @pytest.mark.asyncio
    async def test_quit_command(self):
        """Test that /quit is an alias for /exit."""
        context = {"should_exit": False}
        handled = await handle_command("/quit", context)
        assert handled is True
        assert context["should_exit"] is True

    @pytest.mark.asyncio
    async def test_clear_command(self):
        """Test /clear command."""
        context = {"should_exit": False}
        handled = await handle_command("/clear", context)
        assert handled is True
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_model_command_without_args(self):
        """Test /model command without arguments."""
        context = {"should_exit": False, "model": "gpt-4o"}
        handled = await handle_command("/model", context)
        assert handled is True
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_model_command_with_args(self):
        """Test /model command with arguments."""
        context = {"should_exit": False, "model": "gpt-4o"}
        handled = await handle_command("/model claude-3-opus", context)
        assert handled is True
        assert context["model"] == "claude-3-opus"

    @pytest.mark.asyncio
    async def test_plan_command_no_planner(self):
        """Test /plan command without planner in context."""
        context = {"should_exit": False}
        handled = await handle_command("/plan", context)
        assert handled is True
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_tools_command_no_registry(self):
        """Test /tools command without tool registry."""
        context = {"should_exit": False}
        handled = await handle_command("/tools", context)
        assert handled is True
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_tools_command_lists_registered_tool_names(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        @tool(description="Alpha test tool.")
        def alpha_tool() -> str:
            return "ok"

        @tool(description="Beta test tool.")
        def beta_tool() -> str:
            return "ok"

        registry = ToolRegistry()
        registry.register(beta_tool)
        registry.register(alpha_tool)

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )

        context = {"should_exit": False, "tool_registry": registry}

        handled = await handle_command("/tools", context)

        assert handled is True
        output = buf.getvalue()
        assert "Available Tools:" in output
        assert "alpha_tool" in output
        assert "beta_tool" in output
        assert output.index("alpha_tool") < output.index("beta_tool")

    @pytest.mark.asyncio
    async def test_unknown_command(self, capsys):
        context = {"should_exit": False}
        handled = await handle_command("/unknown_xyz", context)
        assert handled is True  # Still handled (error message shown)
        assert context["should_exit"] is False

    @pytest.mark.asyncio
    async def test_not_a_command(self):
        context = {"should_exit": False}
        handled = await handle_command("hello world", context)
        assert handled is False  # Not a command

    @pytest.mark.asyncio
    async def test_empty_slash_shows_help(self, capsys):
        """Test that just '/' shows help (treated as help command)."""
        context = {"should_exit": False}
        handled = await handle_command("/", context)
        # Empty command now shows help and returns True (handled)
        assert handled is True
        assert context["should_exit"] is False  # Help doesn't exit

    def test_command_completions(self):
        completions = get_command_completions()
        assert "/help" in completions
        assert "/exit" in completions
        assert "/clear" in completions
        assert "/plan" in completions
        assert "/model" in completions
        assert "/tools" in completions
        assert "/quit" in completions
        assert completions == sorted(completions)

    def test_commands_with_descriptions_match_sorted_completions(self):
        completions = get_command_completions()
        commands_with_descriptions = get_commands_with_descriptions()

        assert [name for name, _ in commands_with_descriptions] == completions
        assert all(description for _, description in commands_with_descriptions)

    def test_cli_package_exports_commands_with_descriptions(self):
        from coding_agent import cli

        assert "get_commands_with_descriptions" in cli.__all__
        assert cli.get_commands_with_descriptions() == get_commands_with_descriptions()

    def test_all_commands_have_descriptions(self):
        from coding_agent.cli.commands import _COMMANDS

        for name, func in _COMMANDS.items():
            desc = getattr(func, "_command_description", None)
            assert desc is not None, f"Command {name} is missing description"

    @pytest.mark.asyncio
    async def test_skill_command_no_plugin(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False}
        handled = await handle_command("/skill", context)
        assert handled is True
        assert "not enabled" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_mcp_command_no_plugin(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False}
        handled = await handle_command("/mcp", context)
        assert handled is True
        assert "not enabled" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_help_output_has_commands_header(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False}
        await handle_command("/help", context)
        output = buf.getvalue()
        assert "Available Commands" in output
        assert "/help" in output
        assert "/exit" in output

    @pytest.mark.asyncio
    async def test_model_shows_current_model(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False, "model": "claude-opus-4"}
        await handle_command("/model", context)
        assert "claude-opus-4" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_model_command_escapes_html_like_input(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False, "model": "gpt-4o"}

        handled = await handle_command("/model <danger>&name", context)

        assert handled is True
        output = buf.getvalue()
        assert "<danger>&name" in output
        assert "Command error" not in output

    @pytest.mark.asyncio
    async def test_unknown_command_escapes_html_like_input(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False}

        handled = await handle_command("/<bad>&cmd", context)

        assert handled is True
        output = buf.getvalue()
        assert "Unknown command" in output
        assert "/<bad>&cmd" in output
        assert "Command error" not in output

    @pytest.mark.asyncio
    async def test_skill_command_escapes_dynamic_values(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        class FakeSkillsPlugin:
            active_skill_name = "<active>&skill"

            def list_skills_with_descriptions(self):
                return [
                    ("<active>&skill", "desc with <b>tag</b> & more"),
                    ("plain", "plain desc"),
                ]

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"should_exit": False, "skills_plugin": FakeSkillsPlugin()}

        handled = await handle_command("/skill", context)

        assert handled is True
        output = buf.getvalue()
        assert "<active>&skill" in output
        assert "desc with <b>tag</b> & more" in output
        assert "Command error" not in output


class TestSkillColorHighlighting:
    @pytest.mark.asyncio
    async def test_inactive_skill_name_has_color_tag(self, monkeypatch):
        import coding_agent.cli.commands as commands_module

        class FakeSkillsPlugin:
            active_skill_name = None

            def list_skills_with_descriptions(self):
                return [("my-skill", "A test skill")]

        html_calls: list[str] = []
        original_print_html = commands_module.print_html

        def capture_print_html(html_str, **kwargs):
            html_calls.append(html_str)
            original_print_html(html_str, **kwargs)

        monkeypatch.setattr(commands_module, "print_html", capture_print_html)

        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )

        context = {"should_exit": False, "skills_plugin": FakeSkillsPlugin()}
        await handle_command("/skill", context)

        skill_line = [h for h in html_calls if "my-skill" in h]
        assert len(skill_line) == 1
        assert "<ansiyellow>" in skill_line[0]
        assert "my-skill" in skill_line[0]

    @pytest.mark.asyncio
    async def test_active_skill_name_uses_cyan(self, monkeypatch):
        import coding_agent.cli.commands as commands_module

        class FakeSkillsPlugin:
            active_skill_name = "active-skill"

            def list_skills_with_descriptions(self):
                return [("active-skill", "An active skill")]

        html_calls: list[str] = []
        original_print_html = commands_module.print_html

        def capture_print_html(html_str, **kwargs):
            html_calls.append(html_str)
            original_print_html(html_str, **kwargs)

        monkeypatch.setattr(commands_module, "print_html", capture_print_html)

        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )

        context = {"should_exit": False, "skills_plugin": FakeSkillsPlugin()}
        await handle_command("/skill", context)

        skill_line = [h for h in html_calls if "active-skill" in h and "active" in h]
        assert len(skill_line) >= 1
        assert "<ansicyan>" in skill_line[0]

    @pytest.mark.asyncio
    async def test_description_uses_dim_color(self, monkeypatch):
        import coding_agent.cli.commands as commands_module

        class FakeSkillsPlugin:
            active_skill_name = None

            def list_skills_with_descriptions(self):
                return [("test-skill", "Test description")]

        html_calls: list[str] = []
        original_print_html = commands_module.print_html

        def capture_print_html(html_str, **kwargs):
            html_calls.append(html_str)
            original_print_html(html_str, **kwargs)

        monkeypatch.setattr(commands_module, "print_html", capture_print_html)

        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )

        context = {"should_exit": False, "skills_plugin": FakeSkillsPlugin()}
        await handle_command("/skill", context)

        skill_line = [h for h in html_calls if "Test description" in h]
        assert len(skill_line) == 1
        assert "<ansibrightblack>" in skill_line[0]


class TestThinkingCommand:
    @pytest.mark.asyncio
    async def test_thinking_no_args_shows_current_state(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {}
        await handle_command("/thinking", context)
        output = buf.getvalue()
        assert "on" in output.lower()
        assert "medium" in output.lower()

    @pytest.mark.asyncio
    async def test_thinking_on_sets_enabled(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"thinking_enabled": False}
        await handle_command("/thinking on", context)
        assert context["thinking_enabled"] is True

    @pytest.mark.asyncio
    async def test_thinking_off_sets_disabled(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"thinking_enabled": True}
        await handle_command("/thinking off", context)
        assert context["thinking_enabled"] is False

    @pytest.mark.asyncio
    async def test_thinking_effort_low(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {}
        await handle_command("/thinking effort low", context)
        assert context["thinking_effort"] == "low"

    @pytest.mark.asyncio
    async def test_thinking_effort_medium(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {}
        await handle_command("/thinking effort medium", context)
        assert context["thinking_effort"] == "medium"

    @pytest.mark.asyncio
    async def test_thinking_effort_high(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {}
        await handle_command("/thinking effort high", context)
        assert context["thinking_effort"] == "high"

    @pytest.mark.asyncio
    async def test_thinking_effort_invalid_shows_error(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {"thinking_effort": "medium"}
        await handle_command("/thinking effort invalid", context)
        assert context["thinking_effort"] == "medium"
        output = buf.getvalue()
        assert "low" in output and "medium" in output and "high" in output

    @pytest.mark.asyncio
    async def test_thinking_garbage_shows_usage(self, monkeypatch):
        import coding_agent.cli.terminal_output as terminal_output_module

        buf = StringIO()
        monkeypatch.setattr(
            terminal_output_module, "_prompt_output", create_output(stdout=buf)
        )
        context = {}
        await handle_command("/thinking garbage", context)
        output = buf.getvalue()
        assert "usage" in output.lower() or "on" in output.lower()
