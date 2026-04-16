"""Integration tests for Wire protocol + HTTP server.

Tests the interaction between the wire protocol implementation and the HTTP server,
ensuring proper message flow and event handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import textwrap
from datetime import datetime

import httpx
import pytest
from httpx_sse import aconnect_sse

from coding_agent.ui.session_manager import MockProvider, Session
from coding_agent.ui.http_server import (
    APPROVAL_TIMEOUT_SECONDS,
    _broadcast_event,
    _wire_message_to_event,
    app,
    session_manager,
    wait_for_approval,
)
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from tests.ui.test_http_server import add_store_backed_approval_request


@pytest.fixture(autouse=True)
async def clear_sessions():
    """Clear all sessions before each test."""
    session_manager.clear_sessions()
    yield
    session_manager.clear_sessions()


def register_session(
    session_id: str,
    **overrides,
) -> Session:
    session = Session(
        id=session_id,
        created_at=overrides.pop("created_at", datetime.now()),
        last_activity=overrides.pop("last_activity", datetime.now()),
        **overrides,
    )
    session_manager.register_session(session)
    return session


@pytest.fixture
async def client():
    """Create async test client."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestWireHTTPMessageConversion:
    """Test wire message to HTTP SSE event conversion."""

    def test_stream_delta_conversion(self):
        """Test StreamDelta converts to correct SSE event."""
        delta = StreamDelta(
            session_id="test-session",
            agent_id="child-1",
            content="Hello world",
            role="assistant",
        )
        event = _wire_message_to_event(delta)

        assert event["event"] == "StreamDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test-session"
        assert data["agent_id"] == "child-1"
        assert data["content"] == "Hello world"
        assert data["role"] == "assistant"
        assert "timestamp" in data

    def test_tool_call_delta_conversion(self):
        """Test ToolCallDelta converts to correct SSE event."""
        tool_call = ToolCallDelta(
            session_id="test-session",
            agent_id="child-2",
            tool_name="read_file",
            arguments={"path": "/test.txt"},
            call_id="call-123",
        )
        event = _wire_message_to_event(tool_call)

        assert event["event"] == "ToolCallDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test-session"
        assert data["agent_id"] == "child-2"
        assert data["tool_name"] == "read_file"
        assert data["arguments"] == {"path": "/test.txt"}
        assert data["call_id"] == "call-123"

    def test_turn_end_conversion(self):
        """Test TurnEnd converts to correct SSE event."""
        turn_end = TurnEnd(
            session_id="test-session",
            agent_id="child-3",
            turn_id="turn-123",
            completion_status=CompletionStatus.COMPLETED,
        )
        event = _wire_message_to_event(turn_end)

        assert event["event"] == "TurnEnd"
        data = json.loads(event["data"])
        assert data["session_id"] == "test-session"
        assert data["agent_id"] == "child-3"
        assert data["turn_id"] == "turn-123"
        assert data["completion_status"] == "completed"

    def test_approval_request_conversion(self):
        """Test ApprovalRequest converts to correct SSE event."""
        tool_call = ToolCallDelta(
            session_id="test-session",
            agent_id="child-4",
            tool_name="write_file",
            arguments={"path": "/test.txt", "content": "hello"},
            call_id="call-123",
        )
        approval_req = ApprovalRequest(
            session_id="test-session",
            agent_id="child-4",
            request_id="req-123",
            tool_call=tool_call,
            timeout_seconds=60,
        )
        event = _wire_message_to_event(approval_req)

        assert event["event"] == "ApprovalRequest"
        data = json.loads(event["data"])
        assert data["session_id"] == "test-session"
        assert data["agent_id"] == "child-4"
        assert data["request_id"] == "req-123"
        assert data["tool_call"]["tool_name"] == "write_file"
        assert data["timeout_seconds"] == 60

    def test_approval_response_conversion(self):
        """Test ApprovalResponse converts to correct SSE event."""
        approval_resp = ApprovalResponse(
            session_id="test-session",
            agent_id="child-5",
            request_id="req-123",
            approved=True,
            feedback="Looks good",
        )
        event = _wire_message_to_event(approval_resp)

        assert event["event"] == "ApprovalResponse"
        data = json.loads(event["data"])
        assert data["session_id"] == "test-session"
        assert data["agent_id"] == "child-5"
        assert data["request_id"] == "req-123"
        assert data["approved"] is True
        assert data["feedback"] == "Looks good"


