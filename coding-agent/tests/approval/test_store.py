"""Tests for approval store module."""

import asyncio
from datetime import datetime

import pytest

from coding_agent.approval.store import ApprovalStore, PendingRequest
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse, ToolCallDelta


@pytest.fixture
def sample_tool_call():
    """Create a sample tool call for testing."""
    return ToolCallDelta(
        session_id="test-session",
        tool_name="test_tool",
        arguments={"arg": "value"},
        call_id="call-123",
    )


@pytest.fixture
def sample_approval_request(sample_tool_call):
    """Create a sample approval request for testing."""
    return ApprovalRequest(
        session_id="test-session",
        request_id="req-123",
        tool_call=sample_tool_call,
        timeout_seconds=120,
    )


@pytest.fixture
def sample_approval_response():
    """Create a sample approval response for testing."""
    return ApprovalResponse(
        session_id="test-session",
        request_id="req-123",
        approved=True,
        feedback="Looks good",
    )


@pytest.fixture
def store():
    """Create a fresh ApprovalStore for testing."""
    return ApprovalStore()


class TestPendingRequest:
    """Tests for PendingRequest dataclass."""

    def test_pending_request_creation(self, sample_approval_request):
        """PendingRequest can be created with defaults."""
        pending = PendingRequest(request=sample_approval_request)

        assert pending.request == sample_approval_request
        assert isinstance(pending.created_at, datetime)
        assert pending.response is None
        assert isinstance(pending.response_event, asyncio.Event)
        assert not pending.response_event.is_set()


class TestApprovalStoreAddRequest:
    """Tests for ApprovalStore.add_request method."""

    def test_add_request_stores_pending(self, store, sample_approval_request):
        """Adding a request stores it in pending."""
        store.add_request(sample_approval_request)

        assert "req-123" in store._pending
        pending = store._pending["req-123"]
        assert pending.request == sample_approval_request

    def test_add_request_overwrites_existing(self, store, sample_approval_request):
        """Adding a request with same ID overwrites existing."""
        store.add_request(sample_approval_request)

        # Modify and re-add
        new_request = sample_approval_request
        store.add_request(new_request)

        assert len(store._pending) == 1
        assert store._pending["req-123"].request == new_request


class TestApprovalStoreGetRequest:
    """Tests for ApprovalStore.get_request method."""

    def test_get_request_returns_request(self, store, sample_approval_request):
        """Getting a request returns the ApprovalRequest."""
        store.add_request(sample_approval_request)

        result = store.get_request("req-123")

        assert result == sample_approval_request

    def test_get_request_not_found_returns_none(self, store):
        """Getting a non-existent request returns None."""
        result = store.get_request("non-existent")

        assert result is None

    def test_remove_request_deletes_pending_entry(
        self, store, sample_approval_request
    ) -> None:
        store.add_request(sample_approval_request)

        store.remove_request(sample_approval_request.request_id)

        assert store.get_request(sample_approval_request.request_id) is None


class TestApprovalStoreRespond:
    """Tests for ApprovalStore.respond method."""

    def test_respond_records_response(
        self, store, sample_approval_request, sample_approval_response
    ):
        """Responding records the response and sets the event."""
        store.add_request(sample_approval_request)

        success = store.respond(sample_approval_response)

        assert success is True
        pending = store._pending["req-123"]
        assert pending.response == sample_approval_response
        assert pending.response_event.is_set()

    def test_respond_not_found_returns_false(self, store, sample_approval_response):
        """Responding to non-existent request returns False."""
        success = store.respond(sample_approval_response)

        assert success is False

    def test_respond_wrong_request_id_returns_false(
        self, store, sample_approval_request
    ):
        """Responding with wrong request_id returns False."""
        store.add_request(sample_approval_request)

        wrong_response = ApprovalResponse(
            session_id="test-session", request_id="wrong-id", approved=True
        )
        success = store.respond(wrong_response)

        assert success is False


class TestApprovalStoreWaitForResponse:
    """Tests for ApprovalStore.wait_for_response method."""

    @pytest.mark.asyncio
    async def test_wait_returns_response_when_ready(
        self, store, sample_approval_request, sample_approval_response
    ):
        """Waiting returns response when it's ready."""
        store.add_request(sample_approval_request)

        # Schedule response after short delay
        async def respond_later():
            await asyncio.sleep(0.05)
            store.respond(sample_approval_response)

        asyncio.create_task(respond_later())
        result = await store.wait_for_response("req-123", timeout=1)

        assert result == sample_approval_response
        assert store.get_request("req-123") is None

    @pytest.mark.asyncio
    async def test_wait_returns_none_on_timeout(self, store, sample_approval_request):
        """Waiting returns None when timeout occurs (not raises)."""
        store.add_request(sample_approval_request)

        result = await store.wait_for_response("req-123", timeout=0.05)

        assert result is None
        assert store.get_request("req-123") is None

    @pytest.mark.asyncio
    async def test_wait_not_found_returns_none(self, store):
        """Waiting for non-existent request returns None."""
        result = await store.wait_for_response("non-existent", timeout=0.1)

        assert result is None

    @pytest.mark.asyncio
    async def test_wait_already_responded(
        self, store, sample_approval_request, sample_approval_response
    ):
        """Waiting on already-responded request returns response immediately."""
        store.add_request(sample_approval_request)
        store.respond(sample_approval_response)

        result = await store.wait_for_response("req-123", timeout=0.1)

        assert result == sample_approval_response
        assert store.get_request("req-123") is None

    @pytest.mark.asyncio
    async def test_wait_propagates_cancellation(self, store, sample_approval_request):
        store.add_request(sample_approval_request)

        task = asyncio.create_task(store.wait_for_response("req-123", timeout=1))
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert store.get_request("req-123") is None


class TestApprovalStoreConcurrent:
    """Tests for concurrent request handling."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_handled_independently(self, store):
        """Multiple concurrent requests are handled independently."""
        # Create multiple requests
        requests = []
        for i in range(3):
            tool_call = ToolCallDelta(
                session_id="test-session",
                tool_name=f"tool_{i}",
                arguments={},
                call_id=f"call-{i}",
            )
            req = ApprovalRequest(
                session_id="test-session",
                request_id=f"req-{i}",
                tool_call=tool_call,
                timeout_seconds=120,
            )
            requests.append(req)
            store.add_request(req)

        # Respond to each independently
        responses = []
        for i, req in enumerate(requests):
            resp = ApprovalResponse(
                session_id="test-session",
                request_id=req.request_id,
                approved=(i % 2 == 0),  # Alternate approve/reject
            )
            responses.append(resp)
            store.respond(resp)

        # Verify all responses are correct
        for i, resp in enumerate(responses):
            result = store._pending[f"req-{i}"].response
            assert result == resp
            assert result.approved == (i % 2 == 0)

    @pytest.mark.asyncio
    async def test_multiple_waiters_same_request(
        self, store, sample_approval_request, sample_approval_response
    ):
        """Multiple waiters on same request all get the response."""
        store.add_request(sample_approval_request)

        async def waiter():
            return await store.wait_for_response("req-123", timeout=1)

        # Start multiple waiters
        tasks = [asyncio.create_task(waiter()) for _ in range(3)]

        # Respond
        await asyncio.sleep(0.05)
        store.respond(sample_approval_response)

        # All should get the response
        results = await asyncio.gather(*tasks)
        for result in results:
            assert result == sample_approval_response
