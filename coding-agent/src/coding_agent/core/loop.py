"""Agent Loop: the main orchestration kernel."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from coding_agent.config.constants import MAX_TOOL_RESULT_SIZE as MAX_RESULT_SIZE
from coding_agent.core.doom import DoomDetector
from coding_agent.core.parallel import ParallelExecutor
from coding_agent.core.tape import Entry, Tape
from coding_agent.metrics import SessionMetrics, collector
from coding_agent.providers.base import StreamingResponse, ToolCall
from coding_agent.errors import ErrorHandler
from coding_agent.wire import (
    ApprovalRequest,
    ErrorMessage,
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
    error: Any = None  # AgentError if stop_reason is "error"


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
        metrics: Optional session metrics for performance tracking
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
        enable_parallel: bool = True,
        max_parallel: int = 5,
        metrics: SessionMetrics | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.tape = tape
        self.context = context
        self.consumer = consumer
        self.max_steps = max_steps
        self.doom_detector = DoomDetector(threshold=doom_threshold)
        self._enable_parallel = enable_parallel
        self._parallel_executor = ParallelExecutor(
            execute_fn=self.tools.execute,
            max_concurrency=max_parallel,
        )
        self._metrics = metrics

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

        try:
            return await self._run_turn_steps()
        except Exception as e:
            # Structured error handling
            error = ErrorHandler.handle_exception(e)
            
            # Log full traceback for debugging
            logger = logging.getLogger(__name__)
            logger.exception("Agent error during turn")
            
            # Display user-friendly error message
            await self.consumer.emit(ErrorMessage(
                content=error.format_for_display(),
            ))
            
            await self.consumer.emit(TurnEnd(
                stop_reason="error",
                final_message=error.message,
            ))
            
            return TurnOutcome(
                stop_reason="error",
                final_message=error.message,
                error=error,
            )

    async def _run_turn_steps(self) -> TurnOutcome:
        """Internal: run the turn steps."""
        for step in range(self.max_steps):
            # Emit step info
            await self.consumer.emit(StepInfo(step + 1, self.max_steps))

            # Build working set from tape
            messages = await self.context.build_working_set(self.tape)

            # Stream LLM response
            response = await self._stream_response(messages)

            if response.has_tool_calls:
                # Check if we should use parallel execution
                use_parallel = len(response.tool_calls) > 1 and self._can_parallelize(response.tool_calls)
                
                try:
                    if use_parallel:
                        await self._execute_tools_parallel(response.tool_calls)
                    else:
                        await self._execute_tools_sequential(response.tool_calls, step)
                except DoomLoopError:
                    # Return doom loop outcome
                    return TurnOutcome(
                        stop_reason="doom_loop",
                        final_message="[ABORTED] Repetitive tool call detected (doom loop)",
                        steps_taken=step + 1,
                    )
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
        start_time = time.time()
        
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
        
        # Record API call latency
        if self._metrics:
            latency = time.time() - start_time
            self._metrics.record_api_call(latency)
        
        return response

    def _can_parallelize(self, tool_calls: list[ToolCall]) -> bool:
        """Check if tool calls can be parallelized."""
        # Parallel execution disabled by config
        if not self._enable_parallel:
            return False
        
        if len(tool_calls) <= 1:
            return False
        
        # Check if any are high-risk and should be sequential
        high_risk = {"file_write", "file_replace", "bash"}
        risky_count = sum(1 for call in tool_calls if call.name in high_risk)
        
        # If multiple risky operations, be conservative
        if risky_count > 1:
            return False
        
        return True

    async def _execute_tools_parallel(
        self, 
        tool_calls: list[ToolCall]
    ) -> list[tuple[ToolCall, str]]:
        """Execute tools in parallel where possible."""
        # Record all tool calls and request approvals first
        approved_calls: list[ToolCall] = []
        denied_results: dict[str, str] = {}
        
        for call in tool_calls:
            self.tape.append(Entry.tool_call(call.id, call.name, call.arguments))
            await self.consumer.emit(ToolCallBegin(
                call_id=call.id,
                tool=call.name,
                args=call.arguments,
            ))
            
            # Request approval
            approval_req = ApprovalRequest(
                call_id=call.id,
                tool=call.name,
                args=call.arguments,
                risk_level=_get_risk_level(call.name),
            )
            approval = await self.consumer.request_approval(approval_req)
            
            if approval.decision == "approve":
                approved_calls.append(call)
            else:
                denied_results[call.id] = f"[DENIED] {approval.feedback or 'Tool call denied by user'}"
        
        # Check for doom loop before parallel execution
        for call in approved_calls:
            if self.doom_detector.observe(call.name, call.arguments):
                from coding_agent.core.doom import DoomLoopError
                raise DoomLoopError("Repetitive tool call detected")
        
        # Execute approved calls in parallel
        output: list[tuple[ToolCall, str]] = []
        
        if approved_calls:
            results = await self._parallel_executor.execute_all(approved_calls)
            
            # Build result lookup by call id
            result_map = {result.tool_call.id: result for result in results}
            
            # Emit end events in original order
            for call in tool_calls:
                if call.id in denied_results:
                    result_str = denied_results[call.id]
                else:
                    result = result_map.get(call.id)
                    if result:
                        result_str = result.result
                        # Record tool call metrics
                        if self._metrics:
                            self._metrics.record_tool_call(call.name, result.duration)
                        # Truncate if needed
                        if len(result_str) > MAX_RESULT_SIZE:
                            result_str = result_str[:MAX_RESULT_SIZE] + f"\n... ({len(result_str) - MAX_RESULT_SIZE} chars truncated)"
                    else:
                        result_str = "[ERROR] Result not found"
                
                self.tape.append(Entry.tool_result(call.id, result_str))
                await self.consumer.emit(ToolCallEnd(
                    call_id=call.id,
                    result=result_str,
                ))
                
                output.append((call, result_str))
        else:
            # All denied - record results
            for call in tool_calls:
                result_str = denied_results[call.id]
                self.tape.append(Entry.tool_result(call.id, result_str))
                await self.consumer.emit(ToolCallEnd(
                    call_id=call.id,
                    result=result_str,
                ))
                output.append((call, result_str))
        
        return output

    async def _execute_tools_sequential(
        self,
        tool_calls: list[ToolCall],
        step: int
    ) -> list[tuple[ToolCall, str]]:
        """Execute tools sequentially (original behavior)."""
        output = []
        for call in tool_calls:
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
                raise DoomLoopError("Doom loop detected")

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
            start_time = time.time()
            result = await self.tools.execute(call.name, call.arguments)
            duration = time.time() - start_time
            result = str(result) if result is not None else ""
            
            # Record tool call metrics
            if self._metrics:
                self._metrics.record_tool_call(call.name, duration)
            
            # Truncate result if too large (prevent context overflow)
            if len(result) > MAX_RESULT_SIZE:
                result = result[:MAX_RESULT_SIZE] + f"\n... ({len(result) - MAX_RESULT_SIZE} chars truncated)"
            
            # Record result
            self.tape.append(Entry.tool_result(call.id, result))
            await self.consumer.emit(ToolCallEnd(
                call_id=call.id,
                result=result,
            ))
            
            output.append((call, result))
        
        return output


class DoomLoopError(Exception):
    """Raised when a doom loop is detected."""
    pass


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
