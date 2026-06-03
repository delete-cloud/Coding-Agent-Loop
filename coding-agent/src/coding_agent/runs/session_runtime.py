from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessageBus,
    RuntimeMessageCursor,
)

from coding_agent.approval import ApprovalCoordinator


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


__all__ = ["SessionRuntimeHandle"]
