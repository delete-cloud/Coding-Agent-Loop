from __future__ import annotations

from datetime import UTC, datetime

from coding_agent.events import (
    DisplayEvent,
    project_runtime_event_to_display,
    project_wire_sse_event_to_display,
)
from coding_agent.runtime_store import RuntimeEventRecord


def _runtime_event(
    event_kind: str,
    payload: dict[str, object],
    *,
    sequence: int | None = 7,
) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        event_id="event-1",
        run_id="run-1",
        event_kind=event_kind,
        payload=payload,
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        sequence=sequence,
    )


def test_projects_stream_delta_to_assistant_text_delta() -> None:
    runtime_event = _runtime_event(
        "wire.StreamDelta",
        {
            "message_type": "StreamDelta",
            "session_id": "session-1",
            "run_id": "run-1",
            "message": {
                "session_id": "session-1",
                "agent_id": "agent-1",
                "content": "hello",
                "role": "assistant",
            },
        },
    )

    display = project_runtime_event_to_display(runtime_event)

    assert display == DisplayEvent(
        source_event_id="event-1",
        run_id="run-1",
        sequence=7,
        display_kind="assistant_text_delta",
        payload={
            "agent_id": "agent-1",
            "content": "hello",
            "role": "assistant",
        },
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_projects_tool_result_without_raw_result_payload() -> None:
    runtime_event = _runtime_event(
        "wire.ToolResultDelta",
        {
            "message_type": "ToolResultDelta",
            "message": {
                "agent_id": "agent-1",
                "call_id": "call-1",
                "tool_name": "bash_run",
                "result": {"stdout": "SECRET=abc123"},
                "display_result": "command succeeded",
                "is_error": False,
            },
        },
    )

    display = project_runtime_event_to_display(runtime_event)

    assert display is not None
    assert display.display_kind == "tool_result"
    assert display.payload == {
        "agent_id": "agent-1",
        "call_id": "call-1",
        "tool_name": "bash_run",
        "display_result": "command succeeded",
        "is_error": False,
    }
    assert "result" not in display.payload


def test_projects_approval_request_to_prompt() -> None:
    runtime_event = _runtime_event(
        "wire.ApprovalRequest",
        {
            "message_type": "ApprovalRequest",
            "message": {
                "agent_id": "agent-1",
                "request_id": "approval-1",
                "timeout_seconds": 120,
                "tool_call": {
                    "call_id": "call-1",
                    "tool_name": "write_file",
                    "arguments": {"path": "README.md"},
                },
            },
        },
    )

    display = project_runtime_event_to_display(runtime_event)

    assert display is not None
    assert display.display_kind == "approval_prompt"
    assert display.payload == {
        "agent_id": "agent-1",
        "request_id": "approval-1",
        "timeout_seconds": 120,
        "tool_call": {
            "call_id": "call-1",
            "tool_name": "write_file",
            "arguments": {"path": "README.md"},
        },
    }


def test_projects_turn_end_to_final_result() -> None:
    runtime_event = _runtime_event(
        "wire.TurnEnd",
        {
            "message_type": "TurnEnd",
            "message": {
                "turn_id": "turn-1",
                "completion_status": "completed",
                "agent_id": "",
            },
        },
    )

    display = project_runtime_event_to_display(runtime_event)

    assert display is not None
    assert display.display_kind == "final_result"
    assert display.payload == {
        "agent_id": "",
        "turn_id": "turn-1",
        "completion_status": "completed",
    }


def test_skips_internal_or_unknown_runtime_events() -> None:
    runtime_event = _runtime_event(
        "model_request_started",
        {"request_id": "model-1"},
    )

    assert project_runtime_event_to_display(runtime_event) is None


def test_projects_live_wire_sse_event_to_display_event() -> None:
    display = project_wire_sse_event_to_display(
        {
            "event": "StreamDelta",
            "data": (
                '{"session_id":"session-1","agent_id":"agent-1",'
                '"content":"hello","role":"assistant",'
                '"timestamp":"2026-01-02T03:04:05+00:00"}'
            ),
        },
        source_event_id="live:event-1",
        current_run_id="run-live",
    )

    assert display == DisplayEvent(
        source_event_id="live:event-1",
        run_id="run-live",
        sequence=None,
        display_kind="assistant_text_delta",
        payload={
            "agent_id": "agent-1",
            "content": "hello",
            "role": "assistant",
        },
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_projects_live_tool_result_without_raw_result_payload() -> None:
    display = project_wire_sse_event_to_display(
        {
            "event": "ToolResultDelta",
            "data": (
                '{"session_id":"session-1","agent_id":"agent-1",'
                '"call_id":"call-1","tool_name":"bash_run",'
                '"result":null,"display_result":"command succeeded",'
                '"is_error":false}'
            ),
        },
        source_event_id="live:event-2",
        current_run_id="run-live",
        created_at=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
    )

    assert display is not None
    assert display.display_kind == "tool_result"
    assert display.payload == {
        "agent_id": "agent-1",
        "call_id": "call-1",
        "tool_name": "bash_run",
        "display_result": "command succeeded",
        "is_error": False,
    }
    assert "result" not in display.payload


def test_live_turn_end_uses_turn_id_as_run_id() -> None:
    display = project_wire_sse_event_to_display(
        {
            "event": "TurnEnd",
            "data": '{"turn_id":"turn-1","completion_status":"completed"}',
        },
        source_event_id="live:event-3",
        current_run_id=None,
        created_at=datetime(2026, 1, 2, 3, 4, 7, tzinfo=UTC),
    )

    assert display is not None
    assert display.run_id == "turn-1"
    assert display.display_kind == "final_result"
    assert display.payload == {
        "turn_id": "turn-1",
        "completion_status": "completed",
    }
