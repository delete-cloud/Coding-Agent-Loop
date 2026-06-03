from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.approval.interactions import (
    ApprovalInteractionService,
    ApprovalInteractionSession,
)
from coding_agent.approval.runtime_messages import (
    ApprovalDecisionService,
    ApprovalDecisionSession,
)
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse


class ApprovalRequestSession(
    ApprovalDecisionSession,
    ApprovalInteractionSession,
    Protocol,
):
    id: str
    current_turn_id: str | None
    approval_coordinator: ApprovalCoordinator
    pending_approval: dict[str, Any] | None
    approval_event: asyncio.Event
    approval_response: dict[str, Any] | None
    last_activity: datetime


PersistApprovalRequestSession = Callable[
    [ApprovalRequestSession],
    Awaitable[None],
]


def approval_wait_response_projection(response: ApprovalResponse) -> dict[str, Any]:
    return {
        "decision": "approve" if response.approved else "deny",
        "feedback": response.feedback,
    }


@dataclass(frozen=True, slots=True)
class ApprovalRequestService:
    interactions: ApprovalInteractionService
    decisions: ApprovalDecisionService
    persist_session: PersistApprovalRequestSession

    async def resolve_session_approval(
        self,
        session: ApprovalRequestSession,
        request: ApprovalRequest,
    ) -> ApprovalResponse | None:
        if not session.approval_coordinator.is_session_approved(request):
            return None
        response = ApprovalResponse(
            session_id=request.session_id,
            request_id=request.request_id,
            approved=True,
            scope="session",
        )
        await self.interactions.create(session, request)
        await self.interactions.resolve(
            session,
            request.request_id,
            response,
        )
        return response

    async def begin_request(
        self,
        session: ApprovalRequestSession,
        request: ApprovalRequest,
    ) -> ApprovalResponse | None:
        session.approval_coordinator.add_request(request)
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_event.clear()
        session.approval_response = None
        await self.persist_session(session)
        await self.interactions.create(session, request)

        published_decision = await self.decisions.published_decision(
            session,
            request.request_id,
        )
        if published_decision is None:
            return None
        return await self.decisions.apply_published_decision(
            session,
            request.request_id,
            published_decision,
        )

    async def resolve_wait_response(
        self,
        session: ApprovalRequestSession,
        request_id: str,
        response: ApprovalResponse,
        *,
        expose_response: bool,
    ) -> None:
        if expose_response:
            session.approval_response = approval_wait_response_projection(response)
            session.approval_event.set()
            session.pending_approval = session.approval_coordinator.projection()
            await self.persist_session(session)
        await self.interactions.resolve(
            session,
            request_id,
            response,
        )

    async def resolve_timeout(
        self,
        session: ApprovalRequestSession,
        request: ApprovalRequest,
    ) -> ApprovalResponse:
        timeout_response = ApprovalResponse(
            session_id=request.session_id,
            request_id=request.request_id,
            approved=False,
            feedback="Approval timeout or error",
        )
        await self.interactions.resolve(
            session,
            request.request_id,
            timeout_response,
            status="timed_out",
        )
        return timeout_response

    async def cleanup_after_wait(
        self,
        session: ApprovalRequestSession,
        *,
        signal_event: bool,
    ) -> None:
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_response = None
        if signal_event:
            session.approval_event.set()
        await self.persist_session(session)


__all__ = [
    "ApprovalRequestService",
    "ApprovalRequestSession",
    "approval_wait_response_projection",
]
