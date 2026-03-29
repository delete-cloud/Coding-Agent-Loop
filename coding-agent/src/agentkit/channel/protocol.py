"""Channel Protocol — bidirectional communication between agent and consumer."""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    """Protocol for agent <-> consumer communication."""

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message to the channel."""
        ...

    async def receive(self) -> dict[str, Any] | None:
        """Receive the next message, or None if empty."""
        ...

    def subscribe(self, callback: Callable[..., Any]) -> None:
        """Register a callback for incoming messages."""
        ...

    def unsubscribe(self, callback: Callable[..., Any]) -> None:
        """Remove a callback."""
        ...
