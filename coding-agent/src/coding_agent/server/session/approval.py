"""HTTP approval wait/submit and session approval consumer."""

from __future__ import annotations

import logging
from typing import Literal
from agentkit.runtime.contracts import RuntimeCommand
from coding_agent.approval import (
    ApprovalInteractionService,
    ApprovalDecisionService,
    ApprovalRequestService,
)
from coding_agent.runs import RuntimeWireEventRecorder
from coding_agent.runs.serving_runtime import (
    serving_approval_identity,
    session_serving_turn_kind,
)
from coding_agent.wire.consumer import LocalWireConsumer
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
)
from coding_agent.server.session.models import Session

logger = logging.getLogger("coding_agent.server.session_manager")


class ApprovalOps:
    def _approval_interactions(self) -> ApprovalInteractionService:
        return ApprovalInteractionService(
            store=self._runtime_store,
            owner_id=self._owner_id,
            fencing_token=self._fencing_token,
        )

    def _approval_decisions(self) -> ApprovalDecisionService:
        return ApprovalDecisionService(
            interactions=self._approval_interactions(),
            persist_session=self._persist_approval_decided,
        )

    def _approval_requests(self) -> ApprovalRequestService:
        interactions = self._approval_interactions()
        return ApprovalRequestService(
            interactions=interactions,
            decisions=ApprovalDecisionService(
                interactions=interactions,
                persist_session=self._persist_approval_decided,
            ),
            persist_session=self._persist_approval_requested,
        )

    def _make_session_consumer(self, session: Session) -> LocalWireConsumer:
        approval_requests = self._approval_requests()

        async def _request_approval(req: ApprovalRequest) -> ApprovalResponse:
            response = await approval_requests.resolve_session_approval(session, req)
            if response is not None:
                return response
            response = await approval_requests.begin_request(session, req)
            if response is not None:
                return response
            await self._send_session_wire_message(session, req)
            try:
                response = await session.approval_coordinator.wait_for_response(
                    req.request_id,
                    float(req.timeout_seconds),
                )
                if response is None:
                    return await approval_requests.resolve_timeout(session, req)

                await approval_requests.resolve_wait_response(
                    session,
                    req.request_id,
                    response,
                    expose_response=True,
                )
                return response
            finally:
                await approval_requests.cleanup_after_wait(
                    session,
                    signal_event=False,
                )

        return LocalWireConsumer(
            session.wire,
            _request_approval,
            emit_handler=lambda message: self._send_session_wire_message(
                session,
                message,
            ),
        )

    def has_approval_request(self, session_id: str) -> bool:
        return (
            self.get_session(session_id).approval_coordinator.pending_request
            is not None
        )

    def matches_approval_request(self, session_id: str, request_id: str) -> bool:
        session = self.get_session(session_id)
        return session.approval_coordinator.get_request(request_id) is not None

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> ApprovalResponse | None:
        """Submit an approval response for a pending request.

        New-runtime decisions enter the durable mailbox before acknowledgement;
        legacy decisions continue through ApprovalStore.

        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message

        Returns:
            The stored/applied approval response, or None if no matching request exists

        Raises:
            KeyError: If session not found
        """
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        if session_serving_turn_kind(session) == "durable_segment_runner":
            request = session.approval_coordinator.get_request(request_id)
            if request is None:
                logger.warning(
                    "Approval submission failed for session %s: request %s not found",
                    session.id,
                    request_id,
                )
                return None
            run_id = session.current_turn_id
            if run_id is None:
                raise RuntimeError("new-runtime approval requires an active root run")
            store = self._authoritative_store()
            if store is None:
                raise RuntimeError("new-runtime approval requires a durable store")
            authority = self._owner_authorities.get(session.id)
            if authority is None:
                raise RuntimeError("new-runtime approval requires owner authority")
            command_id, input_id = serving_approval_identity(
                run_id=run_id,
                request_id=request_id,
            )
            response = ApprovalResponse(
                session_id=session.id,
                request_id=request_id,
                approved=approved,
                feedback=feedback,
                scope=scope,
            )
            await store.admit_new_runtime_command(
                authority,
                RuntimeCommand(
                    command_id=command_id,
                    command_kind="approval_decision",
                    payload={
                        "approved": approved,
                        "request_id": input_id,
                        "target_run_id": run_id,
                    },
                ),
            )
            if not session.approval_coordinator.respond(response):
                return None
            return response
        response = await self._approval_decisions().submit(
            session,
            request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        if response is not None:
            await RuntimeWireEventRecorder(
                self._runtime_store,
                new_event_id=lambda run_id: (
                    f"{run_id}:wire:approval-response:{request_id}"
                ),
            ).append_wire_event(session, response)
        return response

    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> bool:
        response = await self.submit_approval_response(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        return response is not None

    async def wait_for_http_approval(
        self,
        session_id: str,
        approval_req: ApprovalRequest,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        await self._assert_owner(session_id)
        if not await self.has_session_async(session_id):
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Session not found",
            )

        session = await self.get_session_async(session_id)
        approval_requests = self._approval_requests()
        if not session.turn_in_progress:
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Approval timeout or error",
            )

        response = await approval_requests.resolve_session_approval(
            session,
            approval_req,
        )
        if response is not None:
            return response

        response = await approval_requests.begin_request(
            session,
            approval_req,
        )
        if response is not None:
            return response

        try:
            response = await session.approval_coordinator.wait_for_response(
                approval_req.request_id,
                float(timeout_seconds),
            )
            if response is not None:
                await approval_requests.resolve_wait_response(
                    session,
                    approval_req.request_id,
                    response,
                    expose_response=False,
                )
                return response
        finally:
            await approval_requests.cleanup_after_wait(
                session,
                signal_event=True,
            )

        return await approval_requests.resolve_timeout(
            session,
            approval_req,
        )
