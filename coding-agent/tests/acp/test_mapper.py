from __future__ import annotations

from coding_agent.acp.mapper import (
    acp_stop_reason,
    prompt_blocks_to_text,
    wire_message_to_session_update,
)
from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
)


def test_prompt_blocks_to_text_accepts_text_blocks() -> None:
    prompt = prompt_blocks_to_text(
        [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
    )

    assert prompt == "first\n\nsecond"


def test_wire_mapper_converts_agent_message_chunk() -> None:
    update = wire_message_to_session_update(
        StreamDelta(session_id="sess-1", content="hello")
    )

    assert update == {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "hello"},
    }


def test_wire_mapper_converts_tool_call_and_tool_result_updates() -> None:
    tool_call = wire_message_to_session_update(
        ToolCallDelta(
            session_id="sess-1",
            tool_name="bash_run",
            arguments={"cmd": "pwd"},
            call_id="call-1",
        )
    )
    tool_result = wire_message_to_session_update(
        ToolResultDelta(
            session_id="sess-1",
            call_id="call-1",
            tool_name="bash_run",
            result={"stdout": "/repo"},
            display_result="/repo",
            is_error=False,
        )
    )

    assert tool_call == {
        "sessionUpdate": "tool_call",
        "toolCallId": "call-1",
        "title": "bash_run",
        "kind": "other",
        "status": "pending",
        "rawInput": {"cmd": "pwd"},
    }
    assert tool_result == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "call-1",
        "status": "completed",
        "content": [
            {
                "type": "content",
                "content": {"type": "text", "text": "/repo"},
            }
        ],
    }


def test_acp_stop_reason_maps_turn_end_statuses() -> None:
    assert (
        acp_stop_reason(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        == "end_turn"
    )
    assert (
        acp_stop_reason(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.BLOCKED)
        )
        == "max_turn_requests"
    )
    assert (
        acp_stop_reason(TurnEnd(turn_id="t1", completion_status=CompletionStatus.ERROR))
        == "refusal"
    )
