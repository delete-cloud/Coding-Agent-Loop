from __future__ import annotations

import asyncio

import pytest

from coding_agent.ui.session_manager import SessionManager
from coding_agent.ui.session_store import InMemorySessionStore
from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta


def _request(session_id: str, request_id: str, tool_name: str) -> ApprovalRequest:
    return ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name=tool_name,
            arguments={"path": f"/{tool_name}"},
            call_id=f"call-{request_id}",
        ),
        timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_wait_for_http_approval_routes_each_response_to_matching_request() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True

    request_one = _request(session_id, "req-1", "bash")
    request_two = _request(session_id, "req-2", "write_file")

    wait_one = asyncio.create_task(
        manager.wait_for_http_approval(session_id, request_one, timeout_seconds=1)
    )
    wait_two = asyncio.create_task(
        manager.wait_for_http_approval(session_id, request_two, timeout_seconds=1)
    )
    await asyncio.sleep(0)

    approved_two = await manager.submit_approval(
        session_id=session_id,
        request_id="req-2",
        approved=False,
        feedback="deny second",
    )
    approved_one = await manager.submit_approval(
        session_id=session_id,
        request_id="req-1",
        approved=True,
        feedback="approve first",
    )

    response_one = await wait_one
    response_two = await wait_two

    assert approved_two is True
    assert approved_one is True
    assert response_one.request_id == "req-1"
    assert response_one.approved is True
    assert response_one.feedback == "approve first"
    assert response_two.request_id == "req-2"
    assert response_two.approved is False
    assert response_two.feedback == "deny second"


@pytest.mark.asyncio
async def test_wait_for_http_approval_reuses_session_scope_approval_for_same_tool() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True

    initial_request = _request(session_id, "req-1", "bash")
    initial_wait = asyncio.create_task(
        manager.wait_for_http_approval(session_id, initial_request, timeout_seconds=1)
    )
    await asyncio.sleep(0)

    approved = await manager.submit_approval(
        session_id=session_id,
        request_id="req-1",
        approved=True,
        feedback="approve for session",
        scope="session",
    )
    initial_response = await initial_wait

    repeated_request = _request(session_id, "req-2", "bash")
    repeated_response = await manager.wait_for_http_approval(
        session_id, repeated_request, timeout_seconds=1
    )

    assert approved is True
    assert initial_response.approved is True
    assert initial_response.scope == "session"
    assert repeated_response.approved is True
    assert repeated_response.scope == "session"
    assert session.approval_store.get_request("req-2") is None
    assert session.pending_approval is None


@pytest.mark.asyncio
async def test_wait_for_http_approval_reuses_legacy_always_scope_as_session() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True

    initial_request = _request(session_id, "req-1", "bash")
    initial_wait = asyncio.create_task(
        manager.wait_for_http_approval(session_id, initial_request, timeout_seconds=1)
    )
    await asyncio.sleep(0)

    approved = await manager.submit_approval(
        session_id=session_id,
        request_id="req-1",
        approved=True,
        feedback="approve legacy always",
        scope="always",
    )
    initial_response = await initial_wait

    repeated_request = _request(session_id, "req-2", "bash")
    repeated_response = await manager.wait_for_http_approval(
        session_id, repeated_request, timeout_seconds=1
    )

    assert approved is True
    assert initial_response.approved is True
    assert initial_response.scope == "always"
    assert repeated_response.approved is True
    assert repeated_response.scope == "session"
    assert session.approval_store.get_request("req-2") is None


@pytest.mark.asyncio
async def test_wait_for_http_approval_returns_timeout_response_when_no_reply_arrives() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True

    response = await manager.wait_for_http_approval(
        session_id,
        _request(session_id, "req-timeout", "bash"),
        timeout_seconds=0.01,
    )

    assert response.request_id == "req-timeout"
    assert response.approved is False
    assert response.feedback == "Approval timeout or error"
    assert session.pending_approval is None
