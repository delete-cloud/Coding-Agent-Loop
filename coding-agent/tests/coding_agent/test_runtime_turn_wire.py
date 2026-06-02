from __future__ import annotations

from dataclasses import dataclass

import pytest

from coding_agent.wire import CompletionStatus, StreamDelta, TurnEnd, WireMessage
from coding_agent.wire.runtime import RuntimeTurnWire


@dataclass(frozen=True)
class FakeSession:
    id: str


@pytest.mark.asyncio
async def test_runtime_turn_wire_notifies_generic_error_sequence() -> None:
    emitted: list[tuple[FakeSession, WireMessage]] = []
    log_messages: list[str] = []
    session = FakeSession(id="session-1")
    turn_wire = RuntimeTurnWire(
        session_id="session-1",
        run_id="run-1",
        emit_message=lambda session, message: _record_message(
            emitted,
            session,
            message,
        ),
        log_exception=log_messages.append,
    )

    await turn_wire.notify_generic_error(session, RuntimeError("provider failed"))

    assert log_messages == ["HTTP session turn failed"]
    assert [item[0] for item in emitted] == [session, session]
    stream_delta = emitted[0][1]
    assert isinstance(stream_delta, StreamDelta)
    assert stream_delta.session_id == "session-1"
    assert stream_delta.agent_id == ""
    assert stream_delta.content == "Error: provider failed"
    assert stream_delta.role == "assistant"

    turn_end = emitted[1][1]
    assert isinstance(turn_end, TurnEnd)
    assert turn_end.session_id == "session-1"
    assert turn_end.agent_id == ""
    assert turn_end.turn_id == "run-1"
    assert turn_end.completion_status == CompletionStatus.ERROR


@pytest.mark.asyncio
async def test_runtime_turn_wire_rejects_session_mismatch() -> None:
    emitted: list[tuple[FakeSession, WireMessage]] = []
    turn_wire = RuntimeTurnWire(
        session_id="session-1",
        run_id="run-1",
        emit_message=lambda session, message: _record_message(
            emitted,
            session,
            message,
        ),
    )

    with pytest.raises(
        ValueError,
        match="runtime turn wire session mismatch",
    ):
        await turn_wire.notify_generic_error(
            FakeSession(id="other-session"),
            RuntimeError("provider failed"),
        )

    assert emitted == []


async def _record_message(
    emitted: list[tuple[FakeSession, WireMessage]],
    session: FakeSession,
    message: WireMessage,
) -> None:
    emitted.append((session, message))
