from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.rich_consumer import WireConsumer


class PipelineAdapter:
    def __init__(
        self,
        pipeline: Pipeline,
        ctx: PipelineContext,
        consumer: WireConsumer | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._ctx = ctx
        self._consumer = consumer

    async def run_turn(self, user_input: str) -> TurnOutcome:
        initial_tool_calls = len(self._ctx.tape.filter("tool_call"))
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

    async def _handle_event(self, event: TextEvent | ToolCallEvent | DoneEvent) -> None:
        if self._consumer is None:
            return

        if isinstance(event, TextEvent):
            await self._consumer.emit(
                StreamDelta(content=event.text, session_id=self._ctx.session_id)
            )
        elif isinstance(event, ToolCallEvent):
            await self._consumer.emit(
                ToolCallDelta(
                    tool_name=event.name,
                    arguments=event.arguments,
                    call_id=event.tool_call_id,
                    session_id=self._ctx.session_id,
                )
            )

    def _determine_stop_reason(self) -> StopReason:
        doom_state = self._ctx.plugin_states.get("doom_detector", {})
        if doom_state.get("doom_detected"):
            return StopReason.DOOM_LOOP

        tool_calls = self._ctx.tape.filter("tool_call")
        if not tool_calls:
            return StopReason.NO_TOOL_CALLS

        last_entry = list(self._ctx.tape)[-1]
        if (
            last_entry.kind == "message"
            and last_entry.payload.get("role") == "assistant"
        ):
            return StopReason.NO_TOOL_CALLS

        return StopReason.MAX_STEPS_REACHED

    def _extract_final_message(self) -> str | None:
        for entry in reversed(list(self._ctx.tape)):
            if entry.kind == "message" and entry.payload.get("role") == "assistant":
                return entry.payload.get("content")
        return None

    def _count_steps(self, initial_tool_calls: int) -> int:
        return max(0, len(self._ctx.tape.filter("tool_call")) - initial_tool_calls)

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
                    turn_id=uuid.uuid4().hex,
                    completion_status=status,
                )
            )

        return outcome
