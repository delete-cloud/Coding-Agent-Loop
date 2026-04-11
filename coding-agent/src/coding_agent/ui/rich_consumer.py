"""Rich TUI consumer that renders wire messages to rich components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from coding_agent.ui.theme import theme
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.rich_tui import CodingAgentTUI


class WireConsumer(Protocol):
    """Protocol for wire message consumers."""

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the consumer."""
        ...

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval and wait for response."""
        ...


class RichConsumer:
    """WireConsumer that renders to Rich TUI."""

    def __init__(self, tui: CodingAgentTUI) -> None:
        self.tui = tui
        self.current_tool: dict[str, Any] | None = None

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the TUI."""
        match msg:
            case TurnEnd(completion_status=status):
                self.tui.end_turn(status, None)
            
            case StreamDelta(content=text):
                if text:
                    self.tui.append_stream(text)
            
            case ToolCallDelta(tool_name=tool, arguments=args, call_id=cid):
                self.current_tool = {
                    "id": cid,
                    "name": tool,
                    "args": args,
                    "result": None,
                }
                self.tui.show_tool_call(cid, tool, args)
            
            case _:
                # Handle unknown message types
                pass

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval from user via TUI."""
        # For now, auto-approve in TUI mode (yolo)
        # TODO: Add interactive approval prompt
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
        )
