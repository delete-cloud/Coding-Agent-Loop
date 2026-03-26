# Interactive TUI Mode - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the coding-agent into an interactive TUI application like Kimi CLI or Codex CLI, where users can have continuous conversations, view history, and use slash commands.

**Architecture:** Convert from batch-mode (single goal → result) to REPL-mode (continuous session with history). Add `prompt-toolkit` for input handling (history, completion) and extend the TUI with a persistent input area at the bottom.

**Tech Stack:** Python 3.12+, rich (display), prompt-toolkit (input), asyncio

---

## Key Design Decisions

### UX Flow

**Current (Batch Mode):**
```
$ coding-agent run --goal "fix bug" → wait → result → exit
```

**Target (Interactive Mode):**
```
$ coding-agent                            # Default: interactive TUI
🤖 Coding Agent | Model: gpt-4 | Step: 0
─────────────────────────────────────────
[Chat Area - scrollable history]

> fix the bug in utils.py                 # User types
  [Agent streams response...]
  [Tool calls visualized...]

> /plan                                   # Slash command
  [Shows current plan]

> /exit                                   # Exit gracefully
```

### Mode Switching

| Command | Behavior |
|---------|----------|
| `coding-agent` | Default: interactive TUI |
| `coding-agent run --goal "x"` | Batch mode (headless) |
| `coding-agent run --goal "x" --tui` | Batch mode with TUI display |
| `coding-agent repl` | Explicit REPL mode (same as default) |

---

## File Map

```
coding-agent/
  src/coding_agent/
    cli/                       # NEW: CLI package
      __init__.py
      repl.py                  # REPL loop with prompt-toolkit
      commands.py              # Slash command handlers (/exit, /plan, etc.)
      history.py               # Session history management
    ui/
      interactive_tui.py       # NEW: InteractiveTUI with input area
      input_area.py            # NEW: Custom input widget using prompt-toolkit
      layout_manager.py        # Layout with persistent input at bottom
  tests/
    cli/
      test_repl.py
      test_commands.py
    ui/
      test_interactive_tui.py
```

---

## Task 1: Add prompt-toolkit Dependency

**Files:**
- Modify: `coding-agent/pyproject.toml`

- [ ] **Step 1: Add prompt-toolkit to dependencies**

```toml
dependencies = [
    "openai>=1.50.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
    "click>=8.0.0",
    "rich>=13.0.0",
    "prompt-toolkit>=3.0.0",  # NEW
]
```

- [ ] **Step 2: Install and commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv sync --all-extras
git add pyproject.toml uv.lock
git commit -m "chore(deps): add prompt-toolkit for interactive input"
```

---

## Task 2: Create Slash Commands System

**Files:**
- Create: `coding-agent/src/coding_agent/cli/__init__.py`
- Create: `coding-agent/src/coding_agent/cli/commands.py`

- [ ] **Step 1: Create command registry and handlers**

`coding-agent/src/coding_agent/cli/commands.py`:

```python
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
```

- [ ] **Step 2: Create CLI package init**

`coding-agent/src/coding_agent/cli/__init__.py`:

```python
"""CLI package for interactive and batch modes."""

from coding_agent.cli.commands import handle_command, get_command_completions

__all__ = ["handle_command", "get_command_completions"]
```

- [ ] **Step 3: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/cli/
git commit -m "feat(cli): add slash command system"
```

---

## Task 3: Create Interactive Input with prompt-toolkit

**Files:**
- Create: `coding-agent/src/coding_agent/cli/input_handler.py`

- [ ] **Step 1: Create prompt-toolkit input handler**

`coding-agent/src/coding_agent/cli/input_handler.py`:

```python
"""Interactive input handling with prompt-toolkit."""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from coding_agent.cli.commands import get_command_completions


class SlashCommandCompleter(Completer):
    """Completer for slash commands."""
    
    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith('/'):
            for cmd in get_command_completions():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


# Custom style for prompt
PROMPT_STYLE = Style.from_dict({
    'prompt': 'bold cyan',
    'input': 'white',
})


class InputHandler:
    """Handles interactive user input with history and completion."""
    
    def __init__(self):
        self.session = PromptSession(
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            style=PROMPT_STYLE,
        )
        self.bindings = KeyBindings()
        self._setup_bindings()
    
    def _setup_bindings(self):
        """Setup custom key bindings."""
        @self.bindings.add('c-c')
        def _(event):
            """Ctrl+C to cancel current input."""
            event.app.current_buffer.reset()
        
        @self.bindings.add('c-d')
        def _(event):
            """Ctrl+D to exit."""
            event.app.exit()
    
    async def get_input(self, prompt: str = "> ") -> str | None:
        """Get input from user.
        
        Returns:
            User input string, or None if user wants to exit
        """
        try:
            result = await self.session.prompt_async(
                prompt,
                key_bindings=self.bindings,
            )
            return result.strip()
        except (EOFError, KeyboardInterrupt):
            return None
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/cli/input_handler.py
git commit -m "feat(cli): add prompt-toolkit input handler with history"
```

