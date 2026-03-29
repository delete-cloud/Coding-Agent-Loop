"""ShellSessionPlugin — persistent shell session management.

Tracks working directory and environment variables across tool calls,
enabling persistent shell sessions (Kapybara pattern).

The plugin:
  - mount: Initializes session with current directory
  - on_checkpoint: Logs session state for persistence
  - Exposes get_session_context() for shell tools to use
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ShellSessionPlugin:
    """Plugin for persistent shell session state."""

    state_key = "shell_session"

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "on_checkpoint": self.on_checkpoint,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialize shell session state."""
        self._state = {
            "cwd": os.getcwd(),
            "env_vars": {},
            "active": True,
        }
        return dict(self._state)

    def on_checkpoint(self, **kwargs: Any) -> None:
        """Observer: log session state for persistence."""
        logger.debug(
            "Shell session checkpoint: cwd=%s, env_count=%d",
            self._state.get("cwd", "?"),
            len(self._state.get("env_vars", {})),
        )

    def get_session_context(self) -> dict[str, Any]:
        """Get current session context for shell tools."""
        return dict(self._state)

    def update_cwd(self, new_cwd: str) -> None:
        """Update the tracked working directory."""
        self._state["cwd"] = new_cwd

    def update_env(self, key: str, value: str) -> None:
        """Track an environment variable change."""
        self._state.setdefault("env_vars", {})[key] = value