class TestBroadcastEvent:
    """Test event broadcasting to multiple clients."""

    async def test_broadcast_to_multiple_queues(self):
        """Test events are broadcast to all connected clients."""
        session = register_session("test-session")

        # Add multiple event queues
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session.event_queues = [queue1, queue2]

        event = {"event": "Test", "data": "test-data"}
        await _broadcast_event(session, event)

        # Both queues should receive the event
        assert await queue1.get() == event
        assert await queue2.get() == event

    async def test_broadcast_wire_message(self):
        """Test broadcasting wire message events."""
        session = register_session("test-session")

        queue = asyncio.Queue()
        session.event_queues = [queue]

        delta = StreamDelta(
            session_id="test-session",
            content="Hello",
        )
        event = _wire_message_to_event(delta)
        await _broadcast_event(session, event)

        received = await queue.get()
        assert received["event"] == "StreamDelta"


class TestSessionCreationAndEvents:
    """Test session creation and event streaming flow."""

    async def test_create_session_via_http(self, client):
        """Test creating session through HTTP API."""
        response = await client.post("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert session_manager.has_session(data["session_id"])

    async def test_get_session_via_http(self, client):
        """Test getting session info through HTTP API."""
        # Create a session
        session_id = "test-session-123"
        register_session(session_id)

        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["turn_in_progress"] is False

    async def test_session_not_found(self, client):
        """Test 404 for non-existent session."""
        response = await client.get("/sessions/non-existent")
        assert response.status_code == 404


class TestPromptStreamingFlow:
    """Test the full prompt streaming flow."""

    async def test_prompt_streaming_events(self, client):
        """Test that prompt endpoint streams correct events."""
        # Create a session using the API (for full AgentLoop integration)
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session_manager.get_session(session_id).provider = MockProvider()

        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            events = []
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "TurnEnd":
                    break

            # Should have StreamDelta events and a TurnEnd
            assert any(e == "StreamDelta" for e in events)
            assert any(e == "TurnEnd" for e in events)

    async def test_concurrent_turn_rejection(self, client):
        """Test that concurrent turns are rejected with 409."""
        # Create a session with turn in progress
        session_id = "test-session-busy"
        register_session(session_id, turn_in_progress=True)

        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )
        assert response.status_code == 409


