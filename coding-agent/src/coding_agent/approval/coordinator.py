"""Backend approval coordination for concurrent requests."""

from __future__ import annotations

from collections import deque

from coding_agent.approval.store import ApprovalStore
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse


class ApprovalCoordinator:
    """Coordinates approval lifecycle while keeping the store as truth source."""

    def __init__(self, store: ApprovalStore | None = None) -> None:
        self._store: ApprovalStore = store or ApprovalStore()
        self._request_order: deque[str] = deque()
        self._session_approved_tools: set[tuple[str, str]] = set()

    @property
    def store(self) -> ApprovalStore:
        return self._store

    def add_request(self, request: ApprovalRequest) -> None:
        self._drop_request_id(request.request_id)
        self._store.add_request(request)
        self._request_order.append(request.request_id)

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        return self._store.get_request(request_id)

    @property
    def pending_request_id(self) -> str | None:
        pending = self.pending_request
        if pending is None:
            return None
        return pending.request_id

    @property
    def pending_request(self) -> ApprovalRequest | None:
        while self._request_order:
            request_id = self._request_order[0]
            request = self._store.get_request(request_id)
            if request is not None:
                return request
            self._request_order.popleft()
        return None

    def projection(self) -> dict[str, object] | None:
        request = self.pending_request
        if request is None:
            return None
        tool_call = request.tool_call
        if tool_call is None:
            raise ValueError("approval request is missing tool_call")
        return {
            "request_id": request.request_id,
            "tool_name": tool_call.tool_name,
            "arguments": tool_call.arguments,
        }

    def respond(self, response: ApprovalResponse) -> bool:
        request = self.get_request(response.request_id)
        if request is None:
            return False

        recorded = self._store.respond(response)
        if (
            recorded
            and response.approved
            and response.scope
            in {
                "session",
                "always",
            }
        ):
            self.remember_session_approval(request)
        if recorded:
            self._drop_request_id(response.request_id)
        return recorded

    def is_session_approved(self, request: ApprovalRequest) -> bool:
        return (request.agent_id, request.tool) in self._session_approved_tools

    def remember_session_approval(self, request: ApprovalRequest) -> None:
        self._session_approved_tools.add((request.agent_id, request.tool))

    async def wait_for_response(
        self, request_id: str, timeout: float
    ) -> ApprovalResponse | None:
        try:
            return await self._store.wait_for_response(request_id, timeout=timeout)
        finally:
            self._drop_request_id(request_id)

    def _drop_request_id(self, request_id: str) -> None:
        self._request_order = deque(
            queued_id for queued_id in self._request_order if queued_id != request_id
        )
