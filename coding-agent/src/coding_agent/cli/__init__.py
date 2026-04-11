"""CLI package for interactive and batch modes."""

from coding_agent.cli.commands import (
    get_command_completions,
    get_commands_with_descriptions,
    handle_command,
)

__all__ = [
    "handle_command",
    "get_command_completions",
    "get_commands_with_descriptions",
]
