"""SessionManager for managing agent sessions.

NOTE: This module previously used AgentLoop which has been removed.
The create_session/run_agent methods now raise NotImplementedError.
The HTTP serve command needs to be migrated to Pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from coding_agent.adapter import PipelineAdapter
from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.providers.base import ChatProvider, ToolSchema
from agentkit.providers.models import DoneEvent, TextEvent
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
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
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        response_text = (
            "I'll help you with that request. Let me analyze the task... Done!"
        )

        for word in response_text.split():
            yield TextEvent(text=word + " ")
            await asyncio.sleep(0.01)

        yield DoneEvent()

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        """Return complete mock response."""
        return "Mock response"


@dataclass
class Session:
    """A managed agent session."""

    id: str
    created_at: datetime
    last_activity: datetime
    wire: LocalWire = field(init=False)
    approval_store: ApprovalStore = field(default_factory=ApprovalStore)
    repo_path: Path | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    max_steps: int = 30
    task: asyncio.Task[Any] | None = None
    turn_in_progress: bool = False
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.wire = LocalWire(self.id)


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

        approval_store = ApprovalStore()
        self._approval_stores[session_id] = approval_store

        session = Session(
            id=session_id,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            repo_path=repo_path,
            approval_policy=approval_policy,
            provider=provider,
            max_steps=max_steps,
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

    def register_session(self, session: Session) -> None:
        self._sessions[session.id] = session
        self._approval_stores[session.id] = session.approval_store

    def remove_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"Session not found: {session_id}")
        del self._sessions[session_id]
        self._approval_stores.pop(session_id, None)

    def clear_sessions(self) -> None:
        self._sessions.clear()
        self._approval_stores.clear()

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

            self.remove_session(session_id)

        logger.info(f"Closed session: {session_id}")

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
    ) -> None:
        session = self.get_session(session_id)
        session.last_activity = datetime.now()

        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }

        from coding_agent.__main__ import create_agent

        pipeline, ctx = create_agent(
            workspace_root=session.repo_path,
            max_steps_override=session.max_steps,
            approval_mode_override=approval_mode_map[session.approval_policy],
            session_id_override=session_id,
            api_key="http-session",
        )

        llm_plugin = pipeline._registry.get("llm_provider")
        llm_plugin._instance = session.provider or MockProvider()

        class _WireConsumer:
            def __init__(self, wire: LocalWire) -> None:
                self._wire = wire

            async def emit(self, msg: WireMessage) -> None:
                await self._wire.send(msg)

            async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
                session.pending_approval = {
                    "request_id": req.request_id,
                    "tool_name": req.tool_call.tool_name if req.tool_call else "",
                    "arguments": req.tool_call.arguments if req.tool_call else {},
                }
                session.approval_event.clear()
                session.approval_response = None
                session.approval_store.add_request(req)
                await self._wire.send(req)
                response = await session.approval_store.wait_for_response(
                    req.request_id,
                    req.timeout_seconds,
                )
                if response is None:
                    return ApprovalResponse(
                        session_id=req.session_id,
                        request_id=req.request_id,
                        approved=False,
                        feedback="Approval timeout or error",
                    )

                session.approval_response = {
                    "decision": "approve" if response.approved else "deny",
                    "feedback": response.feedback,
                }
                session.approval_event.set()
                return response

        consumer = _WireConsumer(session.wire)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        try:
            session.turn_in_progress = True
            await adapter.run_turn(prompt)
        except Exception as exc:
            logger.exception("HTTP session turn failed")
            await session.wire.send(
                StreamDelta(
                    session_id=session_id,
                    content=f"Error: {exc}",
                )
            )
            await session.wire.send(
                TurnEnd(
                    session_id=session_id,
                    turn_id=uuid.uuid4().hex,
                    completion_status=CompletionStatus.ERROR,
                )
            )
        finally:
            session.turn_in_progress = False
            session.last_activity = datetime.now()

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

        if not success and session.pending_approval is not None:
            success = session.pending_approval.get("request_id") == request_id

        if success:
            session.pending_approval = None
            session.approval_response = {
                "decision": "approve" if approved else "deny",
                "feedback": feedback,
            }
            session.approval_event.set()
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
            "turn_in_progress": session.turn_in_progress,
            "pending_approval": session.pending_approval is not None,
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
