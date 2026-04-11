"""Headless consumer for batch mode (logs to console, auto-approves)."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)

logger = logging.getLogger(__name__)


class WireConsumer(Protocol):
    """Protocol for wire message consumers (TUI, headless, HTTP)."""

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the consumer."""
        ...

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval and wait for response."""
        ...


class HeadlessConsumer:
    """WireConsumer implementation for batch/headless mode.

    - Logs all messages to console
    - Auto-approves tool calls (yolo mode)
    - No interactive UI
    """

    def __init__(self, auto_approve: bool = True):
        self.auto_approve = auto_approve
        # Note: logging configuration is left to the application entry point
        # to avoid side effects from library code

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the consumer."""
        match msg:
            case TurnEnd(completion_status=status):
                logger.info(f"=== Turn End ({status}) ===")
            case StreamDelta(content=text):
                # Stream text to stdout in real-time
                print(text, end="", flush=True)
            case ToolCallDelta(tool_name=tool, arguments=args, call_id=cid):
                logger.info(f"[Tool Call] {tool} (id={cid}): {args}")
            case _:
                logger.debug(f"Message: {type(msg).__name__}")

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval and wait for response.

        In headless mode, this auto-approves based on configuration.
        """
        tool_name = req.tool_call.tool_name if req.tool_call is not None else "unknown"
        if self.auto_approve:
            logger.info(f"[Auto-approve] {tool_name}")
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
            )
        else:
            # In non-auto mode, deny by default in headless mode
            logger.warning(
                f"[Auto-deny] {tool_name} (headless mode without auto_approve)"
            )
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=False,
                feedback="Approval denied in headless mode without auto_approve",
            )
