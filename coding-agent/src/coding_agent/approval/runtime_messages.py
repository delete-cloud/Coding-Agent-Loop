"""Runtime-message consumer for product-owned approval decisions."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from typing import Protocol

from agentkit.runtime import (
    DuplicateRuntimeMessageError,
    RuntimeMessage,
    RuntimeMessageBus,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.approval.interactions import ApprovalInteractionService
from coding_agent.wire.protocol import ApprovalResponse

logger = logging.getLogger(__name__)

_APPROVAL_SCOPES = {"once", "session", "always"}


def approval_decision_message_id(session_id: str, request_id: str) -> str:
    return f"approval_decision:{session_id}:{request_id}"


def approval_response_projection(response: ApprovalResponse) -> dict[str, Any]:
    return {
        "request_id": response.request_id,
        "decision": "approve" if response.approved else "deny",
        "feedback": response.feedback,
    }


@dataclass(frozen=True, slots=True)
class ApprovalDecisionConsumptionResult:
    """Result of applying approval_decision messages to an approval store."""

    cursor: RuntimeMessageCursor
    applied_request_ids: tuple[str, ...]
    skipped_message_ids: tuple[str, ...]
    deferred_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishedApprovalDecision:
    sequence: int
    response: ApprovalResponse


class ApprovalDecisionSession(Protocol):
    id: str
    approval_coordinator: ApprovalCoordinator
    runtime_message_bus: RuntimeMessageBus
    approval_decision_cursor: RuntimeMessageCursor
    last_activity: datetime

    def update_pending_approval_projection(
        self,
        *,
        signal_event: bool = False,
    ) -> None: ...

    def expose_approval_response(self, response_projection: dict[str, Any]) -> None: ...


PersistApprovalDecisionSession = Callable[
    [ApprovalDecisionSession],
    Awaitable[None],
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalDecisionConsumer:
    """Consumes approval_decision messages for one product session."""

    def __init__(self, *, session_id: str, coordinator: ApprovalCoordinator) -> None:
        self._session_id = session_id
        self._coordinator = coordinator

    async def consume(
        self,
        bus: RuntimeMessageBus,
        cursor: RuntimeMessageCursor,
        *,
        limit: int | None = None,
    ) -> ApprovalDecisionConsumptionResult:
        batch = await bus.consume_after(
            cursor,
            kinds={RuntimeMessageKind.APPROVAL_DECISION},
            limit=limit,
        )
        applied_request_ids: list[str] = []
        skipped_message_ids: list[str] = []
        deferred_message_ids: list[str] = []
        cursor_sequence = cursor.sequence

        for item in batch.messages:
            response = self._response_from_payload(
                message_id=item.message.message_id,
                payload=item.message.payload,
            )
            if response is None:
                skipped_message_ids.append(item.message.message_id)
                cursor_sequence = item.sequence
                continue

            if self._coordinator.get_request(response.request_id) is None:
                deferred_message_ids.append(item.message.message_id)
                cursor_sequence = item.sequence
                logger.warning(
                    "approval_decision for unknown request %r in session %r",
                    response.request_id,
                    self._session_id,
                )
                continue

            if self._coordinator.respond(response):
                applied_request_ids.append(response.request_id)
                cursor_sequence = item.sequence
            else:
                skipped_message_ids.append(item.message.message_id)
                cursor_sequence = item.sequence
                logger.warning(
                    "approval_decision for duplicate request %r in session %r",
                    response.request_id,
                    self._session_id,
                )

        return ApprovalDecisionConsumptionResult(
            cursor=RuntimeMessageCursor(cursor_sequence),
            applied_request_ids=tuple(applied_request_ids),
            skipped_message_ids=tuple(skipped_message_ids),
            deferred_message_ids=tuple(deferred_message_ids),
        )

    def _response_from_payload(
        self,
        *,
        message_id: str,
        payload: object,
    ) -> ApprovalResponse | None:
        return approval_response_from_runtime_payload(
            session_id=self._session_id,
            message_id=message_id,
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class ApprovalDecisionService:
    interactions: ApprovalInteractionService
    persist_session: PersistApprovalDecisionSession
    now: Callable[[], datetime] = _utcnow

    async def consume_for_session(
        self,
        session: ApprovalDecisionSession,
        *,
        limit: int | None = None,
    ) -> ApprovalDecisionConsumptionResult:
        consumer = ApprovalDecisionConsumer(
            session_id=session.id,
            coordinator=session.approval_coordinator,
        )
        result = await consumer.consume(
            session.runtime_message_bus,
            session.approval_decision_cursor,
            limit=limit,
        )
        if result.applied_request_ids or not result.deferred_message_ids:
            session.approval_decision_cursor = result.cursor
        if result.applied_request_ids:
            session.update_pending_approval_projection(signal_event=True)
        return result

    async def published_decision(
        self,
        session: ApprovalDecisionSession,
        request_id: str,
    ) -> PublishedApprovalDecision | None:
        message_id = approval_decision_message_id(session.id, request_id)
        batch = await session.runtime_message_bus.consume_after(
            RuntimeMessageCursor(),
            kinds={RuntimeMessageKind.APPROVAL_DECISION},
        )
        for item in batch.messages:
            if item.message.message_id != message_id:
                continue
            response = approval_response_from_runtime_payload(
                session_id=session.id,
                message_id=item.message.message_id,
                payload=item.message.payload,
            )
            if response is None:
                return None
            return PublishedApprovalDecision(
                sequence=item.sequence,
                response=response,
            )
        return None

    async def apply_published_decision(
        self,
        session: ApprovalDecisionSession,
        request_id: str,
        decision: PublishedApprovalDecision,
    ) -> ApprovalResponse | None:
        already_consumed = (
            decision.sequence <= session.approval_decision_cursor.sequence
        )
        applied = False
        if session.approval_coordinator.get_request(request_id) is not None:
            applied = session.approval_coordinator.respond(decision.response)
            if applied and not already_consumed:
                session.approval_decision_cursor = RuntimeMessageCursor(
                    max(
                        session.approval_decision_cursor.sequence,
                        decision.sequence,
                    )
                )
        if not applied and not already_consumed:
            return None

        session.last_activity = self.now()
        session.expose_approval_response(
            approval_response_projection(decision.response)
        )
        await self.persist_session(session)
        await self.interactions.resolve(
            session,
            request_id,
            decision.response,
        )
        if not applied:
            logger.info(
                "approval_decision for session %s request %s was already published; keeping the first decision",
                session.id,
                request_id,
            )
        return decision.response

    async def submit(
        self,
        session: ApprovalDecisionSession,
        request_id: str,
        *,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> ApprovalResponse | None:
        message_id = approval_decision_message_id(session.id, request_id)

        published_decision = await self.published_decision(
            session,
            request_id,
        )
        if published_decision is not None:
            return await self.apply_published_decision(
                session,
                request_id,
                published_decision,
            )

        if session.approval_coordinator.get_request(request_id) is None:
            logger.warning(
                "Approval submission failed for session %s: request %s not found",
                session.id,
                request_id,
            )
            return None

        try:
            await session.runtime_message_bus.publish(
                RuntimeMessage(
                    message_id=message_id,
                    kind=RuntimeMessageKind.APPROVAL_DECISION,
                    payload={
                        "session_id": session.id,
                        "request_id": request_id,
                        "approved": approved,
                        "feedback": feedback,
                        "scope": scope,
                    },
                )
            )
        except DuplicateRuntimeMessageError as exc:
            if exc.message_id != message_id:
                raise
            published_decision = await self.published_decision(
                session,
                request_id,
            )
            if published_decision is None:
                raise RuntimeError(
                    f"duplicate approval_decision {message_id!r} was not readable"
                ) from exc
            logger.info(
                "approval_decision already published for session %s request %s",
                session.id,
                request_id,
            )

        if published_decision is None:
            published_decision = await self.published_decision(
                session,
                request_id,
            )
        if published_decision is None:
            raise RuntimeError(f"approval_decision {message_id!r} was not readable")

        result = await self.consume_for_session(session)
        success = request_id in result.applied_request_ids
        session.last_activity = self.now()

        if success:
            session.expose_approval_response(
                approval_response_projection(published_decision.response)
            )
            await self.persist_session(session)
            await self.interactions.resolve(
                session,
                request_id,
                published_decision.response,
            )
            logger.info(
                "Approval submitted for session %s: %s",
                session.id,
                published_decision.response.approved,
            )
        else:
            logger.warning(
                "approval_decision for session %s request %s was not applied (validation failure or race)",
                session.id,
                request_id,
            )
            if result.applied_request_ids:
                await self.persist_session(session)
            return None

        return published_decision.response


def approval_response_from_runtime_payload(
    *,
    session_id: str,
    message_id: str,
    payload: object,
) -> ApprovalResponse | None:
    if not isinstance(payload, Mapping):
        logger.warning(
            "approval_decision %r has non-object payload",
            message_id,
        )
        return None

    typed_payload = cast(Mapping[str, Any], payload)

    payload_session_id = typed_payload.get("session_id")
    if payload_session_id is not None and payload_session_id != session_id:
        logger.warning(
            "approval_decision %r targets session %r but consumer owns %r",
            message_id,
            payload_session_id,
            session_id,
        )
        return None

    request_id = typed_payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        logger.warning("approval_decision %r is missing request_id", message_id)
        return None

    approved = typed_payload.get("approved")
    if not isinstance(approved, bool):
        logger.warning("approval_decision %r has invalid approved", message_id)
        return None

    feedback = typed_payload.get("feedback")
    if feedback is not None and not isinstance(feedback, str):
        logger.warning("approval_decision %r has invalid feedback", message_id)
        return None

    scope_value = typed_payload.get("scope", "once")
    if not isinstance(scope_value, str) or scope_value not in _APPROVAL_SCOPES:
        logger.warning("approval_decision %r has invalid scope", message_id)
        return None

    return ApprovalResponse(
        session_id=session_id,
        request_id=request_id,
        approved=approved,
        feedback=feedback,
        scope=cast(Literal["once", "session", "always"], scope_value),
    )
