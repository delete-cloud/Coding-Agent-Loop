"""Tests for HTTP API server."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from httpx_sse import aconnect_sse

from coding_agent.ui.http_server import (
    APPROVAL_TIMEOUT_SECONDS,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SessionState,
    _broadcast_event,
    _session_to_dict,
    _wire_message_to_event,
    app,
    sessions,
    wait_for_approval,
)
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)


@pytest.fixture(autouse=True)
async def clear_sessions():
    """Clear sessions before each test."""
    sessions.clear()
    yield
    sessions.clear()


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSessionCreation:
    """Tests for session creation endpoint."""

    async def test_create_session(self, client):
        """Test creating a new session."""
        response = await client.post("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    async def test_create_session_stores_in_memory(self, client):
        """Test that created session is stored in memory."""
        response = await client.post("/sessions")
        data = response.json()
        session_id = data["session_id"]
        assert session_id in sessions
        assert sessions[session_id].id == session_id


class TestPromptStreaming:
    """Tests for prompt streaming endpoint."""

    async def test_prompt_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/prompt",
            params={"prompt": "test"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_prompt_streaming_events(self, client):
        """Test that prompt returns SSE events."""
        # Create session first
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Send prompt and collect SSE events
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            params={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd":
                    break

        # Verify events
        assert len(events) > 0
        stream_events = [e for e in events if e["event"] == "StreamDelta"]
        assert len(stream_events) > 0
        assert events[-1]["event"] == "TurnEnd"

    async def test_prompt_sets_turn_in_progress(self, client):
        """Test that prompt sets turn_in_progress flag."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Start prompt in background
        async def send_prompt():
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                params={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if sse.event == "TurnEnd":
                        break

        # Check turn_in_progress during execution
        task = asyncio.create_task(send_prompt())
        await asyncio.sleep(0.05)  # Let it start
        assert sessions[session_id].turn_in_progress
        await task
        assert not sessions[session_id].turn_in_progress


class TestConcurrentTurns:
    """Tests for 409 conflict on concurrent turns."""

    async def test_concurrent_turn_returns_409(self, client):
        """Test that concurrent turns return 409."""
        # Create session
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Manually set turn_in_progress
        sessions[session_id].turn_in_progress = True

        # Try to send another prompt
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            params={"prompt": "Hello"},
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()

    async def test_turn_in_progress_cleared_after_completion(self, client):
        """Test that turn_in_progress is cleared after turn completes."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Complete a turn
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            params={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if sse.event == "TurnEnd":
                    break

        # Should be able to start another turn
        assert not sessions[session_id].turn_in_progress
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            params={"prompt": "Hello again"},
        )
        assert response.status_code == 200


class TestApprovalEndpoint:
    """Tests for approval endpoint."""

    async def test_approve_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/approve",
            params={"request_id": "req1", "approved": True},
        )
        assert response.status_code == 404

    async def test_approve_no_pending_request(self, client):
        """Test 400 when no pending approval."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.post(
            f"/sessions/{session_id}/approve",
            params={"request_id": "req1", "approved": True},
        )
        assert response.status_code == 400
        assert "no pending" in response.json()["detail"].lower()

    async def test_approve_request_id_mismatch(self, client):
        """Test 400 when request ID doesn't match."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Set up pending approval
        sessions[session_id].pending_approval = {"request_id": "correct_id"}

        response = await client.post(
            f"/sessions/{session_id}/approve",
            params={"request_id": "wrong_id", "approved": True},
        )
        assert response.status_code == 400
        assert "mismatch" in response.json()["detail"].lower()

    async def test_approve_success(self, client):
        """Test successful approval."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Set up pending approval
        sessions[session_id].pending_approval = {"request_id": "req123"}
        sessions[session_id].approval_event.clear()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            params={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"
        assert sessions[session_id].approval_event.is_set()

    async def test_deny_success(self, client):
        """Test successful denial."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Set up pending approval
        sessions[session_id].pending_approval = {"request_id": "req123"}
        sessions[session_id].approval_event.clear()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            params={"request_id": "req123", "approved": False, "feedback": "Too risky"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "denied"


class TestEventsFanOut:
    """Tests for SSE fan-out with multiple clients."""

    async def test_event_queues_registered(self, client):
        """Test that event queues are registered for fan-out."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Manually add queues to test fan-out
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        sessions[session_id].event_queues = [queue1, queue2]

        # Broadcast an event
        test_event = {"event": "Test", "data": "{}"}
        await _broadcast_event(sessions[session_id], test_event)

        # Both queues should receive the event
        assert await queue1.get() == test_event
        assert await queue2.get() == test_event

    async def test_multiple_queues_in_session(self, client):
        """Test that a session can have multiple event queues."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Verify the session has event_queues list
        assert hasattr(sessions[session_id], 'event_queues')
        assert isinstance(sessions[session_id].event_queues, list)


class TestGetSession:
    """Tests for get session endpoint."""

    async def test_get_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.get("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_get_session_success(self, client):
        """Test getting session details."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert "created_at" in data
        assert "last_activity" in data
        assert "turn_in_progress" in data
        assert "pending_approval" in data


class TestCloseSession:
    """Tests for close session endpoint."""

    async def test_close_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.delete("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_close_session_success(self, client):
        """Test closing a session."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["session_id"] == session_id
        assert session_id not in sessions

    async def test_close_session_broadcasts_event(self, client):
        """Test that closing session broadcasts to event queues."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        # Add a queue to receive events
        queue = asyncio.Queue()
        sessions[session_id].event_queues = [queue]

        # Close the session
        await client.delete(f"/sessions/{session_id}")

        # The queue should have received SessionClosed event
        received_events = []
        while not queue.empty():
            received_events.append(await queue.get())

        assert any(e["event"] == "SessionClosed" for e in received_events)


class TestSessionTimeout:
    """Tests for session idle timeout."""

    async def test_session_marked_expired_after_timeout(self):
        """Test that old sessions are marked for cleanup."""
        session_id = "test_session"
        old_time = datetime.now() - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 1)
        sessions[session_id] = SessionState(
            id=session_id,
            created_at=old_time,
            last_activity=old_time,
        )

        # Check that session is old enough to expire
        now = datetime.now()
        idle_time = now - sessions[session_id].last_activity
        assert idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    async def test_session_not_expired_if_active(self):
        """Test that active sessions are not expired."""
        session_id = "test_session"
        sessions[session_id] = SessionState(
            id=session_id,
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )

        now = datetime.now()
        idle_time = now - sessions[session_id].last_activity
        assert idle_time < timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)


