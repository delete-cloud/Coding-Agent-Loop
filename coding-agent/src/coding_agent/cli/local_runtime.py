"""Local CLI runtime/session boundary.

This module is the CLI-facing seam for local interactive sessions. The first
slice intentionally delegates to the existing server SessionManager so behavior
stays unchanged while the REPL stops depending on control-plane symbols.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Any

from coding_agent.server.session_manager import SessionManager


@runtime_checkable
class LocalCliSessionManager(Protocol):
    """Subset of session behavior used by the interactive local REPL."""

    async def create_session(self, **kwargs: Any) -> str: ...

    def get_session(self, session_id: str) -> Any: ...

    async def get_session_async(self, session_id: str) -> Any: ...

    async def ensure_session_runtime(self, session_id: str) -> Any: ...

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
    ) -> Any: ...

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None: ...

    async def run_agent(self, session_id: str, prompt: str) -> None: ...


def create_local_cli_session_manager() -> LocalCliSessionManager:
    """Create the local REPL session manager.

    The concrete implementation remains the existing managed local session
    service for now. Keeping this construction here prevents REPL code from
    importing server/control-plane types directly.
    """

    return SessionManager(
        storage_config={
            "http_session_backend": "fs",
            "runtime_backend": "jsonl",
        }
    )


__all__ = ["LocalCliSessionManager", "create_local_cli_session_manager"]
