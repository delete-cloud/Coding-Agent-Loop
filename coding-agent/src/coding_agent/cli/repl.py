"""Main REPL loop for interactive mode."""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import Any

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from coding_agent.cli.commands import handle_command
from coding_agent.cli.input_handler import InputHandler, expand_pasted_refs
from coding_agent.cli.terminal_output import (
    get_prompt_output,
    print_pt,
    set_prompt_output,
)
from coding_agent.cli.bash_executor import BashExecutor
from coding_agent.core.config import Config
from coding_agent.adapter import PipelineAdapter
from coding_agent.ui.stream_renderer import StreamingRenderer
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI
from coding_agent.ui.status_footer import StatusFooter
from coding_agent.ui.session_manager import SessionManager


console = Console(force_terminal=True, soft_wrap=False)


def create_agent(*args: Any, **kwargs: Any):
    return importlib.import_module("coding_agent.app").create_agent(*args, **kwargs)


class InteractiveSession:
    """Manages an interactive agent session."""

    def __init__(self, config: Config):
        self.config = config
        self._session_manager = SessionManager()
        self.context: dict[str, Any] = {
            "should_exit": False,
            "model": config.model,
            "thinking_enabled": True,
            "thinking_effort": "medium",
            "session_manager": self._session_manager,
        }
        self.input_handler = InputHandler()
        self._bash_executor = BashExecutor(
            cwd=str(config.repo) if config.repo else None
        )

        # Scrollback-based renderer — created once, persists across turns
        self._renderer = StreamingRenderer(console=console, enhanced_boundaries=True)
        self._consumer = RichConsumer(
            self._renderer,
            thinking_enabled=lambda: bool(self.context.get("thinking_enabled", True)),
            thinking_effort=lambda: str(self.context.get("thinking_effort", "medium")),
            on_status=self._handle_status_update,
        )
        self._footer = StatusFooter(console=console)

        self._setup_agent()

    def _refresh_command_context_from_pipeline_ctx(self, pipeline_ctx: Any) -> None:
        self.context["pipeline_ctx"] = pipeline_ctx
        self.context["tool_registry"] = pipeline_ctx.config.get("tool_registry")
        if "skills_plugin" in pipeline_ctx.config:
            self.context["skills_plugin"] = pipeline_ctx.config["skills_plugin"]
        else:
            self.context.pop("skills_plugin", None)
        if "mcp_plugin" in pipeline_ctx.config:
            self.context["mcp_plugin"] = pipeline_ctx.config["mcp_plugin"]
        else:
            self.context.pop("mcp_plugin", None)

    def _format_status_text(self, snapshot: dict[str, Any]) -> str:
        phase_icons = {
            "thinking": "⠋",
            "streaming": "▸",
            "tool": "⚡",
            "idle": "—",
        }
        phase = str(snapshot.get("phase", "idle"))
        model_name = str(snapshot.get("model_name") or self.context.get("model", ""))
        context_percent = float(snapshot.get("context_percent", 0.0) or 0.0)
        tokens_in = int(snapshot.get("tokens_in", 0) or 0)
        tokens_out = int(snapshot.get("tokens_out", 0) or 0)
        elapsed = int(float(snapshot.get("elapsed_seconds", 0.0) or 0.0))
        parts = [f"{phase_icons.get(phase, '—')} {model_name}".strip()]
        if context_percent > 0:
            parts.append(f"{context_percent:.0f}%")
        parts.append(f"{tokens_in}↑ {tokens_out}↓")
        parts.append(f"{elapsed}s")
        return " | ".join(parts)

    def _handle_status_update(self, snapshot: dict[str, Any]) -> None:
        status_text = self._format_status_text(snapshot)
        self.input_handler.set_status_text(status_text)
        if self._footer.mode == "persistent" and self._footer.enabled:
            self._footer.update(
                model=str(snapshot.get("model_name") or self.context.get("model", "")),
                context_pct=float(snapshot.get("context_percent", 0.0) or 0.0),
                tokens_in=int(snapshot.get("tokens_in", 0) or 0),
                tokens_out=int(snapshot.get("tokens_out", 0) or 0),
                elapsed=float(snapshot.get("elapsed_seconds", 0.0) or 0.0),
                phase=str(snapshot.get("phase", "idle")),
            )

    def _setup_agent(self):
        """Setup agent components."""
        session_id = str(
            asyncio.run(
                self._session_manager.create_session(  # type: ignore[misc]
                    repo_path=self.config.repo,
                    approval_policy=getattr(
                        __import__(
                            "coding_agent.approval", fromlist=["ApprovalPolicy"]
                        ),
                        "ApprovalPolicy",
                    ).YOLO
                    if self.config.approval_mode == "yolo"
                    else getattr(
                        __import__(
                            "coding_agent.approval", fromlist=["ApprovalPolicy"]
                        ),
                        "ApprovalPolicy",
                    ).INTERACTIVE
                    if self.config.approval_mode == "interactive"
                    else getattr(
                        __import__(
                            "coding_agent.approval", fromlist=["ApprovalPolicy"]
                        ),
                        "ApprovalPolicy",
                    ).AUTO,
                    provider_name=self.config.provider,
                    model_name=self.config.model,
                    base_url=self.config.base_url,
                    max_steps=self.config.max_steps,
                )
            )
        )
        self.context["session_id"] = session_id
        self.context["create_session"] = self._create_managed_session
        self.context["switch_session"] = self._switch_session
        self.context["restore_checkpoint"] = self._restore_checkpoint
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
        self._pipeline_adapter = PipelineAdapter(
            pipeline=pipeline, ctx=pipeline_ctx, consumer=self._consumer
        )
        pipeline_ctx.config["wire_consumer"] = self._consumer
        pipeline_ctx.config["agent_id"] = ""
        self._pipeline_ctx = pipeline_ctx
        managed_session = self._session_manager.get_session(session_id)
        managed_session.runtime_pipeline = pipeline
        managed_session.runtime_ctx = pipeline_ctx
        managed_session.runtime_adapter = self._pipeline_adapter
        managed_session.tape_id = pipeline_ctx.tape.tape_id
        self._session_manager.register_session(managed_session)
        self._refresh_command_context_from_pipeline_ctx(pipeline_ctx)

    async def _create_managed_session(self) -> str:
        session_id = await self._session_manager.create_session(
            repo_path=self.config.repo,
            provider_name=self.config.provider,
            model_name=self.config.model,
            base_url=self.config.base_url,
            max_steps=self.config.max_steps,
        )
        return session_id

    async def _switch_session(self, session_id: str) -> None:
        await self._session_manager.ensure_session_runtime(session_id)
        managed_session = self._session_manager.get_session(session_id)
        self.context["session_id"] = managed_session.id
        self._pipeline_ctx = managed_session.runtime_ctx
        self._pipeline_adapter = managed_session.runtime_adapter
        self._refresh_command_context_from_pipeline_ctx(managed_session.runtime_ctx)

    async def _restore_checkpoint(self, checkpoint_id: str) -> None:
        session_id = self.context.get("session_id")
        if not isinstance(session_id, str):
            raise RuntimeError("no active session")
        await self._session_manager.restore_checkpoint(session_id, checkpoint_id)
        await self._switch_session(session_id)

    # Proxy methods for WireConsumer protocol (used by subagent tool)
    async def emit(self, msg) -> None:
        """Proxy emit to current consumer."""
        await self._consumer.emit(msg)

    async def request_approval(self, req):
        """Proxy approval to current consumer."""
        return await self._consumer.request_approval(req)

    async def initialize(self) -> None:
        if not hasattr(self, "_pipeline_adapter"):
            return
        await self._pipeline_adapter.initialize()

    async def run(self):
        """Run the REPL loop."""
        prompt_output = get_prompt_output()
        set_prompt_output(prompt_output)

        await self.initialize()

        print_pt("🤖 Coding Agent - Interactive Mode", output=prompt_output)
        print_pt(
            "Type /help for commands, ! for real shell mode, or just chat.\n",
            output=prompt_output,
        )

        self._footer.run_spike_check()
        if self._footer.mode == "persistent":
            self._footer.enable()

        turn_count = 0

        try:
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
                    if user_input.strip() == "/clear" and self._footer.enabled:
                        self._footer.clear_and_redraw()
                    continue

                try:
                    await self._process_message(user_input)
                except Exception as e:
                    print_pt(
                        f"\nError during agent execution: {e}", output=prompt_output
                    )
                    print_pt(
                        "You can continue with a new message.\n", output=prompt_output
                    )
                turn_count += 1
        finally:
            self._footer.disable()
            if hasattr(self, "_pipeline_adapter"):
                await self._pipeline_adapter.close()

        print_pt("\nSession ended.\n", output=prompt_output)

    async def _process_message(self, message: str):
        self._renderer.user_message(message)
        full_message = expand_pasted_refs(message, self.input_handler._paste_refs)
        self.input_handler._paste_refs.clear()

        if self._footer.enabled:
            self._footer.update(
                model=self.context.get("model", ""),
                phase="streaming",
            )

        result = await self._pipeline_adapter.run_turn(full_message)

        if self._footer.enabled:
            self._footer.update(phase="idle")

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
