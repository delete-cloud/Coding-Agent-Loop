from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from inspect import isawaitable
from typing import Any, AsyncContextManager, cast

from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import tool

from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.agent_identity import effective_agent_id, legacy_agent_id_str
from coding_agent.wire.protocol import ToolCallDelta, WireMessage


logger = logging.getLogger(__name__)


ChildPipelineBuilder = Callable[..., tuple[Pipeline, PipelineContext]]

# Trace metadata keys reserved by subagent dispatch. Namespaced so they cannot
# collide with caller-supplied trace metadata.
_TRACE_KEY_PARENT_AGENT_ID = "subagent.parent_agent_id"
_TRACE_KEY_CHILD_AGENT_ID = "subagent.child_agent_id"

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
    parent_agent_id = legacy_agent_id_str(effective_agent_id(pipeline_ctx))
    coordinator = pipeline_ctx.config.get("child_worker_coordinator")
    if coordinator is not None:
        allocate_child_id = getattr(coordinator, "allocate_child_id", None)
        if not callable(allocate_child_id):
            raise TypeError(
                "child_worker_coordinator must provide callable allocate_child_id"
            )
        return str(allocate_child_id(parent_agent_id))

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


async def _publish_subagent_summary(
    pipeline_ctx: PipelineContext,
    *,
    session_id: str,
    summary: str,
    child_agent_id: str,
) -> None:
    publisher = pipeline_ctx.config.get("subagent_message_publisher")
    if publisher is None:
        return
    if not callable(publisher):
        raise TypeError("subagent_message_publisher must be callable")

    try:
        publish_result = publisher(
            session_id,
            summary,
            message_id=None,
            metadata={"source": "subagent", "child_agent_id": child_agent_id},
        )
        if isawaitable(publish_result):
            await publish_result
    except Exception:
        logger.warning(
            "Failed to publish subagent summary for session %s; returning summary anyway",
            session_id,
            exc_info=True,
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
        child_agent_id = _child_agent_id(__pipeline_ctx__)
        parent_run_context = __pipeline_ctx__.run_context
        parent_agent_id = legacy_agent_id_str(effective_agent_id(__pipeline_ctx__))
        # Prefer the run_context's session_id (canonical) and fall back to the
        # legacy ctx.session_id only if no run_context is attached. Empty
        # session identity must never silently become a fresh uuid in the
        # child, so fail fast here.
        parent_session_id = (
            parent_run_context.session_id
            if parent_run_context is not None
            else __pipeline_ctx__.session_id
        )
        if not parent_session_id:
            raise ValueError(
                "subagent requires a non-empty parent session_id; "
                "ensure PipelineContext.session_id or run_context is populated"
            )
        child_trace_metadata = {
            _TRACE_KEY_PARENT_AGENT_ID: parent_agent_id,
            _TRACE_KEY_CHILD_AGENT_ID: child_agent_id,
        }
        child_run_context = (
            parent_run_context.derive_child(
                run_id=uuid.uuid4().hex,
                agent_id=child_agent_id,
                trace_metadata=child_trace_metadata,
            )
            if parent_run_context is not None
            else None
        )
        child_pipeline_kwargs: dict[str, Any] = {
            "parent_provider": __pipeline_ctx__.llm_provider,
            "tape_fork": child_tape,
            "tool_filter": lambda tool_name: tool_name != "subagent",
            "session_id_override": parent_session_id,
            "agent_id_override": child_agent_id,
            "parent_run_id_override": (
                child_run_context.parent_run_id
                if child_run_context is not None
                else None
            ),
            "context_budget": (
                child_run_context.context_budget
                if child_run_context is not None
                else None
            ),
            "trace_metadata": (
                child_run_context.trace_metadata
                if child_run_context is not None
                else child_trace_metadata
            ),
        }
        if child_run_context is not None:
            child_pipeline_kwargs["environment"] = child_run_context.environment
            child_pipeline_kwargs["run_id_override"] = child_run_context.run_id
        child_pipeline, child_ctx = child_pipeline_builder(**child_pipeline_kwargs)
        child_ctx.config["agent_id"] = legacy_agent_id_str(
            effective_agent_id(child_ctx) or child_agent_id
        )
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
            summary = f"Subagent timed out after {timeout_seconds:g} seconds"
        else:
            if outcome is None:
                raise RuntimeError("subagent turn ended without outcome")
            summary = _summarize_subagent_outcome(outcome)

        _append_child_trace_to_parent(
            __pipeline_ctx__.tape,
            child_ctx.tape,
            base_length=child_base_length,
            child_agent_id=child_agent_id,
        )
        await _publish_subagent_summary(
            __pipeline_ctx__,
            session_id=parent_session_id,
            summary=summary,
            child_agent_id=child_agent_id,
        )
        return summary

    return subagent_dispatch
