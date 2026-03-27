"""Tests for LocalWire implementation."""

from __future__ import annotations

import asyncio

import pytest

from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    LocalWire,
    StreamDelta,
    ToolCallDelta,
)


class TestLocalWireCreation:
    """Tests for LocalWire initialization."""

    def test_local_wire_creation(self):
        """Test LocalWire can be created with session_id."""
        wire = LocalWire("test-session-123")
        
        assert wire.session_id == "test-session-123"

    def test_local_wire_queues_initialized(self):
        """Test LocalWire queues are properly initialized."""
        wire = LocalWire("test-session")
        
        # Queues should exist and be empty
        assert wire._outgoing is not None
        assert wire._incoming is not None


class TestLocalWireSendReceive:
    """Tests for LocalWire send and receive operations."""

    @pytest.mark.asyncio
    async def test_send_message(self):
        """Test sending a message adds it to outgoing queue."""
        wire = LocalWire("test-session")
        msg = StreamDelta(
            session_id="test-session",
            content="Hello, world!",
        )
        
        await wire.send(msg)
        
        # Message should be in outgoing queue
        result = wire.consume_outgoing()
        assert result == msg
        assert result.content == "Hello, world!"

    @pytest.mark.asyncio
    async def test_send_multiple_messages(self):
        """Test sending multiple messages preserves order."""
        wire = LocalWire("test-session")
        
        msg1 = StreamDelta(session_id="test-session", content="First")
        msg2 = StreamDelta(session_id="test-session", content="Second")
        msg3 = StreamDelta(session_id="test-session", content="Third")
        
        await wire.send(msg1)
        await wire.send(msg2)
        await wire.send(msg3)
        
        assert wire.consume_outgoing().content == "First"
        assert wire.consume_outgoing().content == "Second"
        assert wire.consume_outgoing().content == "Third"

    @pytest.mark.asyncio
    async def test_receive_message(self):
        """Test receiving a message from incoming queue."""
        wire = LocalWire("test-session")
        msg = StreamDelta(
            session_id="test-session",
            content="Test message",
        )
        
        # Inject message into incoming queue
        wire.inject_incoming(msg)
        
        # Receive should return the message
        result = await wire.receive()
        assert result == msg

    @pytest.mark.asyncio
    async def test_receive_order(self):
        """Test receive preserves FIFO order."""
        wire = LocalWire("test-session")
        
        wire.inject_incoming(StreamDelta(session_id="test-session", content="1"))
        wire.inject_incoming(StreamDelta(session_id="test-session", content="2"))
        wire.inject_incoming(StreamDelta(session_id="test-session", content="3"))
        
        assert (await wire.receive()).content == "1"
        assert (await wire.receive()).content == "2"
        assert (await wire.receive()).content == "3"

    @pytest.mark.asyncio
    async def test_consume_outgoing_empty(self):
        """Test consuming from empty queue returns None."""
        wire = LocalWire("test-session")
        
        result = wire.consume_outgoing()
        
        assert result is None


class TestLocalWireApprovalFlow:
    """Tests for approval request/response flow."""

    @pytest.mark.asyncio
    async def test_request_approval_success(self):
        """Test successful approval request and response."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="write_file",
            arguments={"path": "/tmp/test.txt"},
            call_id="call-123",
        )
        
        # Schedule response in background (simulating UI)
        async def send_response():
            # Wait a bit then inject response to incoming queue
            await asyncio.sleep(0.01)
            response = ApprovalResponse(
                session_id="test-session",
                request_id="call-123",  # Matches call_id
                approved=True,
                feedback="Looks good",
            )
            # UI injects response to incoming queue
            wire.inject_incoming(response)
        
        # Start response sender
        asyncio.create_task(send_response())
        
        # Request approval
        result = await wire.request_approval(tool_call, timeout=1)
        
        assert isinstance(result, ApprovalResponse)
        assert result.approved is True
        assert result.feedback == "Looks good"
        assert result.request_id == "call-123"

    @pytest.mark.asyncio
    async def test_request_approval_denied(self):
        """Test approval request that is denied."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="delete_file",
            arguments={"path": "/important/file"},
            call_id="call-456",
        )
        
        async def send_response():
            await asyncio.sleep(0.01)
            response = ApprovalResponse(
                session_id="test-session",
                request_id="call-456",
                approved=False,
                feedback="Too dangerous",
            )
            # UI injects response to incoming queue
            wire.inject_incoming(response)
        
        asyncio.create_task(send_response())
        
        result = await wire.request_approval(tool_call, timeout=1)
        
        assert result.approved is False
        assert result.feedback == "Too dangerous"

    @pytest.mark.asyncio
    async def test_request_approval_timeout(self):
        """Test approval request times out when no response."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="write_file",
            arguments={},
            call_id="call-timeout",
        )
        
        # Don't send any response - should return denial response on timeout
        result = await wire.request_approval(tool_call, timeout=0.1)
        
        assert isinstance(result, ApprovalResponse)
        assert result.approved is False
        assert "timeout" in result.feedback.lower()
        assert result.request_id == "call-timeout"

    @pytest.mark.asyncio
    async def test_request_approval_default_timeout(self):
        """Test approval request with custom short timeout."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="write_file",
            arguments={},
            call_id="call-default",
        )
        
        # Just verify no exception with quick response
        async def send_response():
            await asyncio.sleep(0.01)
            response = ApprovalResponse(
                session_id="test-session",
                request_id="call-default",
                approved=True,
            )
            # UI injects response to incoming queue
            wire.inject_incoming(response)
        
        asyncio.create_task(send_response())
        result = await wire.request_approval(tool_call, timeout=0.5)
        
        assert result.approved is True


