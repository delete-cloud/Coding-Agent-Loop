"""Tests for backend approval coordination."""

from __future__ import annotations

import asyncio

import pytest

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse, ToolCallDelta


def _request(request_id: str, tool_name: str = "bash") -> ApprovalRequest:
    return ApprovalRequest(
        session_id="session-1",
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id="session-1",
            tool_name=tool_name,
            arguments={"command": tool_name},
            call_id=f"call-{request_id}",
        ),
        timeout_seconds=1,
    )


class TestApprovalCoordinator:
    @pytest.mark.asyncio
    async def test_pending_projection_advances_to_next_request_after_first_response(
        self,
    ) -> None:
        coordinator = ApprovalCoordinator()
        first = _request("req-1", tool_name="bash")
        second = _request("req-2", tool_name="write_file")

        coordinator.add_request(first)
        coordinator.add_request(second)

        assert coordinator.pending_request_id == "req-1"

        responded = coordinator.respond(
            ApprovalResponse(
                session_id="session-1",
                request_id="req-1",
                approved=True,
                feedback="ok",
            )
        )

        assert responded is True

        response = await coordinator.wait_for_response("req-1", timeout=1)

        assert response is not None
        assert response.request_id == "req-1"
        assert coordinator.pending_request_id == "req-2"

    @pytest.mark.asyncio
    async def test_waits_are_isolated_per_request(self) -> None:
        coordinator = ApprovalCoordinator()
        first = _request("req-1")
        second = _request("req-2", tool_name="write_file")

        coordinator.add_request(first)
        coordinator.add_request(second)

        wait_first = asyncio.create_task(
            coordinator.wait_for_response("req-1", timeout=1)
        )
        wait_second = asyncio.create_task(
            coordinator.wait_for_response("req-2", timeout=1)
        )
        await asyncio.sleep(0)

        coordinator.respond(
            ApprovalResponse(
                session_id="session-1",
                request_id="req-2",
                approved=False,
                feedback="deny second",
            )
        )
        coordinator.respond(
            ApprovalResponse(
                session_id="session-1",
                request_id="req-1",
                approved=True,
                feedback="approve first",
            )
        )

        first_response = await wait_first
        second_response = await wait_second

        assert first_response is not None
        assert second_response is not None
        assert first_response.request_id == "req-1"
        assert first_response.approved is True
        assert second_response.request_id == "req-2"
        assert second_response.approved is False

    def test_legacy_always_scope_is_treated_as_session_approval(self) -> None:
        coordinator = ApprovalCoordinator()
        request = _request("req-1", tool_name="bash")
        repeated = _request("req-2", tool_name="bash")

        coordinator.add_request(request)

        responded = coordinator.respond(
            ApprovalResponse(
                session_id="session-1",
                request_id="req-1",
                approved=True,
                scope="always",
            )
        )

        assert responded is True
        assert coordinator.is_session_approved(repeated) is True
