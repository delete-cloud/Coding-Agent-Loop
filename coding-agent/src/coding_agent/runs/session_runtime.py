from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessageBus,
    RuntimeMessageCursor,
)

from coding_agent.approval import ApprovalCoordinator
from coding_agent.wire.protocol import ApprovalRequest


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EventBroadcastResult:
    delivered_count: int
    full_pruned_count: int
    failed_pruned_count: int


@dataclass
class SessionRuntimeHandle:
    """Process-local runtime state associated with a session record."""

    approval_coordinator: ApprovalCoordinator
    task: asyncio.Task[Any] | None = None
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    runtime_pipeline: Any | None = None
    runtime_ctx: Any | None = None
    runtime_adapter: Any | None = None
    runtime_message_bus: RuntimeMessageBus = field(
        default_factory=InMemoryRuntimeMessageBus
    )
    approval_decision_cursor: RuntimeMessageCursor = field(
        default_factory=RuntimeMessageCursor
    )

    def has_event_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> bool:
        return queue in self.event_queues

    def add_event_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.event_queues.append(queue)

    def remove_event_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> bool:
        if queue not in self.event_queues:
            return False
        self.event_queues.remove(queue)
        return True

    def clear_approval_runtime_state(self) -> None:
        self.pending_approval = None
        self.approval_response = None
        self.approval_event.clear()

    def begin_approval_request(self, request: ApprovalRequest) -> None:
        self.approval_coordinator.add_request(request)
        self.pending_approval = self.approval_coordinator.projection()
        self.approval_event.clear()
        self.approval_response = None

    def update_pending_approval_projection(
        self,
        *,
        signal_event: bool = False,
    ) -> None:
        self.pending_approval = self.approval_coordinator.projection()
        if signal_event:
            self.approval_event.set()

    def expose_approval_response(self, response_projection: dict[str, Any]) -> None:
        self.approval_response = response_projection
        self.pending_approval = self.approval_coordinator.projection()
        self.approval_event.set()

    def cleanup_approval_wait_projection(self, *, signal_event: bool) -> None:
        self.pending_approval = self.approval_coordinator.projection()
        self.approval_response = None
        if signal_event:
            self.approval_event.set()

    def broadcast_event_nowait(
        self,
        event: dict[str, Any],
    ) -> EventBroadcastResult:
        active_queues: list[asyncio.Queue[dict[str, Any]]] = []
        delivered_count = 0
        full_pruned_count = 0
        failed_pruned_count = 0

        for queue in self.event_queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                full_pruned_count += 1
            except Exception:
                logger.debug(
                    "Pruning event queue after broadcast failure",
                    exc_info=True,
                )
                failed_pruned_count += 1
            else:
                delivered_count += 1
                active_queues.append(queue)

        self.event_queues = active_queues
        return EventBroadcastResult(
            delivered_count=delivered_count,
            full_pruned_count=full_pruned_count,
            failed_pruned_count=failed_pruned_count,
        )

__all__ = ["EventBroadcastResult", "SessionRuntimeHandle"]