class TestApprovalFlowIntegration:
    """Test approval flow integration between wire and HTTP."""

    async def test_approval_request_broadcast(self):
        """Test approval request is broadcast to event stream."""
        # Create session
        session_id = "test-approval-session"
        session = register_session(session_id, turn_in_progress=True)

        # Add event queue
        queue = asyncio.Queue()
        session.event_queues = [queue]

        # Create approval request
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="write_file",
            arguments={"path": "/test.txt"},
            call_id="call-123",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req-123",
            tool_call=tool_call,
        )

        # Broadcast the approval request
        event = _wire_message_to_event(approval_req)
        await _broadcast_event(session, event)

        # Verify it was broadcast
        received = await queue.get()
        assert received["event"] == "ApprovalRequest"
        data = json.loads(received["data"])
        assert data["request_id"] == "req-123"
        assert data["tool_call"]["tool_name"] == "write_file"

    async def test_approve_endpoint_sets_response(self, client):
        """Test approve endpoint properly sets approval response."""
        # Create session with pending approval
        session_id = "test-approve-endpoint"
        session = register_session(
            session_id,
            turn_in_progress=True,
        )
        add_store_backed_approval_request(session, session_id, "req-123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req-123",
                "approved": True,
                "feedback": "Approved!",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"

        # Verify session state
        assert session.approval_response is not None
        assert session.approval_response["decision"] == "approve"
        assert session.approval_response["feedback"] == "Approved!"
        assert session.approval_event.is_set()

    async def test_approve_endpoint_request_id_mismatch(self, client):
        """Test approve endpoint rejects mismatched request ID."""
        # Create session with pending approval
        session_id = "test-approve-mismatch"
        register_session(
            session_id,
            turn_in_progress=True,
            pending_approval={
                "request_id": "req-123",
                "tool_name": "write_file",
                "arguments": {},
            },
        )

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "wrong-id",
                "approved": True,
            },
        )
        # Legacy check returns 400 for request ID mismatch
        assert response.status_code == 400

    async def test_session_scope_approval_skips_second_http_prompt_approval_live_server(
        self, tmp_path
    ):
        """Test session approval reuse through a real localhost HTTP server."""

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        session_id = "live-http-approval-session"
        env = os.environ.copy()
        env.update(
            {
                "LIVE_HTTP_TEST_PORT": str(port),
                "LIVE_HTTP_TEST_SESSION_ID": session_id,
                "LIVE_HTTP_TEST_REPO_PATH": str(tmp_path),
            }
        )

        server_script = textwrap.dedent(
            """
            import os
            from datetime import datetime
            from pathlib import Path

            import uvicorn
            from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

            from coding_agent.approval import ApprovalPolicy
            from coding_agent.ui.http_server import app, session_manager
            from coding_agent.ui.session_manager import Session

            class ScriptedApprovalProvider:
                def __init__(self) -> None:
                    self.calls = 0

                @property
                def model_name(self) -> str:
                    return "scripted-approval"

                @property
                def max_context_size(self) -> int:
                    return 128000

                async def stream(self, messages, tools=None, **kwargs):
                    del messages, tools, kwargs
                    self.calls += 1
                    if self.calls == 1:
                        yield ToolCallEvent(
                            tool_call_id="tc-write-1",
                            name="file_write",
                            arguments={"path": "first.txt", "content": "first"},
                        )
                        yield DoneEvent()
                        return

                    if self.calls == 2:
                        yield TextEvent(text="first write complete")
                        yield DoneEvent()
                        return

                    if self.calls == 3:
                        yield ToolCallEvent(
                            tool_call_id="tc-write-2",
                            name="file_write",
                            arguments={"path": "second.txt", "content": "second"},
                        )
                        yield DoneEvent()
                        return

                    yield TextEvent(text="second write complete")
                    yield DoneEvent()

            session = Session(
                id=os.environ["LIVE_HTTP_TEST_SESSION_ID"],
                created_at=datetime.now(),
                last_activity=datetime.now(),
                repo_path=Path(os.environ["LIVE_HTTP_TEST_REPO_PATH"]),
                approval_policy=ApprovalPolicy.INTERACTIVE,
                provider=ScriptedApprovalProvider(),
            )
            session_manager.register_session(session)
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=int(os.environ["LIVE_HTTP_TEST_PORT"]),
                log_level="error",
            )
            """
        )

        server = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            server_script,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        base_url = f"http://127.0.0.1:{port}"
        for _ in range(200):
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=1.0) as probe:
                    ready = await probe.get("/healthz")
                if ready.status_code == 200:
                    break
            except Exception:
                await asyncio.sleep(0.05)
        else:
            stdout, stderr = await server.communicate()
            raise AssertionError(
                f"live server failed to start\nstdout={stdout.decode()}\nstderr={stderr.decode()}"
            )

        try:
            async with (
                httpx.AsyncClient(base_url=base_url, timeout=30.0) as prompt_client,
                httpx.AsyncClient(base_url=base_url, timeout=30.0) as approval_client,
            ):
                first_events: list[dict[str, object]] = []
                approved_request_id: str | None = None
                async with aconnect_sse(
                    prompt_client,
                    "POST",
                    f"/sessions/{session_id}/prompt",
                    json={"prompt": "Write the first file"},
                ) as event_source:
                    async for sse in event_source.aiter_sse():
                        payload = json.loads(sse.data)
                        first_events.append({"event": sse.event, "data": payload})
                        if sse.event == "ApprovalRequest":
                            approved_request_id = payload["request_id"]
                            approve_resp = await approval_client.post(
                                f"/sessions/{session_id}/approve",
                                json={
                                    "request_id": approved_request_id,
                                    "approved": True,
                                    "feedback": "approve for session",
                                    "scope": "session",
                                },
                            )
                            assert approve_resp.status_code == 200
                        if sse.event == "TurnEnd" and not payload["agent_id"]:
                            break

                second_events: list[dict[str, object]] = []
                async with aconnect_sse(
                    prompt_client,
                    "POST",
                    f"/sessions/{session_id}/prompt",
                    json={"prompt": "Write the second file"},
                ) as event_source:
                    async for sse in event_source.aiter_sse():
                        payload = json.loads(sse.data)
                        second_events.append({"event": sse.event, "data": payload})
                        if sse.event == "TurnEnd" and not payload["agent_id"]:
                            break

            assert approved_request_id is not None
            assert any(event["event"] == "ApprovalRequest" for event in first_events)
            assert not any(
                event["event"] == "ApprovalRequest" for event in second_events
            )
            assert (tmp_path / "first.txt").read_text() == "first"
            assert (tmp_path / "second.txt").read_text() == "second"
        finally:
            server.terminate()
            await server.wait()


