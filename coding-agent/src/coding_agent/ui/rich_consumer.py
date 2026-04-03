from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from coding_agent.ui.collapse import CollapseGroup, is_collapsible
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
        self._collapse_group: CollapseGroup | None = None

    def _flush_collapse_group(self) -> None:
        group = self._collapse_group
        if group is None or group.is_empty:
            self._collapse_group = None
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
            case StreamDelta(content=text):
                self._flush_collapse_group()
                if text:
                    if not self._stream_active:
                        self.renderer.stream_start()
                        self._stream_active = True
                    self.renderer.stream_text(text)

            case ToolCallDelta(tool_name=tool, arguments=args, call_id=cid):
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                if is_collapsible(tool):
                    if self._collapse_group is None:
                        self._collapse_group = CollapseGroup()
                    self._collapse_group.add_tool_call(cid, tool, args)
                else:
                    self._flush_collapse_group()
                    self.renderer.tool_call(cid, tool, args)

            case ToolResultDelta(
                call_id=cid, tool_name=tool, result=result, is_error=err
            ):
                if self._collapse_group is not None and self._collapse_group.has_call(
                    cid
                ):
                    self._collapse_group.add_tool_result(cid, is_error=err)
                else:
                    self.renderer.tool_result(cid, tool, result, is_error=err)

            case TurnEnd(completion_status=status):
                self._flush_collapse_group()
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
