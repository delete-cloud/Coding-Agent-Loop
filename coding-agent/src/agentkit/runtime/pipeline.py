"""Pipeline — Bub-style linear stage runner for agent turns.

Stages: resolve_session → load_state → build_context → run_model → save_state → render → dispatch
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false, reportPrivateUsage=false, reportDeprecated=false

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from inspect import Parameter, isawaitable, signature
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Protocol, cast

from agentkit._types import StageName
from agentkit.directive.types import Directive
from agentkit.errors import HookError, HookTypeError, PipelineError
from agentkit.observability import ObservationSink, record_span
from agentkit.plugin.registry import PluginCapability, PluginRegistry
from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from agentkit.runtime.context import AgentRunContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.messages import (
    RuntimeMessageBus,
    RuntimeMessageCursor,
    RuntimeMessageKind,
    SequencedRuntimeMessage,
)
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError
from agentkit.tools.toolset import (
    ToolCallRequest,
    ToolExecutionOptions,
    ToolExecutor,
    Toolset,
)

logger = logging.getLogger(__name__)

_PIPELINE_RUNTIME_MESSAGE_KINDS = frozenset(
    {
        RuntimeMessageKind.INTERRUPT,
        RuntimeMessageKind.USER_STEER,
        RuntimeMessageKind.SUBAGENT_MESSAGE,
        RuntimeMessageKind.SYSTEM_NOTICE,
    }
)
_PROMPT_RUNTIME_MESSAGE_KINDS = frozenset(
    {
        RuntimeMessageKind.USER_STEER,
        RuntimeMessageKind.SUBAGENT_MESSAGE,
        RuntimeMessageKind.SYSTEM_NOTICE,
    }
)
_STAGE_SPAN_NAMES: dict[StageName, str] = {
    "resolve_session": "runtime.stage.resolve",
    "load_state": "runtime.stage.load_state",
    "build_context": "runtime.stage.build_context",
    "run_model": "runtime.stage.model_generate",
    "save_state": "runtime.stage.save_tape",
    "render": "runtime.stage.apply_directives",
    "dispatch": "runtime.stage.dispatch",
}
_TRACE_METADATA_ATTRIBUTE_KEYS = frozenset(
    {
        "turn_id",
        "tape_id",
        "tool_call_id",
        "interaction_id",
        "event_id",
        "checkpoint_id",
    }
)
_EMPTY_CONTEXT_INPUTS: Mapping[str, object] = MappingProxyType({})

StructuredToolResultScopeFactory = Callable[[bool], AbstractContextManager[None]]


class BuildContextInputProvider(Protocol):
    """Host boundary that freezes plugin-specific context inputs."""

    async def snapshot(self, ctx: "PipelineContext") -> Mapping[str, object]: ...


@contextmanager
def _noop_structured_tool_result_scope(enabled: bool) -> Iterator[None]:
    yield


def _structured_tool_result_scope(
    ctx: "PipelineContext", enabled: bool
) -> AbstractContextManager[None]:
    scope_factory = ctx.config.get("structured_tool_result_scope")
    if scope_factory is None:
        return _noop_structured_tool_result_scope(enabled)
    if not callable(scope_factory):
        raise TypeError("structured_tool_result_scope must be callable")
    return cast(StructuredToolResultScopeFactory, scope_factory)(enabled)


def _observation_sink(ctx: "PipelineContext") -> ObservationSink | None:
    sink = ctx.config.get("observation_sink")
    if sink is None:
        return None
    if not isinstance(sink, ObservationSink):
        raise TypeError("observation_sink must implement ObservationSink")
    return sink


def _stage_span_attributes(
    ctx: "PipelineContext",
    *,
    stage: StageName,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "stage": stage,
        "entry_count_before": len(ctx.tape),
    }
    if ctx.session_id:
        attributes["session_id"] = ctx.session_id
    attributes.update(_runtime_correlation_attributes(ctx))
    return attributes


def _safe_trace_metadata_attributes(run_context: AgentRunContext) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for key, value in run_context.trace_metadata.items():
        if key not in _TRACE_METADATA_ATTRIBUTE_KEYS:
            continue
        if isinstance(value, bool | int | float | str):
            attributes[key] = value
    return attributes


def _runtime_correlation_attributes(ctx: "PipelineContext") -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if ctx.run_context is not None:
        attributes.update(_safe_trace_metadata_attributes(ctx.run_context))
        attributes["run_id"] = ctx.run_context.run_id
        if ctx.run_context.agent_id is not None:
            attributes["agent_id"] = ctx.run_context.agent_id
        if ctx.run_context.parent_run_id is not None:
            attributes["parent_run_id"] = ctx.run_context.parent_run_id
    if ctx.tape.tape_id:
        attributes["tape_id"] = ctx.tape.tape_id
    return attributes


def _llm_generation_attributes(
    ctx: "PipelineContext",
    *,
    message_count: int,
    tool_schema_count: int,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "message_count": message_count,
        "tool_schema_count": tool_schema_count,
    }
    if ctx.session_id:
        attributes["session_id"] = ctx.session_id
    attributes.update(_runtime_correlation_attributes(ctx))
    model = ctx.config.get("model")
    if isinstance(model, str) and model:
        attributes["model"] = model
    return attributes


try:
    from agentkit.tracing import get_tracer as _get_tracer

    _tracer = _get_tracer("agentkit.pipeline")
except Exception:
    _tracer = None


def _format_result(
    result: Any,
    *,
    structured: bool = False,
    max_size: int = 10000,
) -> str:
    if structured and isinstance(result, dict):
        result_str = json.dumps(result)
    else:
        result_str = str(result) if result is not None else ""

    if len(result_str) > max_size:
        result_str = (
            result_str[:max_size]
            + f"\n... ({len(result_str) - max_size} chars truncated)"
        )
    return result_str


def _string_payload_value(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _format_active_approvals(value: Any) -> str:
    """Format active approval summaries.

    Items must be non-empty strings or mappings with a non-empty ``request_id``
    and optional ``tool`` string; other entries are ignored.
    """
    if not isinstance(value, list):
        return ""

    formatted: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            formatted.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        request_id = item.get("request_id")
        tool = item.get("tool")
        if isinstance(request_id, str) and request_id:
            if isinstance(tool, str) and tool:
                formatted.append(f"{request_id}:{tool}")
            else:
                formatted.append(request_id)
    return ", ".join(formatted)


def _build_context_hook_kwargs(
    fn: Callable[..., Any],
    ctx: "PipelineContext",
    *,
    context_inputs: Mapping[str, object],
) -> dict[str, Any]:
    try:
        parameters = signature(fn).parameters
    except (TypeError, ValueError):
        return {"tape": ctx.tape}
    has_var_kwargs = any(
        parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    if "context_inputs" in parameters:
        kwargs: dict[str, Any] = {"context_inputs": context_inputs}
        if "tape" in parameters or has_var_kwargs:
            kwargs["tape"] = ctx.tape
        if "runtime_prompt" in parameters:
            kwargs["runtime_prompt"] = _latest_runtime_query(ctx.runtime_messages)
        return kwargs
    if has_var_kwargs:
        return {"tape": ctx.tape, "ctx": ctx}
    kwargs = {}
    if "tape" in parameters:
        kwargs["tape"] = ctx.tape
    if "ctx" in parameters:
        kwargs["ctx"] = ctx
    return kwargs


def _latest_runtime_query(
    messages: list[SequencedRuntimeMessage],
) -> str | None:
    for item in reversed(messages):
        message = item.message
        if message.kind not in (
            RuntimeMessageKind.USER_STEER,
            RuntimeMessageKind.SUBAGENT_MESSAGE,
        ):
            continue
        value = _string_payload_value(
            message.payload,
            "text",
            "message",
            "content",
        )
        if value:
            return value
    return None


def _format_runtime_prompt_messages(
    messages: list[SequencedRuntimeMessage],
) -> list[str]:
    prompt_kinds = {
        RuntimeMessageKind.USER_STEER,
        RuntimeMessageKind.SUBAGENT_MESSAGE,
        RuntimeMessageKind.SYSTEM_NOTICE,
    }
    formatted: list[str] = []
    for item in messages:
        message = item.message
        if message.kind not in prompt_kinds:
            continue
        text = _string_payload_value(message.payload, "text", "message", "content")
        if text:
            formatted.append(f"{message.kind.value} {message.message_id}: {text}")
        else:
            formatted.append(f"{message.kind.value} {message.message_id}")
    return formatted


@dataclass
class PipelineContext:
    """Mutable context threaded through pipeline stages."""

    tape: Tape
    session_id: str = ""
    run_context: AgentRunContext | None = None
    config: dict[str, Any] = field(default_factory=dict)
    plugin_states: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    llm_provider: Any = None
    storage: Any = None
    runtime_message_bus: RuntimeMessageBus | None = None
    runtime_message_cursor: RuntimeMessageCursor = field(
        default_factory=RuntimeMessageCursor
    )
    runtime_messages: list[SequencedRuntimeMessage] = field(default_factory=list)
    runtime_started_at: float | None = None
    active_approvals: list[Any] = field(default_factory=list)
    toolset: Toolset | None = None
    tool_schemas: list[Any] = field(default_factory=list)
    response_entries: list[Any] = field(default_factory=list)
    output: Any = None
    context_builder: Any = None
    incremental_core_messages: list[dict[str, Any]] = field(default_factory=list)
    incremental_entry_count: int = 0
    incremental_tool_round_count: int = 0
    incremental_grounding_start: int = 0
    incremental_grounding_count: int = 0
    incremental_window_start: int = 0
    incremental_requires_full_rebuild: bool = False
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
        *,
        tool_executor: ToolExecutor | None = None,
        context_input_provider: BuildContextInputProvider | None = None,
    ) -> None:
        self._runtime = runtime
        self._registry = registry
        self._directive_executor = directive_executor
        self._tool_executor = tool_executor
        self._context_input_provider = context_input_provider

    @property
    def stage_names(self) -> list[str]:
        return list(self.STAGES)

    def _ensure_toolset(self, ctx: PipelineContext) -> Toolset:
        if ctx.toolset is None:
            ctx.toolset = Toolset(
                runtime=self._runtime,
                directive_executor=self._directive_executor,
                host_executor=self._tool_executor,
            )
        return ctx.toolset

    def _require_toolset(self, ctx: PipelineContext, *, stage: StageName) -> Toolset:
        if ctx.toolset is None:
            raise PipelineError(
                "toolset must be initialized before pipeline stages",
                stage=stage,
            )
        return ctx.toolset

    async def _consume_runtime_messages(
        self,
        ctx: PipelineContext,
        *,
        stage: StageName,
    ) -> bool:
        if ctx.runtime_message_bus is None:
            return False

        batch = await ctx.runtime_message_bus.consume_after(
            ctx.runtime_message_cursor,
            kinds=_PIPELINE_RUNTIME_MESSAGE_KINDS,
        )
        if not batch.messages:
            return False

        for item in batch.messages:
            message = item.message
            if message.kind is RuntimeMessageKind.INTERRUPT:
                reason = _string_payload_value(message.payload, "reason", "text")
                if not reason:
                    reason = message.message_id
                raise PipelineError(f"runtime interrupted: {reason}", stage=stage)

        ctx.runtime_messages.extend(batch.messages)
        ctx.runtime_message_cursor = batch.cursor
        return any(
            item.message.kind in _PROMPT_RUNTIME_MESSAGE_KINDS
            for item in batch.messages
        )

    async def _raise_if_runtime_interrupted(
        self,
        ctx: PipelineContext,
        *,
        stage: StageName,
    ) -> None:
        if ctx.runtime_message_bus is None:
            return

        batch = await ctx.runtime_message_bus.consume_after(
            ctx.runtime_message_cursor,
            kinds={RuntimeMessageKind.INTERRUPT},
        )
        for item in batch.messages:
            message = item.message
            reason = _string_payload_value(message.payload, "reason", "text")
            if not reason:
                reason = message.message_id
            raise PipelineError(f"runtime interrupted: {reason}", stage=stage)

    def _runtime_context_grounding(self, ctx: PipelineContext) -> dict[str, Any] | None:
        lines: list[str] = []
        run_context = ctx.run_context

        if run_context is not None:
            lines.append(f"session_id: {run_context.session_id}")
            lines.append(f"run_id: {run_context.run_id}")
            if run_context.agent_id is not None:
                lines.append(f"agent_id: {run_context.agent_id}")
            if run_context.parent_run_id is not None:
                lines.append(f"parent_run_id: {run_context.parent_run_id}")
            lines.append(f"environment: {run_context.environment.kind}")
            workspace_summary = run_context.environment.workspace_summary()
            if workspace_summary.display_name:
                lines.append(f"workspace: {workspace_summary.display_name}")
            if workspace_summary.local_root:
                lines.append(f"workspace_root: {workspace_summary.local_root}")
            if workspace_summary.default_cwd:
                lines.append(f"default_cwd: {workspace_summary.default_cwd}")

            budget = run_context.context_budget
            lines.append(
                "context_budget: "
                f"max_input={budget.max_input_tokens} "
                f"reserved_output={budget.reserved_output_tokens} "
                f"max_output={budget.max_output_tokens}"
            )

        if ctx.runtime_started_at is not None:
            elapsed = max(0, int(time.monotonic() - ctx.runtime_started_at))
            lines.append(f"elapsed_seconds: {elapsed}")

        active_approvals = _format_active_approvals(ctx.active_approvals)
        if active_approvals:
            lines.append(f"active_approvals: {active_approvals}")

        runtime_messages = _format_runtime_prompt_messages(ctx.runtime_messages)
        lines.extend(runtime_messages)

        if not lines:
            return None

        return {
            "role": "system",
            "content": "Runtime context\n" + "\n".join(lines),
        }

    async def mount(self, ctx: PipelineContext) -> None:
        self._ensure_toolset(ctx)
        for plugin_id in self._registry.plugin_ids():
            plugin = self._registry.get(plugin_id)
            mount_hook = plugin.hooks().get("mount")
            if mount_hook is not None:
                state = mount_hook(ctx=ctx, runtime=self._runtime)
                if state is not None:
                    ctx.plugin_states[plugin_id] = state

    async def shutdown(self, ctx: PipelineContext) -> None:
        callables = self._registry.get_hooks("on_shutdown")
        for fn in callables:
            result = fn(ctx=ctx, runtime=self._runtime)
            if isawaitable(result):
                await result

    async def run_turn(self, ctx: PipelineContext) -> PipelineContext:
        fork = None
        original_tape = ctx.tape
        ctx._handoff_done = False
        ctx.runtime_messages = []
        ctx.runtime_started_at = time.monotonic()
        self._ensure_toolset(ctx)
        observation_sink = _observation_sink(ctx)

        try:
            for stage in self.STAGES:
                try:
                    if stage in {"save_state", "render", "dispatch"}:
                        await self._raise_if_runtime_interrupted(ctx, stage=stage)
                        runtime_prompt_changed = False
                    else:
                        runtime_prompt_changed = await self._consume_runtime_messages(
                            ctx,
                            stage=stage,
                        )
                    handler = getattr(self, f"_stage_{stage}", None)
                    if handler is not None:
                        if stage == "run_model" and runtime_prompt_changed:
                            await self._stage_build_context(ctx)
                        if _tracer is not None:
                            _tracer.info(
                                "stage_start", stage=stage, entry_count=len(ctx.tape)
                            )
                        attributes = _stage_span_attributes(ctx, stage=stage)
                        span_name = _STAGE_SPAN_NAMES[stage]
                        with record_span(
                            span_name,
                            sink=observation_sink,
                            attributes=attributes,
                        ) as span:
                            await handler(ctx)
                            span.set_attribute("entry_count_after", len(ctx.tape))
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
                except FatalToolExecutionError:
                    raise
                except Exception as exc:
                    self._runtime.notify("on_error", stage=stage, error=exc)
                    raise PipelineError(str(exc), stage=stage) from exc

            if fork is not None:
                stable_tape_id = await ctx.storage.commit(fork)
                ctx.tape.tape_id = stable_tape_id

            return ctx
        except BaseException:
            if fork is not None:
                try:
                    ctx.storage.rollback(fork)
                except BaseException:
                    logger.exception(
                        "Failed to roll back pipeline storage fork %s",
                        fork.tape_id,
                    )
            ctx.tape = original_tape
            raise

    async def _stage_resolve_session(self, ctx: PipelineContext) -> None:
        pass

    async def _stage_load_state(self, ctx: PipelineContext) -> None:
        if ctx.storage is None:
            ctx.storage = self._runtime.call_first("provide_storage")
        if ctx.llm_provider is None:
            ctx.llm_provider = self._runtime.call_first("provide_llm")

        toolset = self._require_toolset(ctx, stage="load_state")
        ctx.tool_schemas = toolset.collect_schemas()

    async def _snapshot_build_context_inputs(
        self, ctx: PipelineContext
    ) -> Mapping[str, object]:
        if self._context_input_provider is None:
            return _EMPTY_CONTEXT_INPUTS
        inputs = await self._context_input_provider.snapshot(ctx)
        if not isinstance(inputs, Mapping):
            raise HookTypeError(
                "BuildContextInputProvider.snapshot must return a mapping",
                hook_name="build_context",
            )
        return MappingProxyType(dict(inputs))

    async def _call_build_context_hooks(
        self, ctx: PipelineContext
    ) -> list[list[dict[str, Any]]]:
        plugin_inputs = await self._snapshot_build_context_inputs(ctx)
        results: list[list[dict[str, Any]]] = []
        for binding in self._registry.get_hook_bindings("build_context"):
            try:
                if binding.capabilities is not None:
                    if PluginCapability.PENDING_FACT not in binding.capabilities:
                        raise HookError(
                            "capability-declared build_context hook must declare "
                            "PluginCapability.PENDING_FACT",
                            hook_name="build_context",
                        )
                    if binding.plugin_id not in plugin_inputs:
                        raise HookError(
                            f"no context input for capability-declared plugin "
                            f"'{binding.plugin_id}'",
                            hook_name="build_context",
                        )
                    kwargs = {"input": plugin_inputs[binding.plugin_id]}
                else:
                    compatibility_view = plugin_inputs.get(
                        binding.plugin_id, _EMPTY_CONTEXT_INPUTS
                    )
                    if not isinstance(compatibility_view, Mapping):
                        raise HookError(
                            f"context input for legacy plugin '{binding.plugin_id}' "
                            "must be a mapping",
                            hook_name="build_context",
                        )
                    kwargs = _build_context_hook_kwargs(
                        binding.hook,
                        ctx,
                        context_inputs=compatibility_view,
                    )
                result = binding.hook(**kwargs)
                if isawaitable(result):
                    result = await result
                if result is not None:
                    if not isinstance(result, list):
                        raise HookTypeError(
                            "Hook 'build_context' declared return_type=list, "
                            f"got {type(result).__name__}: {repr(result)[:100]}",
                            hook_name="build_context",
                        )
                    results.append(cast(list[dict[str, Any]], result))
            except HookError:
                raise
            except Exception as exc:
                raise HookError(str(exc), hook_name="build_context") from exc
        return results

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
                visible_entries = ctx.tape.windowed_entries()
                if not 0 <= window_start <= len(visible_entries):
                    logger.warning(
                        "resolve_context_window returned out-of-range window_start=%s for %s visible entries; skipping windowing",
                        window_start,
                        len(visible_entries),
                    )
                elif summary_anchor is not None and window_start > 0:
                    abs_window_start = ctx.tape.window_start + window_start
                    if abs_window_start > ctx.tape.window_start:
                        ctx.tape.handoff(summary_anchor, window_start=abs_window_start)
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

        grounding_results = await self._call_build_context_hooks(ctx)
        grounding: list[dict[str, Any]] = []
        runtime_context = self._runtime_context_grounding(ctx)
        if runtime_context is not None:
            grounding.append(runtime_context)
        for result in grounding_results:
            grounding.extend(result)

        interval = max(
            1, int(ctx.config.get("incremental_context_rebuild_interval", 5))
        )
        should_full_rebuild = (
            not incremental_enabled
            or force_full_rebuild
            or ctx.incremental_requires_full_rebuild
            or ctx.incremental_entry_count == 0
            or ctx.incremental_entry_count > len(ctx.tape)
            or ctx.incremental_window_start != ctx.tape.window_start
            or ctx.incremental_tool_round_count % interval == 0
        )

        if should_full_rebuild:
            view = TapeView.from_tape(ctx.tape)
            ctx.incremental_core_messages = builder.build_core_messages(view.entries)
            ctx.incremental_entry_count = len(ctx.tape)
            ctx.incremental_window_start = ctx.tape.window_start
            ctx.messages = builder.compose_messages(
                ctx.incremental_core_messages,
                grounding=grounding or None,
            )
            if grounding:
                ctx.incremental_grounding_start = 1 + builder.grounding_insert_index(
                    ctx.incremental_core_messages
                )
                ctx.incremental_grounding_count = len(grounding)
            else:
                ctx.incremental_grounding_start = len(ctx.messages)
                ctx.incremental_grounding_count = 0
            ctx.incremental_requires_full_rebuild = False
            return

        view = TapeView.from_tape(ctx.tape)
        visible_start = max(ctx.incremental_entry_count - ctx.tape.window_start, 0)
        new_entries = view.entries[visible_start:]
        builder.append_to_core_messages(ctx.incremental_core_messages, new_entries)
        ctx.incremental_entry_count = len(ctx.tape)
        ctx.incremental_window_start = ctx.tape.window_start
        grounding_start, grounding_count = builder.patch_messages(
            ctx.messages,
            ctx.incremental_core_messages,
            grounding=grounding or None,
            grounding_start=ctx.incremental_grounding_start,
            grounding_count=ctx.incremental_grounding_count,
        )
        ctx.incremental_grounding_start = grounding_start
        ctx.incremental_grounding_count = grounding_count

    async def _stage_run_model(self, ctx: PipelineContext) -> None:
        if ctx.llm_provider is None:
            logger.warning("No LLM provider available, skipping run_model")
            return

        max_tool_rounds = ctx.config.get("max_tool_rounds", 20)
        toolset = self._require_toolset(ctx, stage="run_model")

        for _round in range(max_tool_rounds):
            if await self._consume_runtime_messages(ctx, stage="run_model"):
                await self._stage_build_context(ctx)
            tool_dicts = (
                [s.to_openai_format() for s in ctx.tool_schemas]
                if ctx.tool_schemas
                else None
            )

            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            thinking_chunks: list[str] = []

            with record_span(
                "llm.generation",
                sink=_observation_sink(ctx),
                attributes=_llm_generation_attributes(
                    ctx,
                    message_count=len(ctx.messages),
                    tool_schema_count=len(tool_dicts or []),
                ),
            ) as llm_span:
                async for event in ctx.llm_provider.stream(
                    ctx.messages,
                    tools=tool_dicts,
                    thinking_config=ctx.config.get("thinking_config"),
                ):
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
                        llm_span.set_attribute("input_tokens", event.input_tokens)
                        llm_span.set_attribute("output_tokens", event.output_tokens)
                        llm_span.set_attribute(
                            "total_tokens",
                            event.input_tokens + event.output_tokens,
                        )
                        if event.provider_name:
                            llm_span.set_attribute("provider_name", event.provider_name)
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

                executable_calls: list[ToolCallRequest] = []
                checkpoint_entry_count: int | None = None

                for i, tc in enumerate(tool_calls):
                    tool_call = ToolCallRequest(
                        tool_call_id=tc["id"],
                        name=tc["name"],
                        arguments=tc["arguments"],
                    )
                    tc_payload: dict[str, Any] = {
                        "id": tool_call.tool_call_id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
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

                    validation_error = toolset.validate_tool_call(
                        tool_call,
                        schemas=ctx.tool_schemas,
                    )
                    if validation_error is not None:
                        rejection_msg = (
                            f"Tool call validation failed: {validation_error.message}"
                        )
                        ctx.tape.append(
                            Entry(
                                kind="tool_result",
                                payload={
                                    "tool_call_id": tool_call.tool_call_id,
                                    "content": rejection_msg,
                                },
                            )
                        )
                        if ctx.on_event:
                            await ctx.on_event(
                                ToolResultEvent(
                                    tool_call_id=tool_call.tool_call_id,
                                    name=tool_call.name,
                                    result=rejection_msg,
                                    is_error=True,
                                )
                            )
                        continue

                    if not toolset.is_proxy_affordance(tool_call.name):
                        approval = await toolset.approve_tool_call(tool_call, ctx=ctx)

                        if not approval.approved:
                            rejection_msg = (
                                f"Tool call rejected: {approval.reason or 'policy'}"
                            )
                            ctx.tape.append(
                                Entry(
                                    kind="tool_result",
                                    payload={
                                        "tool_call_id": tool_call.tool_call_id,
                                        "content": rejection_msg,
                                    },
                                )
                            )
                            if ctx.on_event:
                                await ctx.on_event(
                                    ToolResultEvent(
                                        tool_call_id=tool_call.tool_call_id,
                                        name=tool_call.name,
                                        result=rejection_msg,
                                        is_error=True,
                                    )
                                )
                            continue

                    executable_calls.append(tool_call)

                if executable_calls:
                    await self._raise_if_runtime_interrupted(
                        ctx,
                        stage="run_model",
                    )
                    max_size = ctx.config.get("max_tool_result_size", 10000)
                    structured_results_enabled = bool(
                        ctx.config.get("structured_results", False)
                    )
                    checkpoint_entry_count = len(ctx.tape)
                    with _structured_tool_result_scope(ctx, structured_results_enabled):
                        execution_results = await toolset.execute_tools(
                            executable_calls,
                            ctx=ctx,
                            options=ToolExecutionOptions(
                                timeout_seconds=ctx.config.get("tool_timeout_seconds"),
                            ),
                        )

                    for result in execution_results:
                        if result.is_error:
                            result_str = result.error_message
                            event_result: str | dict[str, Any] = result_str
                            is_error = True
                        else:
                            result_str = _format_result(
                                result.result,
                                structured=structured_results_enabled,
                                max_size=max_size,
                            )
                            event_result = (
                                result.result
                                if structured_results_enabled
                                and isinstance(result.result, dict)
                                else result_str
                            )
                            is_error = False

                        ctx.tape.append(
                            Entry(
                                kind="tool_result",
                                payload={
                                    "tool_call_id": result.tool_call_id,
                                    "content": result_str,
                                },
                            )
                        )
                        if ctx.on_event:
                            await ctx.on_event(
                                ToolResultEvent(
                                    tool_call_id=result.tool_call_id,
                                    name=result.name,
                                    result=event_result,
                                    is_error=is_error,
                                )
                            )

                if executable_calls:
                    ctx.incremental_tool_round_count += 1
                await self._stage_build_context(ctx)
                if checkpoint_entry_count is not None and len(
                    ctx.tape
                ) != checkpoint_entry_count + len(executable_calls):
                    ctx.incremental_tool_round_count = 0
                    ctx.incremental_entry_count = 0
                continue

            break

    async def _stage_save_state(self, ctx: PipelineContext) -> None:
        tape_len_before_checkpoint = len(ctx.tape)
        self._runtime.notify("on_checkpoint", ctx=ctx, runtime=self._runtime)
        if len(ctx.tape) != tape_len_before_checkpoint:
            ctx.incremental_requires_full_rebuild = True

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
