"""Headless consumer for batch mode (logs to console, auto-approves)."""

from __future__ import annotations

import logging
from typing import Any

from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireConsumer,
    WireMessage,
)

logger = logging.getLogger(__name__)


class HeadlessConsumer(WireConsumer):
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
            case TurnBegin():
                logger.info("=== Turn Begin ===")
            case TurnEnd(stop_reason=reason, final_message=msg_text):
                logger.info(f"=== Turn End ({reason}) ===")
                if msg_text:
                    logger.info(f"Final message: {msg_text}")
            case StreamDelta(text=text):
                # Stream text to stdout in real-time
                print(text, end="", flush=True)
            case ToolCallBegin(call_id=cid, tool=tool, args=args):
                logger.info(f"[Tool Call] {tool} (id={cid}): {args}")
            case ToolCallEnd(call_id=cid, result=result):
                logger.info(f"[Tool Result] (id={cid}): {result[:200]}...")
            case _:
                logger.debug(f"Message: {type(msg).__name__}")

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval and wait for response.
        
        In headless mode, this auto-approves based on configuration.
        """
        if self.auto_approve:
            logger.info(f"[Auto-approve] {req.tool}")
            return ApprovalResponse(
                call_id=req.call_id,
                decision="approve",
                scope="once",
            )
        else:
            # In non-auto mode, deny by default in headless mode
            logger.warning(f"[Auto-deny] {req.tool} (headless mode without auto_approve)")
            return ApprovalResponse(
                call_id=req.call_id,
                decision="deny",
                scope="once",
                feedback="Approval denied in headless mode without auto_approve",
            )
