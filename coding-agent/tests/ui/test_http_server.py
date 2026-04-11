"""Tests for HTTP API server."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, ASGITransport
from httpx_sse import aconnect_sse

from coding_agent.approval.store import ApprovalStore
from coding_agent.ui.http_server import (
    APPROVAL_TIMEOUT_SECONDS,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SessionState,
    _broadcast_event,
    _session_to_dict,
    _wire_message_to_event,
    app,
    limiter,
    session_manager,
    sessions,
    wait_for_approval,
)
from coding_agent.wire import ToolCallEnd
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
)


@pytest.fixture(autouse=True)
async def clear_sessions():
    """Clear sessions before each test."""
    sessions.clear()
    # Clear rate limit storage to prevent 429 errors
    limiter.reset()
    # Also close any sessions in session_manager
    for session_id in list(session_manager.list_sessions()):
        try:
            await session_manager.close_session(session_id)
        except Exception:
            pass
    yield
    sessions.clear()
    # Cleanup session_manager
    for session_id in list(session_manager.list_sessions()):
        try:
            await session_manager.close_session(session_id)
        except Exception:
            pass


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
        response = await client.post("/sessions", json={})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    async def test_create_session_stores_in_memory(self, client):
        """Test that created session is stored in memory."""
        response = await client.post("/sessions", json={})
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
            json={"prompt": "test"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_prompt_streaming_events(self, client):
        """Test that prompt returns SSE events."""
        # Create session first
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Send prompt and collect SSE events
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
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
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_agent(_session_id: str, _prompt: str) -> None:
            started.set()
            await release.wait()
            await session_manager.get_session(session_id).wire.send(
                TurnEnd(
                    session_id=session_id,
                    completion_status=CompletionStatus.COMPLETED,
                    turn_id="test-turn",
                )
            )

        # Start prompt in background
        async def send_prompt():
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if sse.event == "TurnEnd":
                        break

        # Check turn_in_progress during execution
        with patch.object(session_manager, "run_agent", side_effect=fake_run_agent):
            task = asyncio.create_task(send_prompt())
            await asyncio.wait_for(started.wait(), timeout=1)
            assert session_manager.get_session(session_id).turn_in_progress
            release.set()
            await task

        assert not session_manager.get_session(session_id).turn_in_progress

    async def test_prompt_surfaces_subagent_tool_failure_in_real_http_session(
        self, client, tmp_path
    ):
        class ScriptedSubagentProvider:
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "scripted-subagent"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(self, messages, tools=None, **kwargs):
                del messages, kwargs
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(
                        tool_call_id="tc-http-subagent",
                        name="subagent",
                        arguments={"goal": "Inspect child task"},
                    )
                    yield DoneEvent()
                    return

                if self.calls == 2:
                    assert tools is not None
                    tool_names = {
                        tool["function"]["name"]
                        for tool in tools
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                    }
                    assert "subagent" not in tool_names
                    yield TextEvent(text="Child finished summary")
                    yield DoneEvent()
                    return

                yield TextEvent(text="Parent received child result")
                yield DoneEvent()

        provider = ScriptedSubagentProvider()
        session_id = "http-subagent-session"
        register_session(
            session_id,
            provider=provider,
            repo_path=tmp_path,
            approval_policy=ApprovalPolicy.YOLO,
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Please delegate this to a subagent"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd" and not events[-1]["data"]["agent_id"]:
                    break

        assert any(
            event["event"] == "ToolCallDelta"
            and event["data"]["tool_name"] == "subagent"
            for event in events
        )
        assert any(
            event["event"] == "ToolResultDelta"
            and event["data"]["tool_name"] == "subagent"
            and event["data"]["display_result"]
            == "Subagent completed: Child finished summary"
            and event["data"]["is_error"] is False
            and event["data"]["result"] is None
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == "child-1"
            and event["data"]["content"] == "Child finished summary"
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == ""
            and event["data"]["content"] == "Parent received child result"
            for event in events
        )
        assert provider.calls == 3


class TestConcurrentTurns:
    """Tests for 409 conflict on concurrent turns."""

    async def test_concurrent_turn_returns_409(self, client):
        """Test that concurrent turns return 409."""
        # Create session
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Manually set turn_in_progress
        sessions[session_id].turn_in_progress = True

        # Try to send another prompt
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()

    async def test_turn_in_progress_cleared_after_completion(self, client):
        """Test that turn_in_progress is cleared after turn completes."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Complete a turn
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if sse.event == "TurnEnd":
                    break

        # Should be able to start another turn
        assert not sessions[session_id].turn_in_progress
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello again"},
        )
        assert response.status_code == 200


