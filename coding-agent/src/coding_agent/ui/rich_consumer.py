"""Wire message consumer that dispatches to StreamingRenderer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.stream_renderer import StreamingRenderer


class WireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...


class RichConsumer:
    def __init__(self, renderer: StreamingRenderer) -> None:
        self.renderer = renderer
        self._stream_active = False
        self._session_approved_tools: set[str] = set()

    async def emit(self, msg: WireMessage) -> None:
        match msg:
            case StreamDelta(content=text):
                if text:
                    if not self._stream_active:
                        self.renderer.stream_start()
                        self._stream_active = True
                    self.renderer.stream_text(text)

            case ToolCallDelta(tool_name=tool, arguments=args, call_id=cid):
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                self.renderer.tool_call(cid, tool, args)

            case ToolResultDelta(
                call_id=cid, tool_name=tool, result=result, is_error=err
            ):
                self.renderer.tool_result(cid, tool, result, is_error=err)

            case TurnEnd(completion_status=status):
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                self.renderer.turn_end(status.value)

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
