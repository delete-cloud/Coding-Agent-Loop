from __future__ import annotations

import uuid
import json
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.plugins.metrics import SessionMetricsPlugin
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.rich_consumer import WireConsumer


def _make_ask_user_handler(consumer: WireConsumer, session_id: str = "") -> Any:
    """Bridge DirectiveExecutor ask_user_handler to WireConsumer.request_approval."""

    async def handler(question: str, metadata: dict[str, Any] | None = None) -> bool:
        tool_name = (metadata or {}).get("tool_name", "")
        arguments = (metadata or {}).get("arguments", {})
        req = ApprovalRequest(
            session_id=session_id,
            request_id=uuid.uuid4().hex,
            tool=tool_name or "approval",
            args=arguments if arguments else {"question": question},
        )
        response = await consumer.request_approval(req)
        return response.approved

    return handler


class PipelineAdapter:
    def __init__(
        self,
        pipeline: Pipeline,
        ctx: PipelineContext,
        consumer: WireConsumer | None = None,
        agent_id: str = "",
    ) -> None:
        self._pipeline = pipeline
        self._ctx = ctx
        self._consumer = consumer
        self._agent_id = agent_id
        self._mounted = False
        self._closed = False
        # Wire approval flow: bridge consumer.request_approval to DirectiveExecutor
        if consumer is not None and pipeline._directive_executor is not None:
            pipeline._directive_executor._ask_user = _make_ask_user_handler(
                consumer, ctx.session_id
            )

    async def initialize(self) -> None:
        if self._mounted:
            return
        await self._pipeline.mount(self._ctx)
        self._mounted = True

    async def close(self) -> None:
        if self._closed:
            return
        if self._mounted:
            shutdown = getattr(self._pipeline, "shutdown", None)
            if callable(shutdown):
                shutdown_result = shutdown(self._ctx)
                if isawaitable(shutdown_result):
                    await shutdown_result
        self._closed = True

    def _message_agent_id(self) -> str:
        return self._agent_id

    def _is_visible_entry(self, entry: Entry) -> bool:
        return not entry.meta.get("skip_context")

    def _current_turn_entries(self) -> list[Entry]:
        entries = list(self._ctx.tape)
        turn_start = 0
        for index in range(len(entries) - 1, -1, -1):
            entry = entries[index]
            if (
                self._is_visible_entry(entry)
                and entry.kind == "message"
                and entry.payload.get("role") == "user"
            ):
                turn_start = index + 1
                break
        return entries[turn_start:]

    def _metrics_plugin(self) -> SessionMetricsPlugin | None:
        try:
            plugin = self._pipeline._registry.get("session_metrics")
        except Exception:
            return None
        if isinstance(plugin, SessionMetricsPlugin):
            return plugin
        wrapped = getattr(plugin, "plugin", None)
        if isinstance(wrapped, SessionMetricsPlugin):
            return wrapped
        return None

    async def run_turn(self, user_input: str) -> TurnOutcome:
        await self.initialize()
        initial_tool_calls = len(
            [
                entry
                for entry in self._ctx.tape.filter("tool_call")
                if self._is_visible_entry(entry)
            ]
        )
        user_entry = Entry(
            kind="message", payload={"role": "user", "content": user_input}
        )
        self._ctx.tape.append(user_entry)
        self._ctx.on_event = self._handle_event

        try:
            await self._pipeline.run_turn(self._ctx)
        except KeyboardInterrupt:
            self._ensure_user_message(user_entry)
            return await self._finish(StopReason.INTERRUPTED, error="Interrupted")
        except Exception as exc:
            self._ensure_user_message(user_entry)
            return await self._finish(StopReason.ERROR, error=str(exc))

        stop_reason = self._determine_stop_reason()
        return await self._finish(
            stop_reason,
            initial_tool_calls=initial_tool_calls,
        )

    def _ensure_user_message(self, user_entry: Entry) -> None:
        for entry in self._ctx.tape:
            if entry is user_entry:
                return
        self._ctx.tape.append(user_entry)

    def _normalize_tool_result_for_wire(
        self, result: str | dict[str, Any] | BaseModel
    ) -> tuple[str | dict[str, Any], str]:
        if isinstance(result, BaseModel):
            payload = result.model_dump()
            return payload, json.dumps(payload)
        if isinstance(result, str):
            return result, result
        if type(result) is dict:
            return result, json.dumps(result)
        return result, str(result)

    def _has_doom_signal(self, entries: list[Entry]) -> bool:
        for entry in reversed(entries):
            if entry.kind != "event":
                continue
            if entry.payload.get("event_type") == "doom_detected":
                return True

        doom_state = self._ctx.plugin_states.get("doom_detector", {})
        return bool(doom_state.get("doom_detected"))

    async def _handle_event(
        self,
        event: TextEvent
        | ThinkingEvent
        | ToolCallEvent
        | ToolResultEvent
        | UsageEvent
        | DoneEvent,
    ) -> None:
        if self._consumer is None:
            return

        if isinstance(event, TextEvent):
            await self._consumer.emit(
                StreamDelta(
                    content=event.text,
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                )
            )
        elif isinstance(event, ThinkingEvent):
            await self._consumer.emit(
                ThinkingDelta(
                    text=event.text,
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                )
            )
        elif isinstance(event, UsageEvent):
            metrics_plugin = self._metrics_plugin()
            if metrics_plugin is not None:
                metrics_plugin.record_token_usage(
                    event.input_tokens,
                    event.output_tokens,
                )
                metrics_plugin.on_checkpoint(ctx=self._ctx)
            await self._consumer.emit(
                TurnStatusDelta(
                    phase="idle",
                    tokens_in=event.input_tokens,
                    tokens_out=event.output_tokens,
                    model_name=event.provider_name,
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                )
            )
        elif isinstance(event, ToolCallEvent):
            await self._consumer.emit(
                ToolCallDelta(
                    tool_name=event.name,
                    arguments=event.arguments,
                    call_id=event.tool_call_id,
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                )
            )
        elif isinstance(event, ToolResultEvent):
            wire_result, display_result = self._normalize_tool_result_for_wire(
                event.result
            )
            await self._consumer.emit(
                ToolResultDelta(
                    call_id=event.tool_call_id,
                    tool_name=event.name,
                    result=wire_result,
                    display_result=display_result,
                    is_error=event.is_error,
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                )
            )

    def _determine_stop_reason(self) -> StopReason:
        entries = self._current_turn_entries()

        if self._has_doom_signal(entries):
            return StopReason.DOOM_LOOP

        tool_calls = [
            entry
            for entry in entries
            if self._is_visible_entry(entry) and entry.kind == "tool_call"
        ]
        if not tool_calls:
            return StopReason.NO_TOOL_CALLS

        last_tool_call_index = -1
        for index in range(len(entries) - 1, -1, -1):
            if entries[index].kind == "tool_call" and self._is_visible_entry(
                entries[index]
            ):
                last_tool_call_index = index
                break

        if last_tool_call_index != -1:
            for entry in entries[last_tool_call_index + 1 :]:
                if (
                    self._is_visible_entry(entry)
                    and entry.kind == "message"
                    and entry.payload.get("role") == "assistant"
                ):
                    return StopReason.NO_TOOL_CALLS

        return StopReason.MAX_STEPS_REACHED

    def _extract_final_message(self) -> str | None:
        for entry in reversed(self._current_turn_entries()):
            if (
                self._is_visible_entry(entry)
                and entry.kind == "message"
                and entry.payload.get("role") == "assistant"
            ):
                return entry.payload.get("content")
        return None

    def _count_steps(self, initial_tool_calls: int) -> int:
        visible_tool_calls = [
            entry
            for entry in self._ctx.tape.filter("tool_call")
            if self._is_visible_entry(entry)
        ]
        return max(0, len(visible_tool_calls) - initial_tool_calls)

    async def _finish(
        self,
        stop_reason: StopReason,
        error: str | None = None,
        initial_tool_calls: int = 0,
    ) -> TurnOutcome:
        outcome = TurnOutcome(
            stop_reason=stop_reason,
            final_message=self._extract_final_message(),
            steps_taken=self._count_steps(initial_tool_calls),
            error=error,
        )

        if self._consumer is not None:
            if stop_reason == StopReason.ERROR:
                status = CompletionStatus.ERROR
            elif stop_reason == StopReason.NO_TOOL_CALLS:
                status = CompletionStatus.COMPLETED
            else:
                status = CompletionStatus.BLOCKED

            await self._consumer.emit(
                TurnEnd(
                    session_id=self._ctx.session_id,
                    agent_id=self._message_agent_id(),
                    turn_id=uuid.uuid4().hex,
                    completion_status=status,
                )
            )

        return outcome
