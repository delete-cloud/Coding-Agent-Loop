from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from coding_agent.ui.collapse import (
    CollapseGroup,
    is_collapsible,
    is_compact,
    is_hidden,
)
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.stream_renderer import StreamingRenderer


class WireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...


class RichConsumer:
    def __init__(
        self,
        renderer: StreamingRenderer,
        thinking_enabled: Callable[[], bool] | None = None,
        thinking_effort: Callable[[], str] | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.renderer: StreamingRenderer = renderer
        self._stream_active: bool = False
        self._session_approved_tools: set[str] = set()
        self._collapse_group: CollapseGroup | None = None
        self._hidden_call_ids: set[str] = set()
        self._turn_start: float | None = None
        self._phase: str = "idle"
        self._turn_tokens_in: int = 0
        self._turn_tokens_out: int = 0
        self._thinking_heartbeat_task: asyncio.Task[None] | None = None
        self._model_name: str = ""
        self._context_percent: float = 0.0
        self._thinking_enabled = thinking_enabled or (lambda: True)
        self._thinking_effort = thinking_effort or (lambda: "medium")
        self._on_status = on_status

    def _prefix_child_text(self, text: str, agent_id: str) -> str:
        if not agent_id or not text:
            return text
        return f"[{agent_id}] {text}"

    def _prefix_child_tool_name(self, tool_name: str, agent_id: str) -> str:
        if not agent_id:
            return tool_name
        return f"[{agent_id}] {tool_name}"

    def _start_turn_timer(self) -> None:
        if self._turn_start is None:
            self._turn_start = time.perf_counter()

    def _elapsed(self) -> float:
        if self._turn_start is None:
            return 0.0
        return time.perf_counter() - self._turn_start

    def _thinking_heartbeat_interval(self) -> float:
        effort = self._thinking_effort().lower()
        if effort == "high":
            return 0.1
        if effort == "low":
            return 0.4
        return 0.2

    def _publish_status(self) -> None:
        if self._on_status is None:
            return
        self._on_status(
            {
                "phase": self._phase,
                "tokens_in": self._turn_tokens_in,
                "tokens_out": self._turn_tokens_out,
                "elapsed_seconds": self._elapsed(),
                "model_name": self._model_name,
                "context_percent": self._context_percent,
            }
        )

    def _ensure_thinking_heartbeat(self) -> None:
        if (
            self._thinking_heartbeat_task is not None
            and not self._thinking_heartbeat_task.done()
        ):
            return
        self._thinking_heartbeat_task = asyncio.create_task(
            self._thinking_heartbeat_loop()
        )

    async def _thinking_heartbeat_loop(self) -> None:
        try:
            while self._phase == "thinking":
                await asyncio.sleep(self._thinking_heartbeat_interval())
                if self._phase == "thinking":
                    self.renderer.thinking_update(elapsed_seconds=self._elapsed())
                    self._publish_status()
        except asyncio.CancelledError:
            pass

    def _stop_thinking_heartbeat(self) -> None:
        if (
            self._thinking_heartbeat_task is not None
            and not self._thinking_heartbeat_task.done()
        ):
            self._thinking_heartbeat_task.cancel()
        self._thinking_heartbeat_task = None

    def _end_thinking(self) -> None:
        if self._phase == "thinking":
            self._stop_thinking_heartbeat()
            self.renderer.thinking_end()
            self._phase = "streaming"
            self._publish_status()

    def _reset_turn_state(self) -> None:
        self._stop_thinking_heartbeat()
        self._turn_start = None
        self._phase = "idle"
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0
        self._model_name = ""
        self._context_percent = 0.0
        self._publish_status()

    def _flush_collapse_group(self, *, force: bool = False) -> None:
        group = self._collapse_group
        if group is None or group.is_empty:
            self._collapse_group = None
            return
        if group.pending_call_ids and not force:
            return
        hint: str | None = None
        if group.read_file_paths:
            hint = group.read_file_paths[-1]
        elif group.search_patterns:
            hint = f'"{group.search_patterns[-1]}"'
        self.renderer.collapsed_group(
            summary=group.summary_text(),
            duration=group.duration,
            has_error=group.has_error,
            hint=hint,
        )
        self._collapse_group = None

    async def emit(self, msg: WireMessage) -> None:
        match msg:
            case ThinkingDelta(text=text):
                self._start_turn_timer()
                if not self._thinking_enabled():
                    return
                if self._phase != "thinking":
                    self._phase = "thinking"
                    self.renderer.thinking_start()
                    self._ensure_thinking_heartbeat()
                    self._publish_status()
                if text:
                    self.renderer.thinking(text)

            case StreamDelta(content=text, agent_id=agent_id):
                self._start_turn_timer()
                self._end_thinking()
                if self._phase != "streaming":
                    self._phase = "streaming"
                    self._publish_status()
                self._flush_collapse_group()
                text = self._prefix_child_text(text, agent_id)
                if text:
                    if not self._stream_active:
                        self.renderer.stream_start()
                        self._stream_active = True
                    self.renderer.stream_text(text)

            case TurnStatusDelta(tokens_in=t_in, tokens_out=t_out):
                if msg.phase != "thinking":
                    self._end_thinking()
                self._phase = msg.phase
                self._turn_tokens_in = t_in
                self._turn_tokens_out = t_out
                self._model_name = msg.model_name
                self._context_percent = msg.context_percent
                self._publish_status()

            case ToolCallDelta(
                tool_name=tool, arguments=args, call_id=cid, agent_id=agent_id
            ):
                self._end_thinking()
                self._phase = "tool"
                self._publish_status()
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                display_tool = self._prefix_child_tool_name(tool, agent_id)
                if is_hidden(tool):
                    self._flush_collapse_group()
                    self._hidden_call_ids.add(cid)
                elif is_collapsible(tool):
                    if self._collapse_group is None:
                        self._collapse_group = CollapseGroup()
                    self._collapse_group.add_tool_call(cid, tool, args)
                elif is_compact(tool):
                    self._flush_collapse_group()
                    self.renderer.compact_tool_call(cid, display_tool, args)
                else:
                    self._flush_collapse_group()
                    self.renderer.tool_call(cid, display_tool, args)

            case (
                ToolResultDelta(
                    call_id=cid,
                    tool_name=tool,
                    result=result,
                    display_result=display_result,
                    is_error=err,
                    agent_id=agent_id,
                ) as msg
            ):
                rendered_result = msg.display_result or display_result
                if not rendered_result:
                    rendered_result = (
                        result if isinstance(result, str) else json.dumps(result)
                    )
                display_tool = self._prefix_child_tool_name(tool, agent_id)
                if cid in self._hidden_call_ids:
                    self._hidden_call_ids.discard(cid)
                elif self._collapse_group is not None and self._collapse_group.has_call(
                    cid
                ):
                    self._collapse_group.add_tool_result(cid, is_error=err)
                elif is_compact(tool):
                    self.renderer.compact_tool_result(
                        cid, display_tool, rendered_result, is_error=err
                    )
                else:
                    self.renderer.tool_result(
                        cid, display_tool, rendered_result, is_error=err
                    )

            case TurnEnd(completion_status=status):
                self._end_thinking()
                self._flush_collapse_group(force=True)
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                self.renderer.turn_end(status.value)
                self._reset_turn_state()

            case _:
                pass

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        from coding_agent.ui.approval_prompt import prompt_approval

        if req.tool in self._session_approved_tools:
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
                scope="session",
            )

        if self._stream_active:
            self.renderer.stream_end()
            self._stream_active = False

        response = await prompt_approval(self.renderer.console, req)

        if response.approved and response.scope == "session":
            self._session_approved_tools.add(req.tool)

        return response