class TestLocalWireErrorHandling:
    """Tests for error handling in LocalWire."""

    @pytest.mark.asyncio
    async def test_request_approval_wrong_response_type(self):
        """Test error when response is wrong type."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="write_file",
            arguments={},
            call_id="call-wrong",
        )
        
        async def send_wrong_response():
            await asyncio.sleep(0.01)
            # Send wrong message type (UI injects wrong type)
            wrong_msg = StreamDelta(
                session_id="test-session",
                content="wrong",
            )
            wire.inject_incoming(wrong_msg)
        
        asyncio.create_task(send_wrong_response())
        
        with pytest.raises(ValueError) as exc_info:
            await wire.request_approval(tool_call, timeout=1)
        
        assert "Expected ApprovalResponse" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_approval_mismatched_request_id(self):
        """Test error when response has wrong request_id."""
        wire = LocalWire("test-session")
        
        tool_call = ToolCallDelta(
            session_id="test-session",
            tool_name="write_file",
            arguments={},
            call_id="call-correct",
        )
        
        async def send_mismatched_response():
            await asyncio.sleep(0.01)
            response = ApprovalResponse(
                session_id="test-session",
                request_id="wrong-request-id",  # Doesn't match
                approved=True,
            )
            # UI injects response with wrong ID
            wire.inject_incoming(response)
        
        asyncio.create_task(send_mismatched_response())
        
        with pytest.raises(ValueError) as exc_info:
            await wire.request_approval(tool_call, timeout=1)
        
        assert "request_id mismatch" in str(exc_info.value)


class TestLocalWireQueueMethods:
    """Tests for queue helper methods."""

    @pytest.mark.asyncio
    async def test_inject_incoming(self):
        """Test injecting messages into incoming queue."""
        wire = LocalWire("test-session")
        msg = StreamDelta(session_id="test-session", content="injected")
        
        wire.inject_incoming(msg)
        
        result = await wire.receive()
        assert result.content == "injected"

    def test_consume_outgoing_with_message(self):
        """Test consuming outgoing message."""
        wire = LocalWire("test-session")
        msg = StreamDelta(session_id="test-session", content="outgoing")
        
        # Put message in queue using put_nowait
        wire._outgoing.put_nowait(msg)
        
        result = wire.consume_outgoing()
        assert result.content == "outgoing"

    @pytest.mark.asyncio
    async def test_get_next_outgoing(self):
        """Test async get next outgoing message."""
        wire = LocalWire("test-session")
        msg = StreamDelta(session_id="test-session", content="async-outgoing")
        
        # Schedule message
        async def send_message():
            await asyncio.sleep(0.01)
            await wire.send(msg)
        
        asyncio.create_task(send_message())
        
        result = await wire.get_next_outgoing()
        assert result.content == "async-outgoing"


class TestLocalWireIntegration:
    """Integration tests for LocalWire."""

    @pytest.mark.asyncio
    async def test_full_message_flow(self):
        """Test full message flow between agent and UI."""
        wire = LocalWire("test-session")
        
        # Simulate agent sending messages
        await wire.send(StreamDelta(session_id="test-session", content="Hello"))
        await wire.send(StreamDelta(session_id="test-session", content="Processing..."))
        
        # UI consumes messages from outgoing queue
        assert wire.consume_outgoing().content == "Hello"
        assert wire.consume_outgoing().content == "Processing..."
        
        # UI sends user input via incoming queue
        wire.inject_incoming(StreamDelta(session_id="test-session", content="User response"))
        
        # Agent receives it from incoming queue
        result = await wire.receive()
        assert result.content == "User response"

    @pytest.mark.asyncio
    async def test_approval_flow_simulation(self):
        """Simulate full approval flow between agent and UI."""
        wire = LocalWire("test-session")
        
        async def agent_side():
            """Simulate agent requesting approval."""
            tool_call = ToolCallDelta(
                session_id="test-session",
                tool_name="write_file",
                arguments={"path": "/tmp/test.txt", "content": "hello"},
                call_id="call-001",
            )
            
            # Send approval request and wait for response
            response = await wire.request_approval(tool_call, timeout=1)
            return response.approved
        
        async def ui_side():
            """Simulate UI handling approval."""
            # Get the approval request from outgoing queue (agent -> UI)
            msg = await wire.get_next_outgoing()
            assert isinstance(msg, ApprovalRequest)
            assert msg.tool_call.tool_name == "write_file"
            
            # Simulate user approval - send response to incoming queue (UI -> agent)
            response = ApprovalResponse(
                session_id="test-session",
                request_id=msg.request_id,
                approved=True,
                feedback="Approved",
            )
            wire.inject_incoming(response)
        
        # Run both sides concurrently
        agent_task = asyncio.create_task(agent_side())
        ui_task = asyncio.create_task(ui_side())
        
        # Wait for both to complete
        approved = await agent_task
        await ui_task
        
        assert approved is True
