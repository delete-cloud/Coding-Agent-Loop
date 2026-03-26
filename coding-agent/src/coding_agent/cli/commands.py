"""Slash command system for interactive mode."""

from __future__ import annotations

import sys
from typing import Any, Callable, Coroutine

from rich.console import Console

# Global console for command output
console = Console()

# Command registry: name -> handler
_COMMANDS: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}


def command(name: str, description: str = ""):
    """Decorator to register a slash command."""
    def decorator(func: Callable[..., Coroutine[Any, Any, None]]):
        func._command_name = name
        func._command_description = description
        _COMMANDS[name] = func
        return func
    return decorator


@command("help", "Show available commands")
async def cmd_help(args: list[str], context: dict[str, Any]) -> None:
    """Show help message."""
    console.print("\n[bold cyan]Available Commands:[/]\n")
    for name, func in sorted(_COMMANDS.items()):
        desc = getattr(func, '_command_description', '')
        console.print(f"  [bold]/{name}[/] - {desc}")
    console.print("\nType your message normally to chat with the agent.\n")


@command("exit", "Exit the agent")
async def cmd_exit(args: list[str], context: dict[str, Any]) -> None:
    """Exit the REPL."""
    console.print("[dim]Goodbye![/]")
    context['should_exit'] = True


@command("quit", "Exit the agent (alias)")
async def cmd_quit(args: list[str], context: dict[str, Any]) -> None:
    """Exit the REPL."""
    await cmd_exit(args, context)


@command("clear", "Clear the screen")
async def cmd_clear(args: list[str], context: dict[str, Any]) -> None:
    """Clear the screen."""
    console.clear()


@command("plan", "Show current plan")
async def cmd_plan(args: list[str], context: dict[str, Any]) -> None:
    """Show current plan from planner."""
    planner = context.get('planner')
    if planner and planner.tasks:
        console.print("\n[bold]Current Plan:[/]\n")
        console.print(planner.to_text())
    else:
        console.print("[dim]No active plan. Use todo_write to create one.[/]")


@command("model", "Show or change model")
async def cmd_model(args: list[str], context: dict[str, Any]) -> None:
    """Show current model or change it."""
    if args:
        new_model = args[0]
        context['model'] = new_model
        console.print(f"[green]Model changed to:[/] {new_model}")
    else:
        current = context.get('model', 'unknown')
        console.print(f"[dim]Current model:[/] {current}")


@command("tools", "List available tools")
async def cmd_tools(args: list[str], context: dict[str, Any]) -> None:
    """List available tools."""
    registry = context.get('tool_registry')
    if registry:
        console.print("\n[bold]Available Tools:[/]\n")
        for name in sorted(registry.list_tools()):
            console.print(f"  • {name}")
        console.print()
    else:
        console.print("[red]No tool registry available[/]")


async def handle_command(input_text: str, context: dict[str, Any]) -> bool:
    """Handle a slash command.
    
    Args:
        input_text: Raw input starting with /
        context: Shared context dictionary
        
    Returns:
        True if command was handled, False otherwise
    """
    if not input_text.startswith('/'):
        return False
    
    # Parse command and args
    parts = input_text[1:].strip().split()
    if not parts:
        return False
    
    cmd_name = parts[0].lower()
    args = parts[1:]
    
    if cmd_name in _COMMANDS:
        try:
            await _COMMANDS[cmd_name](args, context)
        except Exception as e:
            console.print(f"[red]Command error:[/] {e}")
        return True
    else:
        console.print(f"[red]Unknown command:[/] /{cmd_name}. Type /help for available commands.")
        return True


def get_command_completions() -> list[str]:
    """Get list of command names for autocompletion."""
    return [f"/{name}" for name in _COMMANDS.keys()]
