"""Runtime-message consumer for product-owned approval decisions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from agentkit.runtime import RuntimeMessageBus, RuntimeMessageCursor, RuntimeMessageKind

from coding_agent.approval.coordinator import ApprovalCoordinator
from coding_agent.wire.protocol import ApprovalResponse

logger = logging.getLogger(__name__)

_APPROVAL_SCOPES = {"once", "session", "always"}


@dataclass(frozen=True, slots=True)
class ApprovalDecisionConsumptionResult:
    """Result of applying approval_decision messages to an approval store."""

    cursor: RuntimeMessageCursor
    applied_request_ids: tuple[str, ...]
    skipped_message_ids: tuple[str, ...]


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

        for item in batch.messages:
            response = self._response_from_payload(
                message_id=item.message.message_id,
                payload=item.message.payload,
            )
            if response is None:
                skipped_message_ids.append(item.message.message_id)
                continue

            if self._coordinator.respond(response):
                applied_request_ids.append(response.request_id)
            else:
                skipped_message_ids.append(item.message.message_id)
                logger.warning(
                    "approval_decision for unknown request %r in session %r",
                    response.request_id,
                    self._session_id,
                )

        return ApprovalDecisionConsumptionResult(
            cursor=batch.cursor,
            applied_request_ids=tuple(applied_request_ids),
            skipped_message_ids=tuple(skipped_message_ids),
        )

    def _response_from_payload(
        self,
        *,
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

        session_id = typed_payload.get("session_id")
        if session_id is not None and session_id != self._session_id:
            logger.warning(
                "approval_decision %r targets session %r but consumer owns %r",
                message_id,
                session_id,
                self._session_id,
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
        if scope_value not in _APPROVAL_SCOPES:
            logger.warning("approval_decision %r has invalid scope", message_id)
            return None

        return ApprovalResponse(
            session_id=self._session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
            scope=cast(Literal["once", "session", "always"], scope_value),
        )
