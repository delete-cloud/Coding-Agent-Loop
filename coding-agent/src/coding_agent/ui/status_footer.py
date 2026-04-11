from __future__ import annotations

from typing import Any


class StatusFooter:
    def __init__(self, console: Any) -> None:
        self._console = console
        self._enabled = False
        self._mode = "spike-pending"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def mode(self) -> str:
        return self._mode

    def run_spike_check(self) -> str:
        if not getattr(self._console, "is_terminal", False):
            self._mode = "fallback-toolbar"
            return self._mode

        self._mode = "persistent"
        return self._mode

    def enable(self) -> None:
        self._enabled = True
        self._mode = "persistent"

    def disable(self) -> None:
        self._enabled = False

    def update(self, **kwargs: Any) -> None:
        return None

    def clear_and_redraw(self) -> None:
        return None
