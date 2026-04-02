"""Main REPL loop for interactive mode."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from coding_agent.cli.commands import handle_command
from coding_agent.cli.input_handler import InputHandler
from coding_agent.cli.terminal_output import (
    get_prompt_output,
    print_pt,
    set_prompt_output,
)
from coding_agent.cli.bash_executor import BashExecutor
from coding_agent.core.config import Config
from coding_agent.__main__ import create_agent
from coding_agent.adapter import PipelineAdapter
from coding_agent.ui.stream_renderer import StreamingRenderer
from coding_agent.ui.rich_consumer import RichConsumer


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
        self._bash_executor = BashExecutor(
            cwd=str(config.repo) if config.repo else None
        )

        # Scrollback-based renderer — created once, persists across turns
        self._renderer = StreamingRenderer(console=console)
        self._consumer = RichConsumer(self._renderer)

        self._setup_agent()

    def _setup_agent(self):
        """Setup agent components."""
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
        self._pipeline_adapter = PipelineAdapter(
            pipeline=pipeline, ctx=pipeline_ctx, consumer=self._consumer
        )

    async def _ask_user_for_approval(self, question: str) -> bool:
        print_pt("\nApproval Required", output=get_prompt_output(sys.__stdout__))
        print_pt(question, output=get_prompt_output(sys.__stdout__))
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("[y/N] > ").strip().lower()
        )
        return response in ("y", "yes")

    # Proxy methods for WireConsumer protocol (used by subagent tool)
    async def emit(self, msg) -> None:
        """Proxy emit to current consumer."""
        await self._consumer.emit(msg)

    async def request_approval(self, req):
        """Proxy approval to current consumer."""
        return await self._consumer.request_approval(req)

    async def run(self):
        """Run the REPL loop."""
        prompt_output = get_prompt_output()
        set_prompt_output(prompt_output)

        print_pt("🤖 Coding Agent - Interactive Mode", output=prompt_output)
        print_pt(
            "Type /help for commands, ! to enter bash mode, or just chat.\n",
            output=prompt_output,
        )

        turn_count = 0

        while not self.context["should_exit"]:
            with patch_stdout():
                user_input = await self.input_handler.get_input(
                    prompt_builder=lambda shell: self.input_handler.build_prompt(
                        turn_count=turn_count,
                        shell_mode=shell,
                        cwd=str(self.config.repo) if self.config.repo else None,
                    ),
                )

            if user_input is None:
                break

            if not user_input:
                continue

            if self.input_handler.shell_mode:
                if user_input.strip() in {"exit", "quit"}:
                    self.input_handler.exit_shell_mode()
                    print_pt("Left bash mode.", output=prompt_output)
                    continue

                await self._bash_executor.execute(user_input)
                continue

            if user_input.startswith("/"):
                await handle_command(user_input, self.context)
                continue

            try:
                await self._process_message(user_input)
            except Exception as e:
                print_pt(f"\nError during agent execution: {e}", output=prompt_output)
                print_pt("You can continue with a new message.\n", output=prompt_output)
            turn_count += 1

        print_pt("\nSession ended.\n", output=prompt_output)

    async def _process_message(self, message: str):
        self._renderer.user_message(message)
        result = await self._pipeline_adapter.run_turn(message)

        if result.stop_reason == result.stop_reason.ERROR and result.error:
            print_pt(
                f"\nError: {result.error}\n", output=get_prompt_output(sys.__stdout__)
            )


async def run_repl(config: Config):
    """Entry point for REPL mode."""
    from agentkit.tracing import configure_tracing

    configure_tracing()
    session = InteractiveSession(config)
    await session.run()
