"""Slash command system for interactive mode."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from coding_agent.cli.terminal_output import get_prompt_output, print_pt


def _command_output():
    return get_prompt_output()


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
    output = _command_output()
    print_pt("Available Commands:\n", output=output)
    for name, func in sorted(_COMMANDS.items()):
        desc = getattr(func, "_command_description", "")
        print_pt(f"  /{name} - {desc}", output=output)
    print_pt(output=output)
    print_pt("Shell Mode:\n", output=output)
    print_pt("  ! - Instantly enter bash mode (no Enter needed)", output=output)
    print_pt("  Escape or Backspace on empty prompt - Return to chat", output=output)
    print_pt(output=output)
    print_pt("Input:\n", output=output)
    print_pt("  Enter - New line (in multiline mode)", output=output)
    print_pt("  Alt+Enter - Submit message", output=output)
    print_pt("  Enter on empty line - Submit", output=output)
    print_pt("  Ctrl+C × 2 - Exit", output=output)
    print_pt(output=output)
    print_pt("Type your message normally to chat with the agent.\n", output=output)


@command("exit", "Exit the agent")
async def cmd_exit(args: list[str], context: dict[str, Any]) -> None:
    """Exit the REPL."""
    print_pt("Goodbye!", output=_command_output())
    context["should_exit"] = True


@command("quit", "Exit the agent (alias)")
async def cmd_quit(args: list[str], context: dict[str, Any]) -> None:
    """Exit the REPL."""
    await cmd_exit(args, context)


@command("clear", "Clear the screen")
async def cmd_clear(args: list[str], context: dict[str, Any]) -> None:
    """Clear the screen."""
    print("\033[2J\033[H", end="")


@command("plan", "Show current plan")
async def cmd_plan(args: list[str], context: dict[str, Any]) -> None:
    """Show current plan from planner."""
    planner = context.get("planner")
    if planner and planner.tasks:
        output = _command_output()
        print_pt("Current Plan:\n", output=output)
        print_pt(planner.to_text(), output=output)
    else:
        print_pt(
            "No active plan. Use todo_write to create one.", output=_command_output()
        )


@command("model", "Show or change model")
async def cmd_model(args: list[str], context: dict[str, Any]) -> None:
    """Show current model or change it."""
    if args:
        new_model = args[0]
        if len(new_model) < 2 or len(new_model) > 100:
            print_pt(f"Invalid model name: {new_model}", output=_command_output())
            return
        context["model"] = new_model
        output = _command_output()
        print_pt(f"Model changed to: {new_model}", output=output)
        print_pt("Note: Model change will take effect on next turn.", output=output)
    else:
        current = context.get("model", "unknown")
        print_pt(f"Current model: {current}", output=_command_output())


@command("tools", "List available tools")
async def cmd_tools(args: list[str], context: dict[str, Any]) -> None:
    """List available tools."""
    registry = context.get("tool_registry")
    if registry:
        output = _command_output()
        print_pt("Available Tools:\n", output=output)
        for name in sorted(registry.list_tools()):
            print_pt(f"  • {name}", output=output)
        print_pt(output=output)
    else:
        print_pt("No tool registry available", output=_command_output())


async def handle_command(input_text: str, context: dict[str, Any]) -> bool:
    """Handle a slash command.

    Args:
        input_text: Raw input starting with /
        context: Shared context dictionary

    Returns:
        True if command was handled, False otherwise
    """
    if not input_text.startswith("/"):
        return False

    # Parse command and args
    parts = input_text[1:].strip().split()
    if not parts:
        # Empty "/" command - show help
        await cmd_help([], context)
        return True

    cmd_name = parts[0].lower()
    args = parts[1:]

    if cmd_name in _COMMANDS:
        try:
            await _COMMANDS[cmd_name](args, context)
        except Exception as e:
            print_pt(f"Command error: {e}", output=_command_output())
        return True
    else:
        print_pt(
            f"Unknown command: /{cmd_name}. Type /help for available commands.",
            output=_command_output(),
        )
        return True


def get_command_completions() -> list[str]:
    """Get list of command names for autocompletion."""
    return [f"/{name}" for name in _COMMANDS.keys()]
