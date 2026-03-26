"""Agent Loop: the main orchestration kernel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from coding_agent.core.doom import DoomDetector
from coding_agent.core.tape import Entry, Tape
from coding_agent.providers.base import StreamingResponse, ToolCall
from coding_agent.wire import (
    ApprovalRequest,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireConsumer,
)

if TYPE_CHECKING:
    from coding_agent.core.context import Context
    from coding_agent.providers.base import ChatProvider
    from coding_agent.tools.registry import ToolRegistry


@dataclass
class TurnOutcome:
    """Result of a turn."""
    stop_reason: str  # "no_tool_calls", "max_steps_reached", "doom_loop", "error"
    final_message: str | None = None
    steps_taken: int = 0


class AgentLoop:
    """The main agent loop: while True → call model → execute tools → feed results back.
    
    Args:
        provider: LLM provider for streaming responses
        tools: Tool registry for executing tool calls
        tape: Tape for storing conversation history
        context: Context builder for assembling working set
        consumer: Wire consumer for UI/approval
        max_steps: Maximum steps per turn
        doom_threshold: Threshold for doom loop detection
    """

    def __init__(
        self,
        provider: ChatProvider,
        tools: ToolRegistry,
        tape: Tape,
        context: Context,
        consumer: WireConsumer,
        max_steps: int = 30,
        doom_threshold: int = 3,
    ):
        self.provider = provider
        self.tools = tools
        self.tape = tape
        self.context = context
        self.consumer = consumer
        self.max_steps = max_steps
        self.doom_detector = DoomDetector(threshold=doom_threshold)

    async def run_turn(self, user_input: str) -> TurnOutcome:
        """Run a single conversation turn.
        
        Args:
            user_input: User's input message
            
        Returns:
            TurnOutcome with result details
        """
        # Append user message to tape
        self.tape.append(Entry.message("user", user_input))
        await self.consumer.emit(TurnBegin())

        for step in range(self.max_steps):
            # Emit step info
            await self.consumer.emit(StepInfo(step + 1, self.max_steps))

            # Build working set from tape
            messages = self.context.build_working_set(self.tape)

            # Stream LLM response
            response = await self._stream_response(messages)

            if response.has_tool_calls:
                # Execute each tool call
                for call in response.tool_calls:
                    # Record tool call
                    self.tape.append(Entry.tool_call(call.id, call.name, call.arguments))
                    await self.consumer.emit(ToolCallBegin(
                        call_id=call.id,
                        tool=call.name,
                        args=call.arguments,
                    ))

                    # Check for doom loop
                    if self.doom_detector.observe(call.name, call.arguments):
                        result_msg = "[ABORTED] Repetitive tool call detected (doom loop)"
                        self.tape.append(Entry.tool_result(call.id, result_msg))
                        await self.consumer.emit(ToolCallEnd(
                            call_id=call.id,
                            result=result_msg,
                        ))
                        await self.consumer.emit(TurnEnd(
                            stop_reason="doom_loop",
                            final_message=result_msg,
                        ))
                        return TurnOutcome(
                            stop_reason="doom_loop",
                            final_message=result_msg,
                            steps_taken=step + 1,
                        )

                    # Request approval (for now, headless auto-approves)
                    approval_req = ApprovalRequest(
                        call_id=call.id,
                        tool=call.name,
                        args=call.arguments,
                        risk_level=_get_risk_level(call.name),
                    )
                    approval = await self.consumer.request_approval(approval_req)

                    if approval.decision == "deny":
                        result_msg = f"[DENIED] {approval.feedback or 'Tool call denied by user'}"
                        self.tape.append(Entry.tool_result(call.id, result_msg))
                        await self.consumer.emit(ToolCallEnd(
                            call_id=call.id,
                            result=result_msg,
                        ))
                        continue

                    # Execute tool
                    result = await self.tools.execute(call.name, call.arguments)
                    
                    # Truncate result if too large (prevent context overflow)
                    MAX_RESULT_SIZE = 10000
                    if len(result) > MAX_RESULT_SIZE:
                        result = result[:MAX_RESULT_SIZE] + f"\n... ({len(result) - MAX_RESULT_SIZE} chars truncated)"
                    
                    # Record result
                    self.tape.append(Entry.tool_result(call.id, result))
                    await self.consumer.emit(ToolCallEnd(
                        call_id=call.id,
                        result=result,
                    ))
            else:
                # No tool calls = turn complete
                assistant_message = response.text
                self.tape.append(Entry.message("assistant", assistant_message))
                await self.consumer.emit(TurnEnd(
                    stop_reason="no_tool_calls",
                    final_message=assistant_message,
                ))
                return TurnOutcome(
                    stop_reason="no_tool_calls",
                    final_message=assistant_message,
                    steps_taken=step + 1,
                )

        # Max steps reached
        msg = f"Maximum steps ({self.max_steps}) reached"
        await self.consumer.emit(TurnEnd(
            stop_reason="max_steps_reached",
            final_message=msg,
        ))
        return TurnOutcome(
            stop_reason="max_steps_reached",
            final_message=msg,
            steps_taken=self.max_steps,
        )

    async def _stream_response(self, messages: list[dict]) -> StreamingResponse:
        """Stream LLM response and accumulate it.
        
        Args:
            messages: Working set messages
            
        Returns:
            Accumulated response
        """
        response = StreamingResponse()
        
        async for event in self.provider.stream(
            messages=messages,
            tools=self.tools.schemas(),
        ):
            match event.type:
                case "delta":
                    if event.text:
                        response.add_delta(event.text)
                        await self.consumer.emit(StreamDelta(text=event.text))
                case "tool_call":
                    if event.tool_call:
                        response.add_tool_call(event.tool_call)
                case "done":
                    break
                case "error":
                    # Log error but continue
                    response.add_delta(f"\n[Error: {event.error}]\n")
        
        return response


def _get_risk_level(tool_name: str) -> str:
    """Determine risk level for a tool."""
    high_risk = {"bash", "file_write", "file_replace"}
    medium_risk = {"file_patch"}
    
    if tool_name in high_risk:
        return "high"
    elif tool_name in medium_risk:
        return "medium"
    else:
        return "low"
