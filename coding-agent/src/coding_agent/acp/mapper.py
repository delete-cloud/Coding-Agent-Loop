from __future__ import annotations

from typing import Any, Literal

from coding_agent.events import DisplayEvent
from coding_agent.wire.protocol import (
    ApprovalRequest,
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


def display_event_to_session_update(event: DisplayEvent) -> dict[str, Any] | None:
    payload = event.payload
    if event.display_kind == "assistant_text_delta":
        content = payload.get("content")
        if not isinstance(content, str):
            return None
        return {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": content},
        }

    if event.display_kind == "thinking_delta":
        text = payload.get("text")
        if not isinstance(text, str):
            return None
        return {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": text},
        }

    if event.display_kind == "tool_call":
        call_id = payload.get("call_id")
        tool_name = payload.get("tool_name")
        arguments = payload.get("arguments")
        if not isinstance(call_id, str) or not isinstance(tool_name, str):
            return None
        if not isinstance(arguments, dict):
            arguments = {}
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": call_id,
            "title": tool_name,
            "kind": _tool_kind(tool_name),
            "status": "pending",
            "rawInput": dict(arguments),
        }

    if event.display_kind == "tool_result":
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return None
        display_result = payload.get("display_result")
        if not isinstance(display_result, str):
            display_result = ""
        status = "failed" if payload.get("is_error") is True else "completed"
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": call_id,
            "status": status,
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": display_result},
                }
            ],
        }

    if event.display_kind == "approval_result":
        request_id = payload.get("request_id")
        if not isinstance(request_id, str):
            return None
        status = "completed" if payload.get("approved") is True else "failed"
        feedback = payload.get("feedback")
        text = feedback if isinstance(feedback, str) else ""
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": request_id,
            "status": status,
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": text},
                }
            ],
        }

    return None


def approval_request_to_permission_params(
    session_id: str,
    request: ApprovalRequest,
) -> dict[str, Any]:
    tool_call = request.tool_call
    if tool_call is None:
        tool_call_id = request.request_id
        title = request.tool or "Tool approval"
        raw_input = dict(request.args)
    else:
        tool_call_id = tool_call.call_id
        title = tool_call.tool_name
        raw_input = dict(tool_call.arguments)

    return {
        "sessionId": session_id,
        "toolCall": {
            "toolCallId": tool_call_id,
            "title": title,
            "kind": _tool_kind(title),
            "status": "pending",
            "rawInput": raw_input,
        },
        "options": [
            {
                "optionId": "allow-once",
                "name": "Allow once",
                "kind": "allow_once",
            },
            {
                "optionId": "allow-session",
                "name": "Allow for this session",
                "kind": "allow_always",
            },
            {
                "optionId": "reject-once",
                "name": "Reject",
                "kind": "reject_once",
            },
        ],
    }


def permission_outcome_to_approval(
    result: object,
) -> tuple[bool, str | None, str]:
    if not isinstance(result, dict):
        raise ValueError("permission response result must be an object")
    outcome = result.get("outcome")
    if not isinstance(outcome, dict):
        raise ValueError("permission response outcome must be an object")

    outcome_type = outcome.get("outcome")
    if outcome_type == "cancelled":
        return False, "Permission request cancelled by ACP client", "once"
    if outcome_type != "selected":
        raise ValueError(f"unsupported permission outcome: {outcome_type!r}")

    option_id = outcome.get("optionId")
    if option_id == "allow-once":
        return True, None, "once"
    if option_id == "allow-session":
        return True, None, "session"
    if option_id == "reject-once":
        return False, None, "once"
    raise ValueError(f"unsupported permission optionId: {option_id!r}")


def _tool_kind(tool_name: str) -> str:
    if tool_name in {"file_read", "repo_list"}:
        return "read"
    if tool_name in {"file_write", "apply_patch"}:
        return "edit"
    if tool_name in {"bash_run", "shell", "exec"}:
        return "execute"
    if "search" in tool_name:
        return "search"
    return "other"


def acp_stop_reason(message: TurnEnd) -> AcpStopReason:
    if message.completion_status == CompletionStatus.COMPLETED:
        return "end_turn"
    if message.completion_status == CompletionStatus.BLOCKED:
        return "max_turn_requests"
    return "refusal"