class TestApprovalEndpoint:
    """Tests for approval endpoint."""

    async def test_approve_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/approve",
            json={"request_id": "req1", "approved": True},
        )
        assert response.status_code == 404

    async def test_approve_no_pending_request(self, client):
        """Test 400 when no pending approval (legacy check)."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Without adding request to ApprovalStore or setting legacy pending_approval,
        # it will fail the legacy check (400) if legacy session exists
        # But if no legacy session, it should try ApprovalStore (which returns 404)
        # Since create_session creates both, we expect 400 from legacy check
        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req1", "approved": True},
        )
        # Legacy session exists and pending_approval is None -> 400
        assert response.status_code == 400
        assert "no pending" in response.json()["detail"].lower()

    async def test_approve_request_id_mismatch(self, client):
        """Test 400 when request ID doesn't match (legacy check)."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Set up pending approval in legacy session
        sessions[session_id].pending_approval = {"request_id": "correct_id"}

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "wrong_id", "approved": True},
        )
        assert response.status_code == 400
        assert "mismatch" in response.json()["detail"].lower()

    async def test_approve_success(self, client):
        """Test successful approval."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Set up pending approval in legacy session
        sessions[session_id].pending_approval = {"request_id": "req123"}
        sessions[session_id].approval_event.clear()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"
        assert sessions[session_id].approval_event.is_set()

    async def test_deny_success(self, client):
        """Test successful denial."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Set up pending approval in legacy session
        sessions[session_id].pending_approval = {"request_id": "req123"}
        sessions[session_id].approval_event.clear()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "Too risky"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "denied"

    async def test_approve_with_approval_store(self, client):
        """Test approval using ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Add request to ApprovalStore directly (bypassing legacy check)
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        # Clear legacy pending_approval to test ApprovalStore path
        sessions[session_id].pending_approval = None

        # Now approve via submit_approval (which uses ApprovalStore)
        success = await session_manager.submit_approval(
            session_id=session_id,
            request_id="req123",
            approved=True,
            feedback="Looks good",
        )
        assert success is True


class TestEventsFanOut:
    """Tests for SSE fan-out with multiple clients."""

    async def test_event_queues_registered(self, client):
        """Test that event queues are registered for fan-out."""
        create_resp = await client.post("/sessions", json={})
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
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify the session has event_queues list
        assert hasattr(sessions[session_id], "event_queues")
        assert isinstance(sessions[session_id].event_queues, list)


class TestGetSession:
    """Tests for get session endpoint."""

    async def test_get_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.get("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_get_session_success(self, client):
        """Test getting session details."""
        create_resp = await client.post("/sessions", json={})
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
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["session_id"] == session_id
        assert session_id not in sessions

    async def test_close_session_broadcasts_event(self, client):
        """Test that closing session broadcasts to event queues."""
        create_resp = await client.post("/sessions", json={})
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
            completion_status=CompletionStatus.COMPLETED,
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

    def test_tool_result_delta_conversion_redacts_raw_result_payload(self):
        msg = ToolResultDelta(
            session_id="test123",
            call_id="call1",
            tool_name="bash_run",
            result={"stdout": "SECRET=abc123", "stderr": "", "exit_code": 0},
            display_result="command succeeded",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ToolResultDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["call_id"] == "call1"
        assert data["tool_name"] == "bash_run"
        assert data["display_result"] == "command succeeded"
        assert data["is_error"] is False
        assert data["result"] is None

    def test_tool_call_end_conversion_redacts_raw_result_payload(self):
        msg = ToolCallEnd(
            session_id="test123",
            call_id="call1",
            result="SECRET=abc123\nfull command output",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ToolCallEnd"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["call_id"] == "call1"
        assert data["result"] is None

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

    async def test_broadcast_keeps_slow_but_connected_queue(self):
        session = SessionState(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now(),
        )
        slow_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        fast_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)
        await slow_queue.put({"event": "old", "data": "{}"})
        session.event_queues = [slow_queue, fast_queue]

        event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, event)

        assert slow_queue in session.event_queues
        assert fast_queue in session.event_queues
        assert await fast_queue.get() == event


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
        assert response.feedback is not None
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
            assert response.feedback is not None
            assert "timeout" in response.feedback.lower()
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout


class TestIntegration:
    """Integration tests for the full flow."""

    async def test_full_session_lifecycle(self, client):
        """Test full session lifecycle: create -> prompt -> get -> close."""
        # Create session
        response = await client.post("/sessions", json={})
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
            json={"prompt": "Hello"},
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


class TestApprovalStoreIntegration:
    """Tests for ApprovalStore integration in SessionManager and HTTP server."""

    async def test_session_has_approval_store(self, client):
        """Test that newly created sessions have an ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        assert hasattr(session, "approval_store")
        assert isinstance(session.approval_store, ApprovalStore)

    async def test_approval_store_request_response(self, client):
        """Test that ApprovalStore can handle request/response cycle."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)

        # Add a request
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "echo test"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req-test",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        # Verify request was stored
        retrieved = session.approval_store.get_request("req-test")
        assert retrieved is not None
        assert retrieved.request_id == "req-test"

        # Respond to the request
        approval_resp = ApprovalResponse(
            session_id=session_id,
            request_id="req-test",
            approved=True,
            feedback="Approved",
        )
        success = session.approval_store.respond(approval_resp)
        assert success is True

    async def test_submit_approval_returns_bool(self, client):
        """Test that submit_approval returns boolean success status."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Try to approve non-existent request
        result = await session_manager.submit_approval(
            session_id=session_id,
            request_id="nonexistent",
            approved=True,
            feedback=None,
        )
        # Should return False since request wasn't added to store
        assert result is False

        # Now add the request and try again
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id, tool_name="bash", arguments={}, call_id="call1"
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="real-req",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        result = await session_manager.submit_approval(
            session_id=session_id, request_id="real-req", approved=True, feedback="Good"
        )
        assert result is True

    async def test_close_session_cleans_up_approval_store(self, client):
        """Test that closing session removes approval store from manager."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify store exists
        assert session_id in session_manager._approval_stores

        # Close session
        await session_manager.close_session(session_id)

        # Store should be cleaned up
        assert session_id not in session_manager._approval_stores