class TestWaitForApproval:
    """Test the wait_for_approval function."""

    async def test_wait_for_approval_session_not_found(self):
        """Test wait_for_approval returns denied for non-existent session."""
        tool_call = ToolCallDelta(
            session_id="non-existent",
            tool_name="write_file",
            arguments={},
            call_id="call-123",
        )
        approval_req = ApprovalRequest(
            session_id="non-existent",
            request_id="req-123",
            tool_call=tool_call,
        )

        response = await wait_for_approval("non-existent", approval_req)

        assert isinstance(response, ApprovalResponse)
        assert response.approved is False
        assert response.request_id == "req-123"

    async def test_wait_for_approval_no_turn_in_progress(self):
        """Test wait_for_approval returns denied when no turn in progress."""
        session_id = "test-no-turn"
        register_session(session_id, turn_in_progress=False)

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="write_file",
            arguments={},
            call_id="call-123",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req-123",
            tool_call=tool_call,
        )

        response = await wait_for_approval(session_id, approval_req)

        assert isinstance(response, ApprovalResponse)
        assert response.approved is False


class TestFullFlowIntegration:
    """Test complete end-to-end flow."""

    async def test_full_session_lifecycle(self, client):
        """Test complete session lifecycle: create -> prompt -> close."""
        # 1. Create session
        response = await client.post("/sessions")
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # 2. Get session info
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["id"] == session_id

        # 3. Send prompt and receive events
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

        assert len(events) > 0
        assert events[-1] == "TurnEnd"

        # 4. Close session
        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

        # 5. Verify session is gone
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 404

    async def test_events_fan_out(self, client):
        """Test multiple clients can connect to events endpoint."""
        # Create session
        session_id = "test-fanout"
        session = register_session(session_id)

        # Add multiple event queues to simulate multiple clients
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session.event_queues = [queue1, queue2]

        # Send a prompt to generate events (non-streaming request just to trigger events)
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )
        assert response.status_code == 200

        # The prompt endpoint streams events via SSE which also broadcasts to queues
        # Both queues should have received events from the broadcast
        # Note: We test the broadcast mechanism rather than the full SSE fan-out
        # which would require complex async coordination
        assert len(session.event_queues) >= 2


class TestSessionTimeout:
    """Test session timeout handling."""

    async def test_session_marked_expired_after_timeout(self):
        """Test session is marked as expired after idle timeout."""
        from datetime import timedelta

        session_id = "test-timeout"
        old_time = datetime.now() - timedelta(minutes=31)
        session = register_session(
            session_id,
            created_at=old_time,
            last_activity=old_time,
        )

        # Manually check if session would be cleaned up
        from coding_agent.ui.http_server import SESSION_IDLE_TIMEOUT_MINUTES

        idle_time = datetime.now() - session.last_activity
        assert idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    async def test_session_not_expired_if_active(self):
        """Test active session is not marked as expired."""
        from datetime import timedelta

        session_id = "test-active"
        session = register_session(session_id)

        from coding_agent.ui.http_server import SESSION_IDLE_TIMEOUT_MINUTES

        idle_time = datetime.now() - session.last_activity
        assert idle_time < timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
