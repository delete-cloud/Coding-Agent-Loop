"""Tests for CLI commands."""

import pytest

from coding_agent.cli.commands import handle_command, get_command_completions


class TestCommands:
    @pytest.mark.asyncio
    async def test_help_command(self, capsys):
        context = {'should_exit': False}
        handled = await handle_command("/help", context)
        assert handled is True
        # Should not set should_exit
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_exit_command(self):
        context = {'should_exit': False}
        handled = await handle_command("/exit", context)
        assert handled is True
        assert context['should_exit'] is True
    
    @pytest.mark.asyncio
    async def test_quit_command(self):
        """Test that /quit is an alias for /exit."""
        context = {'should_exit': False}
        handled = await handle_command("/quit", context)
        assert handled is True
        assert context['should_exit'] is True
    
    @pytest.mark.asyncio
    async def test_clear_command(self):
        """Test /clear command."""
        context = {'should_exit': False}
        handled = await handle_command("/clear", context)
        assert handled is True
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_model_command_without_args(self):
        """Test /model command without arguments."""
        context = {'should_exit': False, 'model': 'gpt-4o'}
        handled = await handle_command("/model", context)
        assert handled is True
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_model_command_with_args(self):
        """Test /model command with arguments."""
        context = {'should_exit': False, 'model': 'gpt-4o'}
        handled = await handle_command("/model claude-3-opus", context)
        assert handled is True
        assert context['model'] == 'claude-3-opus'
    
    @pytest.mark.asyncio
    async def test_plan_command_no_planner(self):
        """Test /plan command without planner in context."""
        context = {'should_exit': False}
        handled = await handle_command("/plan", context)
        assert handled is True
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_tools_command_no_registry(self):
        """Test /tools command without tool registry."""
        context = {'should_exit': False}
        handled = await handle_command("/tools", context)
        assert handled is True
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_unknown_command(self, capsys):
        context = {'should_exit': False}
        handled = await handle_command("/unknown_xyz", context)
        assert handled is True  # Still handled (error message shown)
        assert context['should_exit'] is False
    
    @pytest.mark.asyncio
    async def test_not_a_command(self):
        context = {'should_exit': False}
        handled = await handle_command("hello world", context)
        assert handled is False  # Not a command
    
    @pytest.mark.asyncio
    async def test_empty_slash_shows_help(self, capsys):
        """Test that just '/' shows help (treated as help command)."""
        context = {'should_exit': False}
        handled = await handle_command("/", context)
        # Empty command now shows help and returns True (handled)
        assert handled is True
        assert context['should_exit'] is False  # Help doesn't exit
    
    def test_command_completions(self):
        completions = get_command_completions()
        assert '/help' in completions
        assert '/exit' in completions
        assert '/clear' in completions
        assert '/plan' in completions
        assert '/model' in completions
        assert '/tools' in completions
        assert '/quit' in completions
    
    def test_all_commands_have_descriptions(self):
        """Test that all registered commands have descriptions."""
        from coding_agent.cli.commands import _COMMANDS
        
        for name, func in _COMMANDS.items():
            desc = getattr(func, '_command_description', None)
            assert desc is not None, f"Command {name} is missing description"
