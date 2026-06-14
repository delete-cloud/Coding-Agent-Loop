from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from coding_agent.approval import ApprovalInteractionService
from coding_agent.stores.runtime_store import AgentInteractionRecord, JSONObject
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse, ToolCallDelta


@dataclass
class FakeSession:
    id: str
    current_turn_id: str | None


class RecordingInteractionStore:
    def __init__(self) -> None:
        self.interactions: list[AgentInteractionRecord] = []

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        for interaction in self.interactions:
            if interaction.interaction_id == record.interaction_id:
                return interaction
        self.interactions.append(record)
        return record

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        for interaction in self.interactions:
            if interaction.interaction_id == interaction_id:
                return interaction
        return None

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions
            if interaction.run_id == run_id
        ]

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        for index, interaction in enumerate(self.interactions):
            if interaction.interaction_id != interaction_id:
                continue
            if interaction.resolved_at is not None:
                return interaction
            resolved = AgentInteractionRecord(
                interaction_id=interaction.interaction_id,
                run_id=interaction.run_id,
                interaction_kind=interaction.interaction_kind,
                status=status,
                request_payload=interaction.request_payload,
                response_payload=response_payload,
                metadata=interaction.metadata,
                created_at=interaction.created_at,
                resolved_at=resolved_at,
            )
            self.interactions[index] = resolved
            return resolved
        raise AssertionError(f"missing interaction {interaction_id}")


@pytest.mark.asyncio
async def test_approval_interaction_service_creates_pending_interaction() -> None:
    store = RecordingInteractionStore()
    service = ApprovalInteractionService(
        store=store,
        owner_id="owner-a",
        fencing_token=7,
    )
    requested_at = datetime(2026, 1, 2, 3, 4, 5)
    tool_called_at = datetime(2026, 1, 2, 3, 4, 4)
    request = ApprovalRequest(
        session_id="session-1",
        request_id="request-1",
        timestamp=requested_at,
        tool_call=ToolCallDelta(
            session_id="session-1",
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-1",
            timestamp=tool_called_at,
        ),
        timeout_seconds=5,
    )

    interaction_id = await service.create(
        FakeSession(id="session-1", current_turn_id="run-1"),
        request,
    )

    assert interaction_id == "run-1:approval:request-1"
    assert store.interactions == [
        AgentInteractionRecord(
            interaction_id="run-1:approval:request-1",
            run_id="run-1",
            interaction_kind="approval",
            status="pending",
            request_payload={
                "session_id": "session-1",
                "request_id": "request-1",
                "timestamp": requested_at.isoformat(),
                "timeout_seconds": 5,
                "tool_call": {
                    "session_id": "session-1",
                    "agent_id": "",
                    "timestamp": tool_called_at.isoformat(),
                    "tool_name": "bash",
                    "arguments": {"command": "pwd"},
                    "call_id": "call-1",
                },
            },
            response_payload={},
            metadata={
                "session_id": "session-1",
                "request_id": "request-1",
                "tool_call_id": "call-1",
                "tool_name": "bash",
                "owner_id": "owner-a",
                "fencing_token": 7,
            },
            created_at=requested_at,
        )
    ]


@pytest.mark.asyncio
async def test_approval_interaction_service_resolves_interaction() -> None:
    store = RecordingInteractionStore()
    service = ApprovalInteractionService(store=store)
    request = ApprovalRequest(
        session_id="session-1",
        request_id="request-1",
        timestamp=datetime(2026, 1, 2, 3, 4, 5),
    )
    session = FakeSession(id="session-1", current_turn_id="run-1")
    await service.create(session, request)
    responded_at = datetime(2026, 1, 2, 3, 4, 9)
    response = ApprovalResponse(
        session_id="session-1",
        request_id="request-1",
        approved=False,
        feedback="no",
        timestamp=responded_at,
    )

    await service.resolve(session, "request-1", response)

    assert store.interactions[0].status == "rejected"
    assert store.interactions[0].response_payload == {
        "session_id": "session-1",
        "request_id": "request-1",
        "approved": False,
        "feedback": "no",
        "scope": "once",
    }
    assert store.interactions[0].resolved_at == responded_at


@pytest.mark.asyncio
async def test_approval_interaction_service_rejects_mismatched_resolution_request_id() -> (
    None
):
    store = RecordingInteractionStore()
    service = ApprovalInteractionService(store=store)
    session = FakeSession(id="session-1", current_turn_id="run-1")
    request = ApprovalRequest(session_id="session-1", request_id="request-1")
    await service.create(session, request)
    response = ApprovalResponse(
        session_id="session-1",
        request_id="request-2",
        approved=True,
    )

    with pytest.raises(ValueError, match="approval response request_id"):
        await service.resolve(session, "request-1", response)

    assert store.interactions[0].status == "pending"
    assert store.interactions[0].response_payload == {}
    assert store.interactions[0].resolved_at is None


@pytest.mark.asyncio
async def test_approval_interaction_service_respects_explicit_resolution_status() -> (
    None
):
    store = RecordingInteractionStore()
    service = ApprovalInteractionService(store=store)
    session = FakeSession(id="session-1", current_turn_id="run-1")
    request = ApprovalRequest(session_id="session-1", request_id="request-timeout")
    await service.create(session, request)
    response = ApprovalResponse(
        session_id="session-1",
        request_id="request-timeout",
        approved=False,
        feedback="Approval timeout or error",
    )

    await service.resolve(session, "request-timeout", response, status="timed_out")

    assert store.interactions[0].status == "timed_out"


@pytest.mark.asyncio
async def test_approval_interaction_service_skips_without_store_or_run() -> None:
    store = RecordingInteractionStore()
    request = ApprovalRequest(session_id="session-1", request_id="request-1")

    missing_store = ApprovalInteractionService(store=None)
    assert (
        await missing_store.create(
            FakeSession(id="session-1", current_turn_id="run-1"),
            request,
        )
        is None
    )

    missing_run = ApprovalInteractionService(store=store)
    assert (
        await missing_run.create(
            FakeSession(id="session-1", current_turn_id=None),
            request,
        )
        is None
    )
    await missing_run.resolve(
        FakeSession(id="session-1", current_turn_id=None),
        "request-1",
        ApprovalResponse(session_id="session-1", request_id="request-1"),
    )

    assert store.interactions == []
