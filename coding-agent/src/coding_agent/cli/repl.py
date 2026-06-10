"""Main REPL loop for interactive mode."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Literal

from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.approval import ApprovalPolicy
from coding_agent.cli.commands import handle_command
from coding_agent.cli.input_handler import InputHandler, expand_pasted_refs
from coding_agent.cli.local_runtime import (
    LocalCliSessionManager,
    create_local_cli_runtime,
    create_local_cli_session_manager,
    local_cli_session_origin,
)
from coding_agent.cli.terminal_output import (
    get_prompt_output,
    print_pt,
    set_prompt_output,
)
from coding_agent.cli.bash_executor import BashExecutor
from coding_agent.core.config import Config
from coding_agent.ui.stream_renderer import StreamingRenderer
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.ui.status_footer import StatusFooter
from coding_agent.wire.protocol import TurnEnd, WireMessage


console = Console(force_terminal=True, soft_wrap=False)


class _InteractiveApprovalMemory:
    def __init__(self) -> None:
        self._coordinator = ApprovalCoordinator()

    def is_session_approved(self, req) -> bool:
        return self._coordinator.is_session_approved(req)

    def remember(self, req, response) -> None:
        if response.approved and response.scope in {"session", "always"}:
            self._coordinator.remember_session_approval(req)


class InteractiveSession:
    """Manages an interactive agent session."""

    def __init__(self, config: Config):
        self.config = config
        self._session_manager = _local_session_manager()
        self._pipeline: Any | None = None
        self._pipeline_ctx: Any | None = None
        self._pipeline_adapter: Any | None = None
        self.context: dict[str, Any] = {
            "should_exit": False,
            "model": config.model,
            "thinking_config": {"enabled": True, "effort": "medium"},
            "session_manager": self._session_manager,
        }
        self.input_handler = InputHandler()
        self._bash_executor = BashExecutor(
            cwd=str(config.repo) if config.repo else None
        )

        # Scrollback-based renderer — created once, persists across turns
        self._renderer = StreamingRenderer(console=console, enhanced_boundaries=True)
        self._approval_memory = _InteractiveApprovalMemory()
        self._consumer = RichConsumer(
            self._renderer,
            thinking_enabled=lambda: bool(
                self.context.get("thinking_config", {}).get("enabled", True)
            ),
            thinking_effort=lambda: str(
                self.context.get("thinking_config", {}).get("effort", "medium")
            ),
            on_status=self._handle_status_update,
            approval_memory=self._approval_memory,
        )
        self._footer = StatusFooter(console=console)
        self._managed_session_initialized = False
        self.context["on_clear"] = self._on_clear

        self._setup_agent()

    def _on_clear(self) -> None:
        if self._footer.enabled:
            self._footer.clear_and_redraw()

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
        self.context["create_session"] = self._create_managed_session
        self.context["switch_session"] = self._switch_session
        self.context["restore_checkpoint"] = self._restore_checkpoint
        self.context["on_model_change"] = self._change_model_for_next_turn
        runtime = create_local_cli_runtime(self.config, self._consumer)
        self._pipeline = runtime.pipeline
        self._pipeline_ctx = runtime.pipeline_ctx
        self._pipeline_adapter = runtime.pipeline_adapter
        # Wire REPL's mutable thinking_config into the pipeline so provider
        # reads it dynamically each turn without needing a rebuild.
        self._pipeline_ctx.config["thinking_config"] = self.context["thinking_config"]
        self._refresh_command_context_from_pipeline_ctx(runtime.pipeline_ctx)

    def _approval_policy(self) -> ApprovalPolicy:
        if self.config.approval_mode == "yolo":
            return ApprovalPolicy.YOLO
        if self.config.approval_mode == "interactive":
            return ApprovalPolicy.INTERACTIVE
        return ApprovalPolicy.AUTO

    async def _initialize_managed_session(self) -> None:
        if self._managed_session_initialized:
            return
        if (
            self._pipeline is None
            or self._pipeline_ctx is None
            or self._pipeline_adapter is None
        ):
            raise RuntimeError("REPL pipeline is not initialized")
        session_id = await self._session_manager.create_session(
            repo_path=self.config.repo,
            origin=local_cli_session_origin(
                entrypoint="repl",
                mode="interactive",
            ),
            approval_policy=self._approval_policy(),
            provider_name=self.config.provider,
            model_name=self.config.model,
            base_url=self.config.base_url,
            max_steps=self.config.max_steps,
        )
        self.context["session_id"] = session_id
        managed_session = self._session_manager.get_session(session_id)
        await self._session_manager.attach_runtime(
            managed_session,
            pipeline=self._pipeline,
            pipeline_ctx=self._pipeline_ctx,
            pipeline_adapter=self._pipeline_adapter,
        )
        await self._session_manager.start_owner_lease_renewal()
        self._managed_session_initialized = True

    async def _create_managed_session(self) -> str:
        session_id = await self._session_manager.create_session(
            repo_path=self.config.repo,
            origin=local_cli_session_origin(
                entrypoint="repl",
                mode="interactive",
            ),
            approval_policy=self._approval_policy(),
            provider_name=self.config.provider,
            model_name=self.config.model,
            base_url=self.config.base_url,
            max_steps=self.config.max_steps,
        )
        return session_id

    def _sync_config_from_managed_session(self, managed_session: Any) -> None:
        session_id = getattr(managed_session, "id", "<unknown>")
        provider_name = getattr(managed_session, "provider_name", None)
        allowed_providers = {
            "openai",
            "anthropic",
            "copilot",
            "kimi",
            "kimi-code",
            "kimi-code-anthropic",
            "deepseek",
            "stepfun",
            "codex",
        }
        if provider_name not in allowed_providers:
            raise RuntimeError(
                f"restored session {session_id} has invalid provider_name"
            )

        model_name = getattr(managed_session, "model_name", None)
        if not isinstance(model_name, str) or not model_name:
            raise RuntimeError(f"restored session {session_id} is missing model_name")

        base_url = getattr(managed_session, "base_url", None)
        if base_url is not None and not isinstance(base_url, str):
            raise RuntimeError(f"restored session {session_id} has invalid base_url")

        max_steps = getattr(managed_session, "max_steps", None)
        if not isinstance(max_steps, int) or max_steps < 0:
            raise RuntimeError(f"restored session {session_id} has invalid max_steps")

        approval_mode: Literal["yolo", "interactive", "auto"]
        match getattr(managed_session, "approval_policy", None):
            case ApprovalPolicy.YOLO:
                approval_mode = "yolo"
            case ApprovalPolicy.INTERACTIVE:
                approval_mode = "interactive"
            case ApprovalPolicy.AUTO:
                approval_mode = "auto"
            case _:
                raise RuntimeError(
                    f"restored session {session_id} has invalid approval_policy"
                )

        self.config.provider = provider_name
        self.config.model = model_name
        self.config.base_url = base_url
        self.config.max_steps = max_steps
        self.config.approval_mode = approval_mode

    async def _switch_session(self, session_id: str) -> None:
        await self._session_manager.ensure_session_runtime(session_id)
        managed_session = self._session_manager.get_session(session_id)
        self._sync_config_from_managed_session(managed_session)
        self.context["session_id"] = managed_session.id
        self.context["model"] = self.config.model
        self.context["provider"] = self.config.provider
        self._pipeline = managed_session.runtime_pipeline
        self._pipeline_ctx = managed_session.runtime_ctx
        self._pipeline_adapter = managed_session.runtime_adapter
        if self._pipeline_adapter is not None:
            self._pipeline_adapter.set_consumer(self._consumer)
        if self._pipeline_ctx is not None:
            self._pipeline_ctx.config["wire_consumer"] = self._consumer
            self._pipeline_ctx.config["thinking_config"] = self.context["thinking_config"]
        self._refresh_command_context_from_pipeline_ctx(managed_session.runtime_ctx)

    async def _change_model_for_next_turn(
        self,
        model_name: str,
        provider_name: str | None = None,
    ) -> None:
        if self.config.model == model_name and (
            provider_name is None or self.config.provider == provider_name
        ):
            return
        old_model_name = self.config.model
        old_provider = self.config.provider
        session_id = self.context.get("session_id")
        if not isinstance(session_id, str):
            self.config.model = model_name
            self.context["model"] = model_name
            if provider_name is not None:
                self.config.provider = provider_name
                self.context["provider"] = provider_name
            try:
                self._setup_agent()
            except Exception:
                self.config.model = old_model_name
                self.config.provider = old_provider
                self.context["model"] = old_model_name
                self.context["provider"] = old_provider
                raise
            return

        try:
            managed_session = (
                await self._session_manager.replace_session_runtime_config(
                    session_id,
                    model_name=model_name,
                    provider_name=provider_name,
                )
            )
        except Exception:
            self.config.model = old_model_name
            self.config.provider = old_provider
            self.context["model"] = old_model_name
            self.context["provider"] = old_provider
            raise

        self._sync_config_from_managed_session(managed_session)
        self.context["session_id"] = managed_session.id
        self.context["model"] = self.config.model
        self.context["provider"] = self.config.provider
        self._pipeline = managed_session.runtime_pipeline
        self._pipeline_ctx = managed_session.runtime_ctx
        self._pipeline_adapter = managed_session.runtime_adapter
        if self._pipeline_adapter is not None:
            self._pipeline_adapter.set_consumer(self._consumer)
        if self._pipeline_ctx is not None:
            self._pipeline_ctx.config["wire_consumer"] = self._consumer
            self._pipeline_ctx.config["thinking_config"] = self.context["thinking_config"]
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
        if self._pipeline_adapter is None:
            return
        await self._initialize_managed_session()
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
            if self._pipeline_adapter is not None:
                await self._pipeline_adapter.close()
            await self._session_manager.close()

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

        if self._pipeline_adapter is None:
            raise RuntimeError("REPL pipeline adapter is not initialized")
        session_id = self.context.get("session_id")
        if isinstance(session_id, str):
            await self._run_managed_turn(session_id, full_message)
        else:
            result = await self._pipeline_adapter.run_turn(full_message)
            if result.stop_reason == result.stop_reason.ERROR and result.error:
                print_pt(
                    f"\nError: {result.error}\n",
                    output=get_prompt_output(sys.__stdout__),
                )

        if self._footer.enabled:
            self._footer.update(phase="idle")

    async def _run_managed_turn(self, session_id: str, message: str) -> None:
        managed_session = await self._session_manager.get_session_async(session_id)
        task = asyncio.create_task(self._session_manager.run_agent(session_id, message))
        await self._stream_managed_wire(managed_session.wire, task)
        await task
        refreshed_session = await self._session_manager.get_session_async(session_id)
        self._pipeline = refreshed_session.runtime_pipeline
        self._pipeline_ctx = refreshed_session.runtime_ctx
        self._pipeline_adapter = refreshed_session.runtime_adapter
        if self._pipeline_adapter is not None:
            self._pipeline_adapter.set_consumer(self._consumer)
        if self._pipeline_ctx is not None:
            self._pipeline_ctx.config["wire_consumer"] = self._consumer
            self._refresh_command_context_from_pipeline_ctx(self._pipeline_ctx)

    async def _stream_managed_wire(self, wire: Any, task: asyncio.Task[object]) -> None:
        while True:
            if task.done() and task.exception() is not None:
                await task
            message_task = asyncio.create_task(wire.get_next_outgoing())
            done, _pending = await asyncio.wait(
                {message_task, task},
                timeout=1.0 if task.done() else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if message_task in done:
                message = message_task.result()
                await self._consumer.emit(message)
                if _is_root_turn_end(message):
                    break
                continue
            message_task.cancel()
            try:
                await message_task
            except asyncio.CancelledError:
                pass
            if task in done:
                await task
                continue
            if task.done():
                break


def _is_root_turn_end(message: WireMessage) -> bool:
    return isinstance(message, TurnEnd) and not message.agent_id


def _local_session_manager() -> LocalCliSessionManager:
    return create_local_cli_session_manager()


async def run_repl(config: Config):
    """Entry point for REPL mode."""
    from agentkit.tracing import configure_tracing

    configure_tracing()
    session = InteractiveSession(config)
    await session.run()