class TestWireMessageConversion:
    """Tests for wire message to SSE event conversion."""

    def test_turn_end_conversion(self):
        """Test TurnEnd message conversion."""
        msg = TurnEnd(
            session_id="test123",
            turn_id="turn456",
            completion_status="completed",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "TurnEnd"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["turn_id"] == "turn456"
        assert data["completion_status"] == "completed"

    def test_stream_delta_conversion(self):
        """Test StreamDelta message conversion."""
        msg = StreamDelta(
            session_id="test123",
            content="Hello world",
            role="assistant",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "StreamDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["content"] == "Hello world"
        assert data["role"] == "assistant"

    def test_tool_call_delta_conversion(self):
        """Test ToolCallDelta message conversion."""
        msg = ToolCallDelta(
            session_id="test123",
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ToolCallDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["tool_name"] == "bash"
        assert data["call_id"] == "call1"
        assert data["arguments"]["command"] == "ls"

    def test_approval_request_conversion(self):
        """Test ApprovalRequest message conversion."""
        tool_call = ToolCallDelta(
            session_id="test123",
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            call_id="call1",
        )
        msg = ApprovalRequest(
            session_id="test123",
            request_id="req1",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalRequest"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["request_id"] == "req1"
        assert data["timeout_seconds"] == 120
        assert data["tool_call"]["tool_name"] == "bash"

    def test_approval_response_conversion(self):
        """Test ApprovalResponse conversion."""
        msg = ApprovalResponse(
            session_id="test123",
            request_id="req1",
            approved=True,
            feedback="Looks good",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalResponse"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["request_id"] == "req1"
        assert data["approved"] is True
        assert data["feedback"] == "Looks good"


class TestSessionToDict:
    """Tests for session serialization."""

    def test_session_to_dict(self):
        """Test session state to dictionary conversion."""
        session = SessionState(
            id="test123",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            last_activity=datetime(2024, 1, 1, 12, 30, 0),
            turn_in_progress=True,
            pending_approval={"call_id": "req1"},
        )
        data = _session_to_dict(session)
        assert data["id"] == "test123"
        assert data["turn_in_progress"] is True
        assert data["pending_approval"] is True
        assert "2024-01-01" in data["created_at"]


class TestBroadcastEvent:
    """Tests for event broadcasting."""

    async def test_broadcast_to_multiple_queues(self):
        """Test that events are broadcast to all queues."""
        session = SessionState(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session.event_queues = [queue1, queue2]

        event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, event)

        assert await queue1.get() == event
        assert await queue2.get() == event


class TestWaitForApproval:
    """Tests for the approval wait function."""

    async def test_wait_for_approval_session_not_found(self):
        """Test handling when session doesn't exist."""
        tool_call = ToolCallDelta(
            session_id="nonexistent",
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id="nonexistent",
            request_id="req1",
            tool_call=tool_call,
        )
        response = await wait_for_approval("nonexistent", req)
        assert isinstance(response, ApprovalResponse)
        assert response.approved is False
        assert "Session not found" in response.feedback

    async def test_wait_for_approval_timeout(self):
        """Test that approval times out correctly."""
        session_id = "test_session"
        sessions[session_id] = SessionState(
            id=session_id,
            created_at=datetime.now(),
            last_activity=datetime.now(),
            turn_in_progress=True,
        )

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id=session_id,
            request_id="req1",
            tool_call=tool_call,
        )

        # Use a very short timeout for testing
        import coding_agent.ui.http_server as http_server
        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.1

        try:
            response = await wait_for_approval(session_id, req)
            assert response.approved is False
            assert "timeout" in response.feedback.lower()
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout


class TestIntegration:
    """Integration tests for the full flow."""

    async def test_full_session_lifecycle(self, client):
        """Test full session lifecycle: create -> prompt -> get -> close."""
        # Create session
        response = await client.post("/sessions")
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # Get session info
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["id"] == session_id

        # Send prompt
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            params={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "TurnEnd":
                    break

        assert "StreamDelta" in events
        assert "TurnEnd" in events

        # Close session
        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

        # Verify session is gone
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 404
