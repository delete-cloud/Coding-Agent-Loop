from __future__ import annotations

import pytest

from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)
from coding_agent.approval import ApprovalCoordinator
from coding_agent.approval.runtime_messages import ApprovalDecisionConsumer
from coding_agent.ui.session_manager import MockProvider, SessionManager
from coding_agent.ui.session_store import InMemorySessionStore
from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta


def _approval_request(
    *,
    session_id: str = "session-approval",
    agent_id: str = "agent-approval",
    request_id: str = "approval-request-1",
    tool_name: str = "bash_run",
) -> ApprovalRequest:
    return ApprovalRequest(
        session_id=session_id,
        agent_id=agent_id,
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id=session_id,
            agent_id=agent_id,
            call_id=request_id,
            tool_name=tool_name,
            arguments={"cmd": "ls"},
        ),
    )


@pytest.mark.asyncio
async def test_approval_decision_consumer_applies_decisions_with_own_cursor() -> None:
    bus = InMemoryRuntimeMessageBus()
    coordinator = ApprovalCoordinator()
    request = _approval_request()
    coordinator.add_request(request)

    await bus.publish(
        RuntimeMessage(
            message_id="approval-decision-1",
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={
                "session_id": request.session_id,
                "request_id": request.request_id,
                "approved": True,
                "feedback": "approved by owner",
                "scope": "session",
            },
        )
    )
    await bus.publish(
        RuntimeMessage(
            message_id="user-steer-1",
            kind=RuntimeMessageKind.USER_STEER,
            payload={"text": "leave this for the pipeline cursor"},
        )
    )

    consumer = ApprovalDecisionConsumer(
        session_id=request.session_id,
        coordinator=coordinator,
    )
    result = await consumer.consume(bus, RuntimeMessageCursor())

    assert result.applied_request_ids == (request.request_id,)
    assert result.cursor.sequence == 1
    assert coordinator.is_session_approved(request)
    response = await coordinator.wait_for_response(request.request_id, timeout=0.01)
    assert response is not None
    assert response.approved is True
    assert response.feedback == "approved by owner"
    assert response.scope == "session"

    pipeline_batch = await bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.USER_STEER},
    )
    assert [item.message.message_id for item in pipeline_batch.messages] == [
        "user-steer-1"
    ]


@pytest.mark.asyncio
async def test_approval_decision_consumer_skips_unhashable_scope() -> None:
    bus = InMemoryRuntimeMessageBus()
    coordinator = ApprovalCoordinator()
    request = _approval_request(request_id="approval-unhashable-scope")
    coordinator.add_request(request)

    await bus.publish(
        RuntimeMessage(
            message_id="approval-decision-unhashable-scope",
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={
                "session_id": request.session_id,
                "request_id": request.request_id,
                "approved": True,
                "scope": [],
            },
        )
    )

    consumer = ApprovalDecisionConsumer(
        session_id=request.session_id,
        coordinator=coordinator,
    )
    result = await consumer.consume(bus, RuntimeMessageCursor())

    assert result.applied_request_ids == ()
    assert result.skipped_message_ids == ("approval-decision-unhashable-scope",)
    assert result.cursor.sequence == 1
    assert coordinator.get_request(request.request_id) is request


@pytest.mark.asyncio
async def test_session_manager_submit_approval_flows_through_runtime_bus(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session(
        repo_path=tmp_path,
        provider=MockProvider(),
        provider_name="mock",
        model_name="mock",
    )
    session = await manager.get_session_async(session_id)
    request = _approval_request(session_id=session_id, request_id="approval-request-2")
    session.approval_coordinator.add_request(request)
    session.pending_approval = session.approval_coordinator.projection()

    success = await manager.submit_approval(
        session_id=session_id,
        request_id=request.request_id,
        approved=False,
        feedback="deny from runtime bus",
        scope="once",
    )

    assert success is True
    assert session.approval_decision_cursor.sequence == 1
    bus_batch = await session.runtime_message_bus.consume_after(
        RuntimeMessageCursor(),
        kinds={RuntimeMessageKind.APPROVAL_DECISION},
    )
    assert [item.message.payload for item in bus_batch.messages] == [
        {
            "session_id": session_id,
            "request_id": request.request_id,
            "approved": False,
            "feedback": "deny from runtime bus",
            "scope": "once",
        }
    ]
    response = await session.approval_coordinator.wait_for_response(
        request.request_id,
        timeout=0.01,
    )
    assert response is not None
    assert response.approved is False
    assert response.feedback == "deny from runtime bus"


@pytest.mark.asyncio
async def test_session_manager_submit_approval_accepts_duplicate_published_decision(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session(
        repo_path=tmp_path,
        provider=MockProvider(),
        provider_name="mock",
        model_name="mock",
    )
    session = await manager.get_session_async(session_id)
    request = _approval_request(session_id=session_id, request_id="approval-retry")
    session.approval_coordinator.add_request(request)
    session.pending_approval = session.approval_coordinator.projection()

    await session.runtime_message_bus.publish(
        RuntimeMessage(
            message_id=f"approval_decision:{session_id}:{request.request_id}",
            kind=RuntimeMessageKind.APPROVAL_DECISION,
            payload={
                "session_id": session_id,
                "request_id": request.request_id,
                "approved": True,
                "feedback": "already published",
                "scope": "once",
            },
        )
    )

    success = await manager.submit_approval(
        session_id=session_id,
        request_id=request.request_id,
        approved=True,
        feedback="already published",
        scope="once",
    )

    assert success is True
    assert session.approval_decision_cursor.sequence == 1
    response = await session.approval_coordinator.wait_for_response(
        request.request_id,
        timeout=0.01,
    )
    assert response is not None
    assert response.approved is True
    assert response.feedback == "already published"
