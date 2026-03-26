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
        
        # Register subagent tool (consumer will be updated per-turn)
        self._current_consumer = None
        register_subagent_tool(
            registry=self.tools,
            provider=self.provider,
            tape=self.tape,
            consumer=self,  # Self as proxy - delegates to _current_consumer
            max_steps=self.config.subagent_max_steps,
            max_depth=self.config.max_subagent_depth,
        )
    
    # Proxy methods for WireConsumer protocol (used by subagent tool)
    async def emit(self, msg) -> None:
        """Proxy emit to current consumer."""
        if self._current_consumer:
            await self._current_consumer.emit(msg)
    
    async def request_approval(self, req):
        """Proxy approval to current consumer."""
        if self._current_consumer:
            return await self._current_consumer.request_approval(req)
        # Default: auto-approve
        from coding_agent.wire import ApprovalResponse
        return ApprovalResponse(call_id=req.call_id, decision="approve", scope="once")
    
    async def run(self):
        """Run the REPL loop."""
        console.print("\n[bold cyan]🤖 Coding Agent[/] - Interactive Mode")
        console.print("[dim]Type /help for commands, or just chat with the agent.[/]\n")
        
        # Register subagent tool - consumer will be set per-turn via context
        
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
            try:
                await self._process_message(user_input)
            except Exception as e:
                console.print(f"\n[red]Error during agent execution:[/] {e}")
                console.print("[dim]You can continue with a new message.[/]\n")
            turn_count += 1
        
        console.print("\n[dim]Session ended.[/]\n")
    
    async def _process_message(self, message: str):
        """Process a user message through the agent."""
        # Create TUI for this turn
        tui = CodingAgentTUI(
            model_name=self.config.model,
            max_steps=self.config.max_steps,
        )
        
        # Set current consumer for subagent proxy
        self._current_consumer = tui.consumer
        
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
