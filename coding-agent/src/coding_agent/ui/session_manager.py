"""SessionManager for managing agent sessions.

NOTE: This module previously used AgentLoop which has been removed.
The create_session/run_agent methods now raise NotImplementedError.
The HTTP serve command needs to be migrated to Pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.providers.base import ChatProvider, StreamingResponse, ToolSchema
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)

logger = logging.getLogger(__name__)


class MockProvider:
    """Mock provider for testing that simulates LLM responses."""

    def __init__(self):
        self._max_context_size = 8192
        self._model_name = "mock"

    @property
    def max_context_size(self) -> int:
        """Maximum context size in tokens."""
        return self._max_context_size

    @property
    def model_name(self) -> str:
        """Name of the model being used."""
        return self._model_name

    async def stream(
        self,
        messages: list[dict],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream mock response."""
        from coding_agent.providers.base import StreamEvent

        # Simple mock response
        response_text = (
            "I'll help you with that request. Let me analyze the task... Done!"
        )

        for word in response_text.split():
            yield StreamEvent(type="delta", text=word + " ", tool_call=None, error=None)
            await asyncio.sleep(0.01)  # Small delay for streaming effect

        yield StreamEvent(type="done", text=None, tool_call=None, error=None)

    async def complete(self, messages: list[dict]) -> str:
        """Return complete mock response."""
        return "Mock response"


@dataclass
class Session:
    """A managed agent session."""

    id: str
    wire: LocalWire
    approval_store: ApprovalStore
    created_at: datetime
    last_activity: datetime
    task: asyncio.Task | None = None


class SessionManager:
    """Manages agent sessions with lifecycle and resource management."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        repo_path: Path | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider: Any | None = None,
        max_steps: int = 30,
        enable_parallel: bool = True,
        max_parallel: int = 5,
    ) -> str:
        """Create a new agent session.

        Args:
            repo_path: Path to the repository root (default: current directory)
            approval_policy: Policy for tool execution approval
            provider: LLM provider (if None, uses mock/test provider)
            max_steps: Maximum steps per turn
            enable_parallel: Enable parallel tool execution
            max_parallel: Maximum number of parallel tool executions

        Returns:
            The session ID
        """
        session_id = str(uuid.uuid4())
        now = datetime.now()

        # Use mock provider if none provided
        if provider is None:
            provider = MockProvider()

        # Create LocalWire for this session
        wire = LocalWire(session_id)

        # Create ApprovalStore for this session
        approval_store = ApprovalStore()
        self._approval_stores[session_id] = approval_store

        session = Session(
            id=session_id,
            wire=wire,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            task=None,
        )

        async with self._lock:
            self._sessions[session_id] = session

        logger.info(f"Created session: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            The Session object

        Raises:
            KeyError: If session not found
        """
        if session_id not in self._sessions:
            raise KeyError(f"Session not found: {session_id}")
        return self._sessions[session_id]

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists.

        Args:
            session_id: The session ID

        Returns:
            True if session exists, False otherwise
        """
        return session_id in self._sessions

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Args:
            session_id: The session ID to close

        Raises:
            KeyError: If session not found
        """
        async with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session not found: {session_id}")

            session = self._sessions[session_id]

            # Cancel any running task
            if session.task and not session.task.done():
                session.task.cancel()
                try:
                    await asyncio.wait_for(session.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            # Remove from store
            del self._sessions[session_id]

            # Cleanup approval store
            self._approval_stores.pop(session_id, None)

        logger.info(f"Closed session: {session_id}")

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
    ) -> None:
        """Run the agent with the given prompt.

        NOTE: Requires Pipeline migration. Currently raises NotImplementedError.
        """
        raise NotImplementedError(
            "SessionManager.run_agent() requires Pipeline migration. "
            "The old AgentLoop has been removed."
        )

    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> bool:
        """Submit an approval response for a pending request.

        Uses the session's ApprovalStore to record the response.

        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message

        Returns:
            True if the response was recorded successfully, False otherwise

        Raises:
            KeyError: If session not found
        """
        session = self.get_session(session_id)

        # Create approval response and submit to ApprovalStore
        response = ApprovalResponse(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
        )
        success = session.approval_store.respond(response)
        session.last_activity = datetime.now()

        if success:
            logger.info(f"Approval submitted for session {session_id}: {approved}")
        else:
            logger.warning(
                f"Approval submission failed for session {session_id}: request {request_id} not found"
            )

        return success

    def list_sessions(self) -> list[str]:
        """List all active session IDs.

        Returns:
            List of session IDs
        """
        return list(self._sessions.keys())

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session information.

        Args:
            session_id: The session ID

        Returns:
            Dictionary with session info

        Raises:
            KeyError: If session not found
        """
        session = self.get_session(session_id)
        return {
            "id": session.id,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
            "turn_in_progress": session.task is not None and not session.task.done(),
        }

    async def cleanup_idle_sessions(self, max_idle_minutes: int = 30) -> list[str]:
        """Clean up sessions that have been idle for too long.

        Args:
            max_idle_minutes: Maximum idle time in minutes

        Returns:
            List of closed session IDs
        """
        now = datetime.now()
        closed = []

        async with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            try:
                session = self.get_session(session_id)
                idle_time = now - session.last_activity
                if idle_time.total_seconds() > max_idle_minutes * 60:
                    await self.close_session(session_id)
                    closed.append(session_id)
            except KeyError:
                # Session already closed
                pass

        if closed:
            logger.info(f"Cleaned up {len(closed)} idle sessions: {closed}")

        return closed
