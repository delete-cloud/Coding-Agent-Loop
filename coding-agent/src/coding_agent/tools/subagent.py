from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from inspect import isawaitable
from typing import Any, AsyncContextManager, cast

from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import tool

from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.wire.protocol import ToolCallDelta, WireMessage


ChildPipelineBuilder = Callable[..., tuple[Pipeline, PipelineContext]]

_READ_ONLY_CHILD_TOOLS = {
    "file_read",
    "glob_files",
    "grep_search",
    "todo_read",
    "repo_list",
    "git_status",
}


class _ChildWriteLeaseConsumer:
    def __init__(self, consumer: Any, pipeline_ctx: PipelineContext) -> None:
        self._consumer = consumer
        self._pipeline_ctx = pipeline_ctx
        self._lease_stack = contextlib.AsyncExitStack()
        self._lease_active = False

    async def emit(self, msg: WireMessage) -> None:
        if (
            isinstance(msg, ToolCallDelta)
            and msg.tool_name not in _READ_ONLY_CHILD_TOOLS
        ):
            await self._ensure_write_lease()
        if self._consumer is not None:
            await self._consumer.emit(msg)

    async def request_approval(self, req: Any) -> Any:
        if self._consumer is None:
            from coding_agent.wire.protocol import ApprovalResponse

            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
            )
        return await self._consumer.request_approval(req)

    async def close(self) -> None:
        await self._release_write_lease()

    async def _ensure_write_lease(self) -> None:
        if self._lease_active:
            return
        coordinator = self._pipeline_ctx.config.get("child_worker_coordinator")
        if coordinator is None:
            raise ValueError("child_worker_coordinator missing from pipeline config")
        acquire_write_lease = getattr(coordinator, "acquire_write_lease", None)
        if not callable(acquire_write_lease):
            raise TypeError(
                "child_worker_coordinator must provide callable acquire_write_lease"
            )
        lease = cast(AsyncContextManager[None], acquire_write_lease())
        await self._lease_stack.enter_async_context(lease)
        self._lease_active = True

    async def _release_write_lease(self) -> None:
        if not self._lease_active:
            return
        await self._lease_stack.aclose()
        self._lease_stack = contextlib.AsyncExitStack()
        self._lease_active = False


async def _close_adapter_if_supported(adapter: object) -> None:
    close = getattr(adapter, "close", None)
    if not callable(close):
        return
    maybe_awaitable = close()
    if not isawaitable(maybe_awaitable):
        return
    await maybe_awaitable


def _child_agent_id(pipeline_ctx: PipelineContext) -> str:
    coordinator = pipeline_ctx.config.get("child_worker_coordinator")
    if coordinator is not None:
        allocate_child_id = getattr(coordinator, "allocate_child_id", None)
        if not callable(allocate_child_id):
            raise TypeError(
                "child_worker_coordinator must provide callable allocate_child_id"
            )
        return str(allocate_child_id(str(pipeline_ctx.config.get("agent_id", ""))))

    parent_agent_id = str(pipeline_ctx.config.get("agent_id", ""))
    if parent_agent_id:
        return f"{parent_agent_id}.child-1"
    return "child-1"


def _summarize_subagent_outcome(outcome: TurnOutcome) -> str:
    if outcome.stop_reason == StopReason.ERROR:
        if outcome.error is None:
            raise ValueError("subagent error outcome missing error message")
        return f"Subagent failed: {outcome.error}"

    if outcome.final_message:
        return f"Subagent completed: {outcome.final_message}"

    return (
        f"Subagent finished ({outcome.stop_reason.value}, steps={outcome.steps_taken})"
    )


def _subagent_timeout_seconds(pipeline_ctx: PipelineContext) -> float:
    timeout = pipeline_ctx.config.get("subagent_timeout")
    if timeout is None:
        raise ValueError("subagent_timeout missing from pipeline config")
    return float(timeout)


def _fork_child_tape(parent_tape: Tape) -> Tape:
    entries = list(parent_tape)
    while entries and entries[-1].kind == "tool_call":
        entries.pop()
    return Tape(
        entries=entries,
        parent_id=parent_tape.tape_id,
        _window_start=parent_tape.window_start,
    )


def _append_child_trace_to_parent(
    parent_tape: Tape,
    child_tape: Tape,
    *,
    base_length: int,
    child_agent_id: str,
) -> None:
    for entry in list(child_tape)[base_length:]:
        parent_tape.append(
            Entry(
                kind=entry.kind,
                payload=dict(entry.payload),
                meta={
                    **entry.meta,
                    "skip_context": True,
                    "subagent_child": True,
                    "child_agent_id": child_agent_id,
                    "source_tape_id": child_tape.tape_id,
                    "source_entry_id": entry.id,
                },
            )
        )


def _child_trace_recorded(child_tape: Tape, *, base_length: int) -> bool:
    return len(child_tape) > base_length


def build_subagent_tool(child_pipeline_builder: ChildPipelineBuilder):
    @tool(
        name="subagent",
        description=(
            "Dispatch a sub-agent to work on a specific sub-task independently. "
            "The sub-agent gets its own context and tool access."
        ),
    )
    async def subagent_dispatch(
        goal: str, __pipeline_ctx__: PipelineContext | None = None
    ) -> str:
        if __pipeline_ctx__ is None:
            raise ValueError("subagent requires active pipeline context")

        child_tape = _fork_child_tape(__pipeline_ctx__.tape)
        child_pipeline, child_ctx = child_pipeline_builder(
            parent_provider=__pipeline_ctx__.llm_provider,
            tape_fork=child_tape,
            tool_filter=lambda tool_name: tool_name != "subagent",
            session_id_override=__pipeline_ctx__.session_id,
        )
        child_agent_id = _child_agent_id(__pipeline_ctx__)
        child_ctx.config["agent_id"] = child_agent_id
        timeout_seconds = _subagent_timeout_seconds(__pipeline_ctx__)
        child_base_length = len(child_tape)
        child_consumer = _ChildWriteLeaseConsumer(
            __pipeline_ctx__.config.get("wire_consumer"),
            __pipeline_ctx__,
        )
        child_adapter = PipelineAdapter(
            pipeline=child_pipeline,
            ctx=child_ctx,
            consumer=child_consumer,
            agent_id=child_agent_id,
        )
        outcome: TurnOutcome | None = None
        timed_out = False
        try:
            outcome = await asyncio.wait_for(
                child_adapter.run_turn(goal), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            await child_consumer.close()
            await _close_adapter_if_supported(child_adapter)

        if timed_out:
            recorded_progress = _child_trace_recorded(
                child_ctx.tape,
                base_length=child_base_length,
            )
            _append_child_trace_to_parent(
                __pipeline_ctx__.tape,
                child_ctx.tape,
                base_length=child_base_length,
                child_agent_id=child_agent_id,
            )
            if recorded_progress:
                return (
                    f"Subagent timed out after {timeout_seconds:g} seconds "
                    "after recording partial child progress"
                )
            return (
                f"Subagent timed out after {timeout_seconds:g} seconds "
                "with no child progress recorded"
            )

        _append_child_trace_to_parent(
            __pipeline_ctx__.tape,
            child_ctx.tape,
            base_length=child_base_length,
            child_agent_id=child_agent_id,
        )
        if outcome is None:
            raise RuntimeError("subagent turn ended without outcome")
        return _summarize_subagent_outcome(outcome)

    return subagent_dispatch