---

## Task 4: Create REPL Loop

**Files:**
- Create: `coding-agent/src/coding_agent/cli/repl.py`

- [ ] **Step 1: Create main REPL loop**

`coding-agent/src/coding_agent/cli/repl.py`:

```python
"""Main REPL loop for interactive mode."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console

from coding_agent.cli.commands import handle_command
from coding_agent.cli.input_handler import InputHandler
from coding_agent.core.config import Config
from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Tape
from coding_agent.providers.openai_compat import OpenAICompatProvider
from coding_agent.tools.file import register_file_tools
from coding_agent.tools.planner import register_planner_tools
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.search import register_search_tools
from coding_agent.tools.shell import register_shell_tools
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.ui.rich_tui import CodingAgentTUI


console = Console()


class InteractiveSession:
    """Manages an interactive agent session."""
    
    def __init__(self, config: Config):
        self.config = config
        self.context: dict[str, Any] = {
            'should_exit': False,
            'model': config.model,
        }
        self.input_handler = InputHandler()
        self._setup_agent()
    
    def _setup_agent(self):
        """Setup agent components."""
        # Provider
        if self.config.provider == "anthropic":
            from coding_agent.providers.anthropic import AnthropicProvider
            self.provider = AnthropicProvider(
                model=self.config.model,
                api_key=self.config.api_key,
            )
        else:
            self.provider = OpenAICompatProvider(
                model=self.config.model,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )
        
        # Tools
        self.tools = ToolRegistry()
        register_file_tools(self.tools, repo_root=self.config.repo)
        register_shell_tools(self.tools, cwd=self.config.repo)
        register_search_tools(self.tools, repo_root=self.config.repo)
        
        self.planner = PlanManager()
        register_planner_tools(self.tools, self.planner)
        
        self.context['planner'] = self.planner
        self.context['tool_registry'] = self.tools
        
        # Tape
        self.tape = Tape.create(self.config.tape_dir)
        
        # System prompt
        self.system_prompt = (
            "You are a coding agent. You can read files, edit files, "
            "run shell commands, search the codebase, create task plans, "
            "and dispatch sub-agents for independent sub-tasks.\n\n"
            "Always create a plan (todo_write) before starting complex work. "
            "Update task status as you progress."
        )
    
    async def run(self):
        """Run the REPL loop."""
        console.print("\n[bold cyan]🤖 Coding Agent[/] - Interactive Mode")
        console.print("[dim]Type /help for commands, or just chat with the agent.[/]\n")
        
        # Register subagent (needs consumer, set up per-turn)
        register_subagent_tool(
            registry=self.tools,
            provider=self.provider,
            tape=self.tape,
            consumer=None,  # Will be set per-turn
            max_steps=self.config.subagent_max_steps,
            max_depth=self.config.max_subagent_depth,
        )
        
        turn_count = 0
        
        while not self.context['should_exit']:
            # Get user input
            user_input = await self.input_handler.get_input(
                prompt=f"[{turn_count}] > "
            )
            
            if user_input is None:
                # User pressed Ctrl+D or similar
                break
            
            if not user_input:
                continue
            
            # Check for slash commands
            if user_input.startswith('/'):
                await handle_command(user_input, self.context)
                continue
            
            # Process user message through agent with TUI
            await self._process_message(user_input)
            turn_count += 1
        
        console.print("\n[dim]Session ended.[/]\n")
    
    async def _process_message(self, message: str):
        """Process a user message through the agent."""
        # Create TUI for this turn
        tui = CodingAgentTUI(
            model_name=self.config.model,
            max_steps=self.config.max_steps,
        )
        
        # Update context with consumer
        self.context['consumer'] = tui.consumer
        
        # Context with plan
        ctx = Context(
            max_tokens=self.provider.max_context_size,
            system_prompt=self.system_prompt,
            planner=self.planner,
        )
        
        # Agent loop
        loop = AgentLoop(
            provider=self.provider,
            tools=self.tools,
            tape=self.tape,
            context=ctx,
            consumer=tui.consumer,
            max_steps=self.config.max_steps,
        )
        
        # Run with TUI display
        with tui:
            tui.add_user_message(message)
            result = await loop.run_turn(message)
        
        # Show result summary
        console.print(f"\n[dim]Completed: {result.stop_reason} | Steps: {result.steps_taken}[/]\n")


async def run_repl(config: Config):
    """Entry point for REPL mode."""
    session = InteractiveSession(config)
    await session.run()
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/cli/repl.py
git commit -m "feat(cli): add interactive REPL loop with TUI per turn"
```

---

## Task 5: Update Main CLI Entry Point

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`

- [ ] **Step 1: Add `repl` command and make it default**

Modify `coding-agent/src/coding_agent/__main__.py`:

```python
"""CLI entry point: python -m coding_agent"""

