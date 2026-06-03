from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)
from coding_agent.approval import (
    ApprovalCoordinator,
    ApprovalDecisionService,
    ApprovalInteractionService,
    ApprovalRequestService,
    approval_decision_message_id,
)
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse, ToolCallDelta


@dataclass
class FakeApprovalRequestSession:
    id: str = "session-approval"
    current_turn_id: str | None = "run-approval"
    approval_coordinator: ApprovalCoordinator = field(
        default_factory=ApprovalCoordinator
    )
    runtime_message_bus: InMemoryRuntimeMessageBus = field(
        default_factory=InMemoryRuntimeMessageBus
    )
    approval_decision_cursor: RuntimeMessageCursor = field(
        default_factory=RuntimeMessageCursor
    )
    pending_approval: dict[str, object] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, object] | None = None
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))


def _request(
    *,
    session_id: str = "session-approval",
    request_id: str = "request-1",
    tool_name: str = "bash",
) -> ApprovalRequest:
    return ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name=tool_name,
            arguments={"command": "pwd"},
            call_id=f"call-{request_id}",
        ),
        timeout_seconds=1,
    )


def _service(
    persisted: list[str],
) -> ApprovalRequestService:
    async def _persist(session: FakeApprovalRequestSession) -> None:
        persisted.append(session.id)

    interactions = ApprovalInteractionService(store=None)
    decisions = ApprovalDecisionService(
        interactions=interactions,
        persist_session=_persist,
    )
    return ApprovalRequestService(
        interactions=interactions,
        decisions=decisions,
        persist_session=_persist,
    )


@pytest.mark.asyncio
async def test_approval_request_service_begins_pending_request() -> None:
    persisted: list[str] = []
    session = FakeApprovalRequestSession()
    request = _request(session_id=session.id)
    session.approval_event.set()
    session.approval_response = {"decision": "approve", "feedback": "old"}

    response = await _service(persisted).begin_request(session, request)

    assert response is None
    assert session.approval_coordinator.get_request(request.request_id) is request
    assert session.pending_approval == {
        "request_id": request.request_id,
        "tool_name": request.tool,
        "arguments": request.args,
    }
    assert session.approval_event.is_set() is False
    assert session.approval_response is None
    assert persisted == [session.id]


@pytest.mark.asyncio
async def test_approval_request_service_applies_prepublished_decision_after_begin() -> (
    None
):
    persisted: list[str] = []
    session = FakeApprovalRequestSession()
    request = _request(session_id=session.id, request_id="request-early")
    await session.runtime_message_bus.publish(
        RuntimeMessage(
            message_id=approval_decision_message_id(session.id, request.request_id),
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={
                "session_id": session.id,
                "request_id": request.request_id,
                "approved": True,
                "feedback": "arrived early",
                "scope": "once",
            },
        )
    )

    response = await _service(persisted).begin_request(session, request)

    assert response is not None
    assert response.approved is True
    assert response.feedback == "arrived early"
    assert session.approval_decision_cursor.sequence == 1
    assert session.pending_approval is None
    assert session.approval_response == {
        "request_id": request.request_id,
        "decision": "approve",
        "feedback": "arrived early",
    }
    assert persisted == [session.id, session.id]


@pytest.mark.asyncio
async def test_approval_request_service_resolves_wait_response_then_cleans_up() -> (
    None
):
    persisted: list[str] = []
    session = FakeApprovalRequestSession()
    request = _request(session_id=session.id, request_id="request-wait")
    service = _service(persisted)
    await service.begin_request(session, request)
    response = ApprovalResponse(
        session_id=session.id,
        request_id=request.request_id,
        approved=False,
        feedback="deny",
    )
    assert session.approval_coordinator.respond(response) is True
    waited_response = await session.approval_coordinator.wait_for_response(
        request.request_id,
        timeout=0.01,
    )
    assert waited_response is not None

    await service.resolve_wait_response(
        session,
        request.request_id,
        waited_response,
        expose_response=True,
    )

    assert session.pending_approval is None
    assert session.approval_response == {"decision": "deny", "feedback": "deny"}
    assert session.approval_event.is_set() is True

    await service.cleanup_after_wait(session, signal_event=False)

    assert session.pending_approval is None
    assert session.approval_response is None
    assert persisted == [session.id, session.id, session.id]
