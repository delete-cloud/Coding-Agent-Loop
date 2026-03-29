"""Pipeline — Bub-style linear stage runner for agent turns.

Stages: resolve_session → load_state → build_context → run_model → save_state → render → dispatch
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agentkit._types import StageName
from agentkit.errors import PipelineError
from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Mutable context threaded through pipeline stages."""

    tape: Tape
    session_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    plugin_states: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    llm_provider: Any = None
    storage: Any = None
    tool_schemas: list[Any] = field(default_factory=list)
    response_entries: list[Any] = field(default_factory=list)
    output: Any = None


class Pipeline:
    """Linear pipeline that runs one agent turn through 7 stages."""

    STAGES: list[StageName] = [
        "resolve_session",
        "load_state",
        "build_context",
        "run_model",
        "save_state",
        "render",
        "dispatch",
    ]

    def __init__(
        self,
        runtime: HookRuntime,
        registry: PluginRegistry,
        directive_executor: Any = None,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._directive_executor = directive_executor

    @property
    def stage_names(self) -> list[str]:
        return list(self.STAGES)

    async def mount(self, ctx: PipelineContext) -> None:
        for plugin_id in self._registry.plugin_ids():
            plugin = self._registry.get(plugin_id)
            mount_hook = plugin.hooks().get("mount")
            if mount_hook is not None:
                state = mount_hook()
                if state is not None:
                    ctx.plugin_states[plugin_id] = state

    async def run_turn(self, ctx: PipelineContext) -> PipelineContext:
        fork = None
        original_tape = ctx.tape

        try:
            for stage in self.STAGES:
                try:
                    handler = getattr(self, f"_stage_{stage}", None)
                    if handler is not None:
                        await handler(ctx)
                        if stage == "load_state" and ctx.storage is not None:
                            begin = getattr(ctx.storage, "begin", None)
                            if callable(begin):
                                fork = begin(ctx.tape)
                                ctx.tape = fork
                    else:
                        logger.debug("Stage '%s' has no handler, skipping", stage)
                except PipelineError:
                    raise
                except Exception as exc:
                    self._runtime.notify("on_error", stage=stage, error=exc)
                    raise PipelineError(str(exc), stage=stage) from exc

            if fork is not None:
                await ctx.storage.commit(fork)

            return ctx
        except Exception:
            if fork is not None:
                ctx.storage.rollback(fork)
            ctx.tape = original_tape
            raise

    async def _stage_resolve_session(self, ctx: PipelineContext) -> None:
        pass

    async def _stage_load_state(self, ctx: PipelineContext) -> None:
        if ctx.storage is None:
            ctx.storage = self._runtime.call_first("provide_storage")
        if ctx.llm_provider is None:
            ctx.llm_provider = self._runtime.call_first("provide_llm")

        tool_lists = self._runtime.call_many("get_tools")
        ctx.tool_schemas = []
        for tool_list in tool_lists:
            if isinstance(tool_list, list):
                ctx.tool_schemas.extend(tool_list)
            else:
                ctx.tool_schemas.append(tool_list)

    async def _stage_build_context(self, ctx: PipelineContext) -> None:
        summary = self._runtime.call_first("summarize_context", tape=ctx.tape)
        if summary is not None:
            ctx.tape = Tape(
                entries=list(summary),
                tape_id=ctx.tape.tape_id,
                parent_id=ctx.tape.parent_id,
            )
            logger.info("Context summarized: %d entries remaining", len(ctx.tape))

        grounding_results = self._runtime.call_many("build_context", tape=ctx.tape)
        grounding: list[dict[str, Any]] = []
        for result in grounding_results:
            if isinstance(result, list):
                grounding.extend(result)

        from agentkit.context.builder import ContextBuilder

        system_prompt = ctx.config.get("system_prompt", "You are a helpful assistant.")
        builder = ContextBuilder(system_prompt=system_prompt)
        ctx.messages = builder.build(ctx.tape, grounding=grounding or None)

    async def _stage_run_model(self, ctx: PipelineContext) -> None:
        if ctx.llm_provider is None:
            logger.warning("No LLM provider available, skipping run_model")
            return

        max_tool_rounds = ctx.config.get("max_tool_rounds", 20)

        for _round in range(max_tool_rounds):
            tool_dicts = (
                [s.to_openai_format() for s in ctx.tool_schemas]
                if ctx.tool_schemas
                else None
            )

            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            async for event in ctx.llm_provider.stream(ctx.messages, tools=tool_dicts):
                if isinstance(event, TextEvent):
                    text_chunks.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    tool_calls.append(
                        {
                            "id": event.tool_call_id,
                            "name": event.name,
                            "arguments": event.arguments,
                        }
                    )
                elif isinstance(event, DoneEvent):
                    break

            if text_chunks and not tool_calls:
                ctx.tape.append(
                    Entry(
                        kind="message",
                        payload={"role": "assistant", "content": "".join(text_chunks)},
                    )
                )
                break

            if tool_calls:
                for tc in tool_calls:
                    ctx.tape.append(
                        Entry(
                            kind="tool_call",
                            payload={
                                "id": tc["id"],
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                                "role": "assistant",
                            },
                        )
                    )

                    directive = self._runtime.call_first(
                        "approve_tool_call",
                        tool_name=tc["name"],
                        arguments=tc["arguments"],
                    )

                    approved = True
                    if directive is not None and self._directive_executor is not None:
                        approved = await self._directive_executor.execute(directive)

                    if not approved:
                        ctx.tape.append(
                            Entry(
                                kind="tool_result",
                                payload={
                                    "tool_call_id": tc["id"],
                                    "content": f"Tool call rejected: {getattr(directive, 'reason', 'policy')}",
                                },
                            )
                        )
                        continue

                    result = self._runtime.call_first(
                        "execute_tool",
                        name=tc["name"],
                        arguments=tc["arguments"],
                    )

                    ctx.tape.append(
                        Entry(
                            kind="tool_result",
                            payload={
                                "tool_call_id": tc["id"],
                                "content": str(result) if result is not None else "",
                            },
                        )
                    )

                await self._stage_build_context(ctx)
                continue

            break

    async def _stage_save_state(self, ctx: PipelineContext) -> None:
        self._runtime.notify("on_checkpoint", ctx=ctx)

    async def _stage_render(self, ctx: PipelineContext) -> None:
        directives = self._runtime.call_many("on_turn_end", tape=ctx.tape)
        ctx.output = {"directives": directives}

        if self._directive_executor is not None:
            for directive in directives:
                if directive is not None:
                    await self._directive_executor.execute(directive)

    async def _stage_dispatch(self, ctx: PipelineContext) -> None:
        pass