import click


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """Coding Agent CLI.
    
    Without subcommand: starts interactive REPL mode (default)
    """
    if ctx.invoked_subcommand is None:
        # Default to interactive REPL mode
        import asyncio
        from coding_agent.cli.repl import run_repl
        from coding_agent.core.config import load_config
        
        config = load_config()
        asyncio.run(run_repl(config))


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--provider", "provider_name", default="openai", type=click.Choice(["openai", "anthropic"]))
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"]))
@click.option("--tui", is_flag=True, help="Use Rich TUI interface (batch mode)")
def run(goal, repo, model, provider_name, base_url, api_key, max_steps, approval, tui):
    """Run agent on a goal (batch mode)."""
    import asyncio
    from coding_agent.core.config import Config
    
    config = Config(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode=approval,
    )
    
    if tui:
        asyncio.run(_run_with_tui(config, goal))
    else:
        asyncio.run(_run_headless(config, goal))


@main.command()
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--provider", "provider_name", default="openai", type=click.Choice(["openai", "anthropic"]))
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
def repl(repo, model, provider_name, base_url, api_key, max_steps):
    """Start interactive REPL mode (explicit)."""
    import asyncio
    from coding_agent.cli.repl import run_repl
    from coding_agent.core.config import Config
    
    config = Config(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode="yolo",
    )
    asyncio.run(run_repl(config))


# ... keep existing _run_with_tui and _run_headless implementations ...
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/__main__.py
git commit -m "feat(cli): make interactive REPL the default mode"
```

---

## Task 6: Add Tests

**Files:**
- Create: `coding-agent/tests/cli/test_commands.py`
- Create: `coding-agent/tests/cli/test_repl.py`

- [ ] **Step 1: Write command tests**

`coding-agent/tests/cli/test_commands.py`:

```python
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
    
    def test_command_completions(self):
        completions = get_command_completions()
        assert '/help' in completions
        assert '/exit' in completions
        assert '/clear' in completions
```

- [ ] **Step 2: Write REPL tests**

`coding-agent/tests/cli/test_repl.py`:

```python
"""Tests for REPL functionality."""

import pytest

from coding_agent.cli.input_handler import InputHandler


class TestInputHandler:
    def test_input_handler_creation(self):
        handler = InputHandler()
        assert handler is not None
        assert handler.session is not None
    
    @pytest.mark.asyncio
    async def test_get_input_mock(self, monkeypatch):
        """Test input with mocked prompt."""
        handler = InputHandler()
        
        # Mock the prompt_async to return test input
        async def mock_prompt(*args, **kwargs):
            return "test input"
        
        monkeypatch.setattr(handler.session, 'prompt_async', mock_prompt)
        
        result = await handler.get_input()
        assert result == "test input"
```

- [ ] **Step 3: Run tests and commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/cli/ -v
git add tests/cli/
git commit -m "test(cli): add REPL and command tests"
```

---

## Task 7: Final Integration and Documentation

**Files:**
- Modify: `coding-agent/README.md` (or create if not exists)

- [ ] **Step 1: Create/update README with usage examples**

Add to README:

```markdown
## Usage

### Interactive Mode (Default)

Start an interactive coding session:

```bash
# Just run coding-agent - starts interactive REPL
coding-agent

# Or explicitly
coding-agent repl

# Inside REPL:
> fix the bug in utils.py
> /plan                    # Show current plan
> /model gpt-4             # Change model
> /exit                    # Exit
```

### Batch Mode

Run a single task:

```bash
# Headless (for CI/scripts)
coding-agent run --goal "fix bug" --api-key $KEY

# With TUI display
coding-agent run --goal "fix bug" --api-key $KEY --tui
```

### Commands

In interactive mode, use slash commands:

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/exit` | Exit the agent |
| `/clear` | Clear the screen |
| `/plan` | Show current plan |
| `/model` | Show or change model |
| `/tools` | List available tools |
```

- [ ] **Step 2: Final test and commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ -v
git add README.md
git commit -m "docs: update README with interactive mode usage"
```

---

## Summary

| Task | Component | Files | LOC Est. |
|------|-----------|-------|----------|
| 1 | Add dependency | pyproject.toml | 5 |
| 2 | Slash commands | cli/commands.py | 120 |
| 3 | Input handler | cli/input_handler.py | 80 |
| 4 | REPL loop | cli/repl.py | 150 |
| 5 | CLI entry | __main__.py | 80 |
| 6 | Tests | tests/cli/ | 100 |
| 7 | Documentation | README.md | 50 |
| **Total** | | | **~585** |

Exit Criteria: 
- `coding-agent` alone starts interactive REPL mode
- `coding-agent run --goal "x"` works in batch mode
- Slash commands (/help, /exit, /plan, etc.) work
- Command history and completion work
