from __future__ import annotations

import pytest

from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    LocalWire,
    LocalWireConsumer,
    StreamDelta,
    WireMessage,
)


@pytest.mark.asyncio
async def test_local_wire_consumer_emits_to_wire_by_default() -> None:
    wire = LocalWire("session-1")
    consumer = LocalWireConsumer(wire, _approve)
    message = StreamDelta(session_id="session-1", content="hello")

    await consumer.emit(message)

    assert wire.consume_outgoing() is message


@pytest.mark.asyncio
async def test_local_wire_consumer_uses_custom_emit_handler() -> None:
    emitted: list[WireMessage] = []
    wire = LocalWire("session-1")
    consumer = LocalWireConsumer(
        wire,
        _approve,
        emit_handler=lambda message: _record_message(emitted, message),
    )
    message = StreamDelta(session_id="session-1", content="hello")

    await consumer.emit(message)

    assert emitted == [message]
    assert wire.consume_outgoing() is None


@pytest.mark.asyncio
async def test_local_wire_consumer_delegates_approval_handler() -> None:
    requests: list[ApprovalRequest] = []

    async def deny(req: ApprovalRequest) -> ApprovalResponse:
        requests.append(req)
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=False,
            feedback="denied by test",
        )

    consumer = LocalWireConsumer(LocalWire("session-1"), deny)
    request = ApprovalRequest(session_id="session-1", request_id="approval-1")

    response = await consumer.request_approval(request)

    assert requests == [request]
    assert response.session_id == "session-1"
    assert response.request_id == "approval-1"
    assert response.approved is False
    assert response.feedback == "denied by test"


async def _approve(req: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        session_id=req.session_id,
        request_id=req.request_id,
        approved=True,
    )


async def _record_message(
    emitted: list[WireMessage],
    message: WireMessage,
) -> None:
    emitted.append(message)
