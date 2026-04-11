"""Pipeline — Bub-style linear stage runner for agent turns.

Stages: resolve_session → load_state → build_context → run_model → save_state → render → dispatch
"""

from __future__ import annotations

import logging
from inspect import isawaitable
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agentkit._types import StageName
from agentkit.directive.types import Directive
from agentkit.errors import PipelineError
from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape

logger = logging.getLogger(__name__)

try:
    from agentkit.tracing import get_tracer as _get_tracer

    _tracer = _get_tracer("agentkit.pipeline")
except Exception:
    _tracer = None


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
    context_builder: Any = None
    incremental_core_messages: list[dict[str, Any]] = field(default_factory=list)
    incremental_entry_count: int = 0
    incremental_tool_round_count: int = 0
    on_event: (
        Callable[
            [
                TextEvent
                | ThinkingEvent
                | ToolCallEvent
                | ToolResultEvent
                | UsageEvent
                | DoneEvent
            ],
            Awaitable[None],
        ]
        | None
    ) = None
    _handoff_done: bool = False


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
        ctx._handoff_done = False

        try:
            for stage in self.STAGES:
                try:
                    handler = getattr(self, f"_stage_{stage}", None)
                    if handler is not None:
                        if _tracer is not None:
                            _tracer.info(
                                "stage_start", stage=stage, entry_count=len(ctx.tape)
                            )
                        await handler(ctx)
                        if _tracer is not None:
                            _tracer.info(
                                "stage_end", stage=stage, entry_count=len(ctx.tape)
                            )
                        if stage == "load_state" and ctx.storage is not None:
                            begin = getattr(ctx.storage, "begin", None)
                            if callable(begin):
                                fork = begin(ctx.tape)
                                if not isinstance(fork, Tape):
                                    raise PipelineError(
                                        "storage.begin() must return Tape",
                                        stage=stage,
                                    )
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
        from agentkit.tape.view import TapeView
        from agentkit.context.builder import ContextBuilder

        system_prompt = ctx.config.get("system_prompt", "You are a helpful assistant.")
        if ctx.context_builder is None:
            ctx.context_builder = ContextBuilder(system_prompt=system_prompt)
        builder = ctx.context_builder

        incremental_enabled = bool(ctx.config.get("incremental_context"))
        force_full_rebuild = False

        window_result = self._runtime.call_first(
            "resolve_context_window", tape=ctx.tape
        )
        if window_result is not None:
            if not (
                isinstance(window_result, tuple)
                and len(window_result) == 2
                and isinstance(window_result[0], int)
            ):
                logger.warning(
                    "resolve_context_window returned unexpected shape (%s), skipping windowing",
                    type(window_result).__name__,
                )
            else:
                window_start, summary_anchor = window_result
                if summary_anchor is not None and not ctx._handoff_done:
                    abs_window_start = ctx.tape.window_start + window_start
                    ctx.tape.handoff(summary_anchor, window_start=abs_window_start)
                    ctx._handoff_done = True
                    force_full_rebuild = True
                logger.info(
                    "Context window advanced: %d entries visible (of %d total)",
                    len(ctx.tape.windowed_entries()),
                    len(ctx.tape),
                )
        else:
            summary = self._runtime.call_first("summarize_context", tape=ctx.tape)
            if summary is not None:
                ctx.tape = Tape(
                    entries=list(summary),
                    tape_id=ctx.tape.tape_id,
                    parent_id=ctx.tape.parent_id,
                )
                force_full_rebuild = True
                logger.info(
                    "Context summarized (legacy): %d entries remaining", len(ctx.tape)
                )

        grounding_results = self._runtime.call_many("build_context", tape=ctx.tape)
        grounding: list[dict[str, Any]] = []
        for result in grounding_results:
            if isinstance(result, list):
                grounding.extend(result)

        interval = max(
            1, int(ctx.config.get("incremental_context_rebuild_interval", 5))
        )
        should_full_rebuild = (
            not incremental_enabled
            or force_full_rebuild
            or ctx.incremental_entry_count == 0
            or ctx.incremental_entry_count > len(ctx.tape)
            or ctx.incremental_tool_round_count % interval == 0
        )

        if should_full_rebuild:
            view = TapeView.from_tape(ctx.tape)
            ctx.incremental_core_messages = builder.build_core_messages(view.entries)
            ctx.incremental_entry_count = len(ctx.tape)
            ctx.messages = builder.compose_messages(
                ctx.incremental_core_messages,
                grounding=grounding or None,
            )
            return

        snapshot = ctx.tape.snapshot()
        new_entries = list(snapshot[ctx.incremental_entry_count :])
        builder.append_to_core_messages(ctx.incremental_core_messages, new_entries)
        ctx.incremental_entry_count = len(snapshot)
        ctx.messages = builder.compose_messages(
            ctx.incremental_core_messages,
            grounding=grounding or None,
        )

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
            thinking_chunks: list[str] = []

            async for event in ctx.llm_provider.stream(ctx.messages, tools=tool_dicts):
                if isinstance(event, ThinkingEvent):
                    if ctx.on_event:
                        await ctx.on_event(event)
                    thinking_chunks.append(event.text)
                elif isinstance(event, TextEvent):
                    if ctx.on_event:
                        await ctx.on_event(event)
                    text_chunks.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    if ctx.on_event:
                        await ctx.on_event(event)
                    tool_calls.append(
                        {
                            "id": event.tool_call_id,
                            "name": event.name,
                            "arguments": event.arguments,
                        }
                    )
                elif isinstance(event, UsageEvent):
                    if ctx.on_event:
                        await ctx.on_event(event)
                elif isinstance(event, DoneEvent):
                    if ctx.on_event:
                        await ctx.on_event(event)
                    break

            if text_chunks and not tool_calls:
                payload: dict[str, Any] = {
                    "role": "assistant",
                    "content": "".join(text_chunks),
                }
                if thinking_chunks:
                    payload["reasoning_content"] = "".join(thinking_chunks)
                ctx.tape.append(
                    Entry(
                        kind="message",
                        payload=payload,
                    )
                )
                break

            if tool_calls:
                if text_chunks:
                    payload = {
                        "role": "assistant",
                        "content": "".join(text_chunks),
                    }
                    if thinking_chunks:
                        payload["reasoning_content"] = "".join(thinking_chunks)
                    ctx.tape.append(
                        Entry(
                            kind="message",
                            payload=payload,
                        )
                    )

                executable_calls: list[dict[str, Any]] = []
                executable_metas: list[dict[str, Any]] = []
                checkpoint_entry_count: int | None = None

                for i, tc in enumerate(tool_calls):
                    tc_payload: dict[str, Any] = {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "role": "assistant",
                    }
                    if i == 0 and thinking_chunks and not text_chunks:
                        tc_payload["reasoning_content"] = "".join(thinking_chunks)
                    ctx.tape.append(
                        Entry(
                            kind="tool_call",
                            payload=tc_payload,
                        )
                    )

                    directive = self._runtime.call_first(
                        "approve_tool_call",
                        tool_name=tc["name"],
                        arguments=tc["arguments"],
                    )

                    approved = True
                    if directive is not None:
                        if not isinstance(directive, Directive):
                            logger.warning(
                                "approve_tool_call returned non-Directive type %s for tool %r, rejecting (fail-closed)",
                                type(directive).__name__,
                                tc["name"],
                            )
                            approved = False
                        elif self._directive_executor is not None:
                            approved = await self._directive_executor.execute(directive)

                    if not approved:
                        rejection_msg = f"Tool call rejected: {getattr(directive, 'reason', 'policy')}"
                        ctx.tape.append(
                            Entry(
                                kind="tool_result",
                                payload={
                                    "tool_call_id": tc["id"],
                                    "content": rejection_msg,
                                },
                            )
                        )
                        if ctx.on_event:
                            await ctx.on_event(
                                ToolResultEvent(
                                    tool_call_id=tc["id"],
                                    name=tc["name"],
                                    result=rejection_msg,
                                    is_error=True,
                                )
                            )
                        continue

                    executable_calls.append(
                        {"name": tc["name"], "arguments": tc["arguments"]}
                    )
                    executable_metas.append(tc)

                if executable_calls:
                    checkpoint_entry_count = len(ctx.tape)
                    batch_results = (
                        self._runtime.call_first(
                            "execute_tools_batch",
                            tool_calls=executable_calls,
                        )
                        if len(executable_calls) > 1
                        else None
                    )
                    if isawaitable(batch_results):
                        batch_results = await batch_results

                    if batch_results is None:
                        batch_results = []
                        for call in executable_calls:
                            try:
                                result = self._runtime.call_first(
                                    "execute_tool",
                                    name=call["name"],
                                    arguments=call["arguments"],
                                )
                                if isawaitable(result):
                                    result = await result
                                batch_results.append(result)
                            except Exception as exc:
                                batch_results.append(exc)

                    for tc, result in zip(
                        executable_metas, batch_results, strict=False
                    ):
                        if isinstance(result, Exception):
                            result_str = (
                                f"Error executing tool '{tc['name']}': {str(result)}"
                            )
                            is_error = True
                        elif result is None:
                            result_str = (
                                f"Error executing tool '{tc['name']}': "
                                f"tool '{tc['name']}' not found"
                            )
                            is_error = True
                        else:
                            result_str = str(result) if result is not None else ""
                            max_size = ctx.config.get("max_tool_result_size", 10000)
                            if len(result_str) > max_size:
                                result_str = (
                                    result_str[:max_size]
                                    + f"\n... ({len(result_str) - max_size} chars truncated)"
                                )
                            is_error = False

                        ctx.tape.append(
                            Entry(
                                kind="tool_result",
                                payload={
                                    "tool_call_id": tc["id"],
                                    "content": result_str,
                                },
                            )
                        )
                        if ctx.on_event:
                            await ctx.on_event(
                                ToolResultEvent(
                                    tool_call_id=tc["id"],
                                    name=tc["name"],
                                    result=result_str,
                                    is_error=is_error,
                                )
                            )

                if executable_calls:
                    ctx.incremental_tool_round_count += 1
                await self._stage_build_context(ctx)
                if checkpoint_entry_count is not None and len(
                    ctx.tape
                ) != checkpoint_entry_count + len(executable_metas):
                    ctx.incremental_tool_round_count = 0
                continue

            break

    async def _stage_save_state(self, ctx: PipelineContext) -> None:
        self._runtime.notify("on_checkpoint", ctx=ctx, runtime=self._runtime)

    async def _stage_render(self, ctx: PipelineContext) -> None:
        raw_directives = self._runtime.call_many("on_turn_end", tape=ctx.tape)
        directives: list[Directive] = []
        for d in raw_directives:
            if isinstance(d, Directive):
                directives.append(d)
            else:
                logger.warning(
                    "on_turn_end returned non-Directive type %s, dropping",
                    type(d).__name__,
                )
        ctx.output = {"directives": directives}

        if self._directive_executor is not None:
            for directive in directives:
                await self._directive_executor.execute(directive)

    async def _stage_dispatch(self, ctx: PipelineContext) -> None:
        pass
