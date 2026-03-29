"""LocalChannel — in-process async channel for agent communication."""

from __future__ import annotations

import inspect
from collections import deque
from typing import Any, Callable


class LocalChannel:
    """In-process FIFO channel with pub/sub support.

    Messages are stored in a deque and delivered to subscribers on send().
    receive() pops from the queue (non-blocking, returns None if empty).
    """

    def __init__(self, maxlen: int | None = None) -> None:
        self._queue: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: list[Callable[..., Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message: enqueue and notify subscribers."""
        self._queue.append(message)
        for sub in list(self._subscribers):
            result = sub(message)
            if inspect.isawaitable(result):
                await result

    async def receive(self) -> dict[str, Any] | None:
        """Receive next message from queue, or None if empty."""
        if self._queue:
            return self._queue.popleft()
        return None

    def subscribe(self, callback: Callable[..., Any]) -> None:
        """Register a message callback."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[..., Any]) -> None:
        """Remove a message callback."""
        self._subscribers.remove(callback)
