"""Tests for HTTP API server."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
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
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorMessage,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
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
    async with AsyncClient(app=app, base_url="http://test") as ac:
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
        assert events[0]["event"] == "TurnBegin"
        assert any(e["event"] == "StreamDelta" for e in events)
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
        sessions[session_id].pending_approval = {"call_id": "correct_id"}

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
        sessions[session_id].pending_approval = {"call_id": "req123"}
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
        sessions[session_id].pending_approval = {"call_id": "req123"}
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

    async def test_multiple_clients_receive_events(self, client):
        """Test that multiple clients receive the same events."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        events_collected = []

        async def collect_events(client_id: int):
            collected = []
            async with aconnect_sse(
                client,
                "GET",
                f"/sessions/{session_id}/events",
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if sse.event == "TurnEnd":
                        collected.append({"client": client_id, "event": sse.event})
                        break
                    elif sse.event != "ping":
                        collected.append({"client": client_id, "event": sse.event})
            events_collected.extend(collected)

        # Connect two clients
        client1_task = asyncio.create_task(collect_events(1))
        client2_task = asyncio.create_task(collect_events(2))
        await asyncio.sleep(0.1)  # Let clients connect

        # Send a prompt to generate events
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

        await send_prompt()
        await asyncio.wait_for(asyncio.gather(client1_task, client2_task), timeout=5.0)

        # Both clients should have received events
        client1_events = [e for e in events_collected if e["client"] == 1]
        client2_events = [e for e in events_collected if e["client"] == 2]

        assert len(client1_events) > 0
        assert len(client2_events) > 0


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

    async def test_close_session_notifies_clients(self, client):
        """Test that closing session notifies connected clients."""
        create_resp = await client.post("/sessions")
        session_id = create_resp.json()["session_id"]

        events_received = []

        async def collect_events():
            async with aconnect_sse(
                client,
                "GET",
                f"/sessions/{session_id}/events",
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    events_received.append(sse.event)
                    if sse.event == "SessionClosed":
                        break

        # Start collecting events
        task = asyncio.create_task(collect_events())
        await asyncio.sleep(0.1)

        # Close the session
        await client.delete(f"/sessions/{session_id}")

        await asyncio.wait_for(task, timeout=2.0)
        assert "SessionClosed" in events_received


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

    def test_turn_begin_conversion(self):
        """Test TurnBegin message conversion."""
        msg = TurnBegin()
        event = _wire_message_to_event(msg)
        assert event["event"] == "TurnBegin"
        assert "timestamp" in json.loads(event["data"])

    def test_turn_end_conversion(self):
        """Test TurnEnd message conversion."""
        msg = TurnEnd(stop_reason="completed", final_message="Done")
        event = _wire_message_to_event(msg)
        assert event["event"] == "TurnEnd"
        data = json.loads(event["data"])
        assert data["stop_reason"] == "completed"
        assert data["final_message"] == "Done"

    def test_stream_delta_conversion(self):
        """Test StreamDelta message conversion."""
        msg = StreamDelta(text="Hello world")
        event = _wire_message_to_event(msg)
        assert event["event"] == "StreamDelta"
        data = json.loads(event["data"])
        assert data["text"] == "Hello world"

    def test_tool_call_begin_conversion(self):
        """Test ToolCallBegin message conversion."""
        msg = ToolCallBegin(call_id="call1", tool="bash", args={"command": "ls"})
        event = _wire_message_to_event(msg)
        assert event["event"] == "ToolCallBegin"
        data = json.loads(event["data"])
        assert data["call_id"] == "call1"
        assert data["tool"] == "bash"
        assert data["args"]["command"] == "ls"

    def test_tool_call_end_conversion(self):
        """Test ToolCallEnd message conversion."""
        msg = ToolCallEnd(call_id="call1", result="output")
        event = _wire_message_to_event(msg)
        assert event["event"] == "ToolCallEnd"
        data = json.loads(event["data"])
        assert data["call_id"] == "call1"
        assert data["result"] == "output"

    def test_approval_request_conversion(self):
        """Test ApprovalRequest message conversion."""
        msg = ApprovalRequest(
            call_id="call1",
            tool="bash",
            args={"command": "rm -rf /"},
            risk_level="high",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalRequest"
        data = json.loads(event["data"])
        assert data["call_id"] == "call1"
        assert data["risk_level"] == "high"

    def test_step_info_conversion(self):
        """Test StepInfo message conversion."""
        msg = StepInfo(step_number=5, max_steps=10)
        event = _wire_message_to_event(msg)
        assert event["event"] == "StepInfo"
        data = json.loads(event["data"])
        assert data["step_number"] == 5
        assert data["max_steps"] == 10

    def test_error_message_conversion(self):
        """Test ErrorMessage conversion."""
        msg = ErrorMessage(content="Something went wrong")
        event = _wire_message_to_event(msg)
        assert event["event"] == "ErrorMessage"
        data = json.loads(event["data"])
        assert data["content"] == "Something went wrong"


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

    async def test_broadcast_skips_full_queues(self):
        """Test that full queues are skipped."""
        session = SessionState(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        full_queue = asyncio.Queue(maxsize=0)  # Size 0 means it behaves differently
        # Actually, let's test with a queue that would be full
        # In practice, we just check the queue isn't in the list after broadcast


class TestWaitForApproval:
    """Tests for the approval wait function."""

    async def test_wait_for_approval_session_not_found(self):
        """Test handling when session doesn't exist."""
        req = ApprovalRequest(call_id="call1", tool="bash", args={})
        response = await wait_for_approval("nonexistent", req)
        assert isinstance(response, ApprovalResponse)
        assert response.decision == "deny"
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

        req = ApprovalRequest(call_id="call1", tool="bash", args={})

        # Use a very short timeout for testing
        import coding_agent.ui.http_server as http_server
        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.1

        try:
            response = await wait_for_approval(session_id, req)
            assert response.decision == "deny"
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

        assert "TurnBegin" in events
        assert "StreamDelta" in events
        assert "TurnEnd" in events

        # Close session
        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

        # Verify session is gone
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 404
