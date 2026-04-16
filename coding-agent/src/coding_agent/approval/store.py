"""Request storage for approval system.

Provides in-memory storage for pending approval requests with async waiting.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse

logger = logging.getLogger(__name__)


@dataclass
class PendingRequest:
    """Internal representation of pending approval.

    Attributes:
        request: The approval request
        created_at: When the request was created
        response_event: Event set when response is received
        response: The response once received (None until then)
    """

    request: ApprovalRequest
    created_at: datetime = field(default_factory=datetime.now)
    response_event: asyncio.Event = field(default_factory=asyncio.Event)
    response: ApprovalResponse | None = None


class ApprovalStore:
    """In-memory store for pending approval requests.

    Thread-safe for concurrent access. Supports async waiting for responses.
    """

    def __init__(self):
        """Initialize empty store."""
        self._pending: dict[str, PendingRequest] = {}

    def add_request(self, request: ApprovalRequest) -> None:
        """Add new approval request.

        Args:
            request: The approval request to store
        """
        self._pending[request.request_id] = PendingRequest(request=request)
        logger.debug(f"Added approval request {request.request_id}")

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get pending request by ID.

        Args:
            request_id: The request ID to look up

        Returns:
            The ApprovalRequest if found, None otherwise
        """
        if request_id in self._pending:
            return self._pending[request_id].request
        return None

    def remove_request(self, request_id: str) -> None:
        _ = self._pending.pop(request_id, None)

    def respond(self, response: ApprovalResponse) -> bool:
        """Record response to a pending request.

        Args:
            response: The approval response

        Returns:
            True if the request was found and response recorded, False otherwise
        """
        if response.request_id not in self._pending:
            logger.warning(f"Response for unknown request: {response.request_id}")
            return False

        pending = self._pending[response.request_id]
        pending.response = response
        pending.response_event.set()
        logger.debug(f"Recorded response for request {response.request_id}")
        return True

    async def wait_for_response(
        self, request_id: str, timeout: float
    ) -> ApprovalResponse | None:
        """Wait for response with timeout.

        Args:
            request_id: The request ID to wait for
            timeout: Maximum seconds to wait

        Returns:
            The ApprovalResponse if received, None on timeout or if request not found
        """
        if request_id not in self._pending:
            logger.warning(f"Wait for unknown request: {request_id}")
            return None

        pending = self._pending[request_id]

        # If already responded, return immediately
        if pending.response is not None:
            try:
                return pending.response
            finally:
                self.remove_request(request_id)

        try:
            await asyncio.wait_for(pending.response_event.wait(), timeout=timeout)
            return pending.response
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.debug(f"Timeout waiting for response to {request_id}")
            return None
        finally:
            self.remove_request(request_id)
