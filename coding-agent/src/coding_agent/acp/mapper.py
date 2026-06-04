from __future__ import annotations

from typing import Any, Literal

from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
    WireMessage,
)

AcpStopReason = Literal[
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
]


def prompt_blocks_to_text(prompt: object) -> str:
    if not isinstance(prompt, list):
        raise ValueError("session/prompt params.prompt must be a list")

    parts: list[str] = []
    for index, block in enumerate(prompt):
        if not isinstance(block, dict):
            raise ValueError(f"prompt block {index} must be an object")
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError(
                    f"prompt text block {index} must include non-empty text"
                )
            parts.append(text)
            continue
        if block_type == "resource_link":
            uri = block.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ValueError(
                    f"prompt resource_link block {index} must include non-empty uri"
                )
            parts.append(uri)
            continue
        raise ValueError(f"unsupported prompt block type: {block_type!r}")

    if not parts:
        raise ValueError("session/prompt params.prompt must include content")
    return "\n\n".join(parts)


def wire_message_to_session_update(message: WireMessage) -> dict[str, Any] | None:
    if isinstance(message, StreamDelta):
        if message.role == "user":
            session_update = "user_message_chunk"
        else:
            session_update = "agent_message_chunk"
        return {
            "sessionUpdate": session_update,
            "content": {"type": "text", "text": message.content},
        }

    if isinstance(message, ThinkingDelta):
        return {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": message.text},
        }

    if isinstance(message, ToolCallDelta):
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": message.call_id,
            "title": message.tool_name,
            "kind": "other",
            "status": "pending",
            "rawInput": dict(message.arguments),
        }

    if isinstance(message, ToolResultDelta):
        status = "failed" if message.is_error else "completed"
        text = message.display_result or str(message.result)
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": message.call_id,
            "status": status,
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": text},
                }
            ],
        }

    if isinstance(message, TurnStatusDelta | TurnEnd):
        return None

    return None


def acp_stop_reason(message: TurnEnd) -> AcpStopReason:
    if message.completion_status == CompletionStatus.COMPLETED:
        return "end_turn"
    if message.completion_status == CompletionStatus.BLOCKED:
        return "max_turn_requests"
    return "refusal"
