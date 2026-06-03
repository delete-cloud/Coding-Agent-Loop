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
