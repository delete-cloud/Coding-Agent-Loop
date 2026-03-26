"""Rich TUI consumer that renders wire messages to rich components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coding_agent.ui.theme import theme
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireConsumer,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.rich_tui import CodingAgentTUI


class RichConsumer(WireConsumer):
    """WireConsumer that renders to Rich TUI."""

    def __init__(self, tui: CodingAgentTUI) -> None:
        self.tui = tui
        self.current_tool: dict[str, Any] | None = None

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the TUI."""
        match msg:
            case TurnBegin():
                self.tui.start_turn()
            
            case TurnEnd(stop_reason=reason, final_message=text):
                self.tui.end_turn(reason, text)
            
            case StreamDelta(text=text):
                if text:
                    self.tui.append_stream(text)
            
            case ToolCallBegin(call_id=cid, tool=tool, args=args):
                self.current_tool = {
                    "id": cid,
                    "name": tool,
                    "args": args,
                    "result": None,
                }
                self.tui.show_tool_call(tool, args)
            
            case ToolCallEnd(call_id=cid, result=result):
                if self.current_tool and self.current_tool["id"] == cid:
                    self.current_tool["result"] = result
                    self.tui.update_tool_result(result)
                self.current_tool = None
            
            case StepInfo(step_number=step_number, max_steps=max_steps):
                self.tui.update_step(step_number, max_steps)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval from user via TUI."""
        # For now, auto-approve in TUI mode (yolo)
        # TODO: Add interactive approval prompt
        return ApprovalResponse(
            call_id=req.call_id,
            decision="approve",
            scope="once",
        )
