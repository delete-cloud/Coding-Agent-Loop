"""Plugin Protocol — the contract every plugin must satisfy."""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Protocol that all agentkit plugins must satisfy.

    Attributes:
        state_key: Unique identifier for this plugin's state namespace.
    """

    state_key: str

    def hooks(self) -> dict[str, Callable[..., Any]]:
        """Return a mapping of hook_name → callable for this plugin."""
        ...
