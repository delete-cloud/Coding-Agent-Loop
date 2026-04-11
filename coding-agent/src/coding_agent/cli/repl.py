"""Main REPL loop for interactive mode."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.console import Console

from coding_agent.cli.commands import handle_command
from coding_agent.cli.input_handler import InputHandler
from coding_agent.core.config import Config
from coding_agent.core.planner import PlanManager
from coding_agent.providers.openai_compat import OpenAICompatProvider
from coding_agent.tools.file import register_file_tools
from coding_agent.tools.planner import register_planner_tools
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.search import register_search_tools
from coding_agent.tools.shell import register_shell_tools
from coding_agent.ui.rich_tui import CodingAgentTUI
from coding_agent.__main__ import create_agent
from coding_agent.adapter import PipelineAdapter


console = Console()


class InteractiveSession:
    """Manages an interactive agent session."""

    def __init__(self, config: Config):
        self.config = config
        self.context: dict[str, Any] = {
            "should_exit": False,
            "model": config.model,
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
        self.tools = ToolRegistry(
            repo_root=self.config.repo,
            enable_cache=self.config.enable_cache,
            cache_size=self.config.cache_size,
        )
        register_file_tools(self.tools, repo_root=self.config.repo)
        register_shell_tools(self.tools, cwd=self.config.repo)
        register_search_tools(self.tools, repo_root=self.config.repo)

        self.planner = PlanManager()
        register_planner_tools(self.tools, self.planner)

        self.context["planner"] = self.planner
        self.context["tool_registry"] = self.tools

        self._current_consumer = None

        pipeline, pipeline_ctx = create_agent(
            api_key=str(self.config.api_key.get_secret_value())
            if self.config.api_key
            else None,
            model_override=self.config.model,
            provider_override=self.config.provider,
            base_url_override=self.config.base_url,
            workspace_root=self.config.repo,
            max_steps_override=self.config.max_steps,
            approval_mode_override=self.config.approval_mode,
        )
        if pipeline._directive_executor is not None:
            pipeline._directive_executor._ask_user = self._ask_user_for_approval
        self._pipeline_adapter = PipelineAdapter(pipeline=pipeline, ctx=pipeline_ctx)

    async def _ask_user_for_approval(self, question: str) -> bool:
        console.print("\n[yellow bold]⚠ Approval Required[/]")
        console.print(f"[yellow]{question}[/]")
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("[y/N] > ").strip().lower()
        )
        return response in ("y", "yes")

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
        from coding_agent.wire.protocol import ApprovalResponse

        return ApprovalResponse(
            session_id=req.session_id, request_id=req.request_id, approved=True
        )

    async def run(self):
        """Run the REPL loop."""
        console.print("\n[bold cyan]🤖 Coding Agent[/] - Interactive Mode")
        console.print("[dim]Type /help for commands, or just chat with the agent.[/]\n")

        # Register subagent tool - consumer will be set per-turn via context

        turn_count = 0

        while not self.context["should_exit"]:
            # Get user input
            user_input = await self.input_handler.get_input(prompt=f"[{turn_count}] > ")

            if user_input is None:
                # User pressed Ctrl+D or similar
                break

            if not user_input:
                continue

            # Check for slash commands
            if user_input.startswith("/"):
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
        tui = CodingAgentTUI(
            model_name=self.config.model,
            max_steps=self.config.max_steps,
        )

        self._current_consumer = tui.consumer
        self.context["consumer"] = tui.consumer

        self._pipeline_adapter._consumer = tui.consumer
        with tui:
            tui.add_user_message(message)
            result = await self._pipeline_adapter.run_turn(message)
        console.print(
            f"\n[dim]Completed: {result.stop_reason} | Steps: {result.steps_taken}[/]\n"
        )


async def run_repl(config: Config):
    """Entry point for REPL mode."""
    session = InteractiveSession(config)
    await session.run()
